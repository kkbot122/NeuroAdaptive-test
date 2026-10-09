"""Durable learning activity and fixed assessment lifecycle."""
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.problem_details import ProblemDetailException
from app.modules.abuse.service import AbuseControlService
from app.services.ai_usage import ai_usage_scope, provider_attempt
from app.modules.adaptation.models import AdaptationDecision
from app.modules.adaptation.service import AdaptationNotFound, AdaptationService
from app.modules.courses.models import Course, CourseStatus
from app.modules.courses.service import CourseNotFound, CourseService
from app.modules.curriculum.models import Concept, CourseVersion, Lesson, LessonConcept, Module
from app.modules.documents.chunk_models import Chunk
from app.modules.documents.models import Document
from app.modules.learning.models import (
    ActivityStatus,
    AnswerStatus,
    AnswerSubmission,
    AssessmentQuestion,
    AssessmentSession,
    AssessmentStatus,
    AssessmentType,
    GradingCorrection,
    GradingIssueReport,
    GradingJudgment,
    GradingReviewEvent,
    LearningActivity,
)
from app.modules.learning.activity_types import (
    P2_TEACHING_ACTIVITY_TYPES,
    PREPARED_ACTIVITY_TYPES,
    activity_includes_teaching,
)
from app.modules.mastery.grading import (
    P5_GRADING_POLICY_VERSION,
    GradingError,
    grade_attempt,
    grade_short_text_criteria,
)
from app.modules.mastery.models import Question, QuestionAttempt, QuestionConcept
from app.modules.mastery.service import MasteryNotFound, MasteryService
from app.modules.preparation.models import (
    ActivityPreparation,
    PreparationStatus,
    PreparedActivityQuestion,
    QuestionSource,
)
from app.services.embedding.gateway import EmbeddingGateway
from app.services.generation.gateway import GenerationError, GenerationGateway

logger = logging.getLogger(__name__)
MAX_SESSION_QUESTIONS = 50
_LIFECYCLE_LOCKS = tuple(threading.Lock() for _ in range(64))
P2_SUPPORTED_ACTIVITY_TYPES = P2_TEACHING_ACTIVITY_TYPES


class GradingLeaseLost(Exception):
    """This delivery no longer owns the answer's grading lease."""


class GradingCallsExhausted(Exception):
    """The approved provider-call limit for this confirmed answer was reached."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _grading_call_limit(answer: AnswerSubmission) -> int:
    return answer.grading_call_limit or settings.P5_GRADING_MAX_PROVIDER_CALLS_V1


def _lock_current_grading_lease(db: Session, answer_id: UUID, owner_id: int, token: UUID) -> AnswerSubmission:
    row = (
        db.query(AnswerSubmission)
        .join(AssessmentQuestion, AssessmentQuestion.id == AnswerSubmission.assessment_question_id)
        .join(AssessmentSession, AssessmentSession.id == AssessmentQuestion.session_id)
        .join(LearningActivity, LearningActivity.id == AssessmentSession.activity_id)
        .filter(AnswerSubmission.id == answer_id, LearningActivity.owner_id == owner_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if (
        row is None
        or row.status != AnswerStatus.AWAITING_GRADING.value
        or row.grading_lease_token != token
        or row.grading_lease_expires_at is None
        or _as_utc(row.grading_lease_expires_at) <= _now()
    ):
        raise GradingLeaseLost()
    return row


class BudgetedGradingGateway(GenerationGateway):
    """Reserve allowance and the fixed answer call cap for each transport attempt."""

    def __init__(self, delegate: GenerationGateway, db: Session, answer_id: UUID, owner_id: int, token: UUID):
        self.delegate = delegate
        self.db = db
        self.answer_id = answer_id
        self.owner_id = owner_id
        self.token = token

    @property
    def model_name(self) -> str:
        return self.delegate.model_name

    def _before_attempt(self, db):
        row = _lock_current_grading_lease(db, self.answer_id, self.owner_id, self.token)
        if row.grading_attempt_count >= _grading_call_limit(row):
            raise GradingCallsExhausted()
        row.grading_attempt_count += 1
        row.grading_lease_expires_at = _now() + timedelta(seconds=settings.JOB_LEASE_SECONDS_V1)

    def _cancel_attempt(self, db):
        db.execute(update(AnswerSubmission).where(AnswerSubmission.id == self.answer_id).values(
            grading_attempt_count=AnswerSubmission.grading_attempt_count - 1))

    def generate(self, *args, **kwargs) -> str:
        with ai_usage_scope(self.db, self.owner_id, "grading", self.answer_id,
                            worker=True, before_attempt=self._before_attempt, cancel_attempt=self._cancel_attempt):
            if getattr(self.delegate, "reports_provider_attempts", False):
                return self.delegate.generate(*args, **kwargs)
            with provider_attempt("generation", self.model_name, provider="injected"):
                return self.delegate.generate(*args, **kwargs)


class LearningNotFound(Exception):
    """The requested course-scoped learning resource is absent or foreign."""


class LearningConflict(Exception):
    """The requested transition is not legal in the current lifecycle."""


class GradingReviewNotFound(Exception):
    """Report or fixed judgment does not exist in the authorized scope."""


class GradingReviewConflict(Exception):
    """The requested review transition is stale or no longer permitted."""


class AssessmentUnavailable(Exception):
    """No persisted question set is available for this activity yet."""


class LearningService:
    def __init__(
        self,
        db: Session,
        generation: GenerationGateway,
        embeddings: Optional[EmbeddingGateway] = None,
        grading_dispatcher=None,
    ):
        self.db = db
        self.generation = generation
        self.grading_dispatcher = grading_dispatcher
        self.courses = CourseService(db)
        self.mastery = MasteryService(db, generation, embeddings)
        self.adaptation = AdaptationService(db, generation, embeddings)

    @staticmethod
    def _local_lifecycle_lock(resource_id: UUID, owner_id: int):
        return _LIFECYCLE_LOCKS[hash((str(resource_id), owner_id)) % len(_LIFECYCLE_LOCKS)]

    def _owned_course(self, course_id: UUID, owner_id: int, *, lock: bool = False) -> Course:
        try:
            course = self.courses.get_owned(course_id, owner_id, lock=lock)
        except CourseNotFound as exc:
            raise LearningNotFound(str(course_id)) from exc
        if course.status != CourseStatus.PUBLISHED.value or course.active_version_id is None:
            raise LearningConflict("Course is not published")
        version = (
            self.db.query(CourseVersion)
            .filter(
                CourseVersion.id == course.active_version_id,
                CourseVersion.course_id == course.id,
                CourseVersion.owner_id == owner_id,
            )
            .first()
        )
        if version is None:
            raise LearningConflict("Published course version is unavailable")
        return course

    def _active_activity(self, course_id: UUID, owner_id: int) -> Optional[LearningActivity]:
        return (
            self.db.query(LearningActivity)
            .filter(
                LearningActivity.course_id == course_id,
                LearningActivity.owner_id == owner_id,
                LearningActivity.status != ActivityStatus.COMPLETED.value,
            )
            .order_by(LearningActivity.created_at.desc())
            .first()
        )

    def _session_for_activity(self, activity_id: UUID) -> Optional[AssessmentSession]:
        return (
            self.db.query(AssessmentSession)
            .filter(AssessmentSession.activity_id == activity_id)
            .first()
        )

    def _reconcile_submitted_activity(self, activity: LearningActivity, course_id: UUID, owner_id: int) -> None:
        assessment = self._session_for_activity(activity.id)
        if assessment is not None and assessment.status == AssessmentStatus.SUBMITTED.value:
            self._reconcile_activity_status(course_id, assessment.id, owner_id)
            self.db.refresh(activity)

    def _activity_out(self, activity: LearningActivity) -> dict:
        version = (
            self.db.query(CourseVersion)
            .filter(
                CourseVersion.id == activity.course_version_id,
                CourseVersion.course_id == activity.course_id,
                CourseVersion.owner_id == activity.owner_id,
            )
            .first()
        )
        if version is None:
            raise LearningNotFound(str(activity.id))
        if activity.decision_id is not None:
            decision = (
                self.db.query(AdaptationDecision)
                .filter(
                    AdaptationDecision.id == activity.decision_id,
                    AdaptationDecision.owner_id == activity.owner_id,
                    AdaptationDecision.course_id == activity.course_id,
                )
                .first()
            )
            if decision is None:
                raise LearningNotFound(str(activity.id))
        if activity.lesson_id is not None:
            lesson = (
                self.db.query(Lesson)
                .join(Module, Module.id == Lesson.module_id)
                .filter(Lesson.id == activity.lesson_id, Module.course_version_id == activity.course_version_id)
                .first()
            )
            if lesson is None:
                raise LearningNotFound(str(activity.id))
        target_ids = [UUID(item) if isinstance(item, str) else item for item in activity.target_concept_ids]
        valid_targets = {
            row[0]
            for row in self.db.query(Concept.id)
            .filter(
                Concept.id.in_(target_ids),
                Concept.course_id == activity.course_id,
                Concept.course_version_id == activity.course_version_id,
                Concept.owner_id == activity.owner_id,
            )
            .all()
        } if target_ids else set()
        if len(valid_targets) != len(set(target_ids)):
            raise LearningNotFound(str(activity.id))
        session = self._session_for_activity(activity.id)
        preparations = (
            self.db.query(ActivityPreparation)
            .filter(
                ActivityPreparation.activity_id == activity.id,
                ActivityPreparation.include_assessment.is_(True),
                ActivityPreparation.activity_purpose == activity.activity_type,
            )
            .order_by(ActivityPreparation.created_at.asc())
            .all()
        )
        target_key = sorted(str(item) for item in target_ids)
        preparation = next(
            (
                row for row in preparations
                if sorted(str(item) for item in row.target_concept_ids or []) == target_key
            ),
            None,
        )
        prepared_count = (
            self.db.query(PreparedActivityQuestion.id)
            .filter(PreparedActivityQuestion.preparation_id == preparation.id)
            .count()
            if preparation is not None
            else 0
        )
        expected_prepared_count = self._expected_question_count(activity, preparation)
        supported_experience = (
            activity.activity_type in PREPARED_ACTIVITY_TYPES
            and (
                (
                    activity.activity_type == "PREREQUISITE_REMEDIATION"
                    or (activity_includes_teaching(activity.activity_type) and activity.lesson_id is not None)
                )
                or (not activity_includes_teaching(activity.activity_type) and activity.lesson_id is None)
            )
        ) or (activity.activity_type == "DIAGNOSTIC" and session is not None)
        return {
            "id": activity.id,
            "course_version_id": activity.course_version_id,
            "decision_id": activity.decision_id,
            "activity_type": activity.activity_type,
            "experience_availability": "SUPPORTED" if supported_experience else "UNAVAILABLE",
            "unavailable_reason": None
            if supported_experience
            else "This activity cannot be prepared from its saved course concepts. Your selection and progress are still saved.",
            "target_concept_ids": [UUID(item) if isinstance(item, str) else item for item in activity.target_concept_ids],
            "lesson_id": activity.lesson_id,
            "reason": activity.reason_text,
            "status": activity.status,
            "presentation_format": activity.presentation_format,
            "question_count": expected_prepared_count,
            "reading_position": activity.reading_position,
            "reading_completed_at": activity.reading_completed_at,
            "assessment_session_id": session.id if session else None,
            "preparation": None if preparation is None else {
                "id": preparation.id,
                "status": preparation.status,
                "stage": preparation.stage,
                "progress": preparation.progress,
                "error_category": preparation.error_category,
                "content_ready": (
                    not activity_includes_teaching(activity.activity_type)
                    or preparation.content_artifact_id is not None
                ),
                "assessment_ready": preparation is not None
                and preparation.status == PreparationStatus.READY
                and prepared_count == expected_prepared_count,
                "updated_at": preparation.updated_at,
            },
        }

    @staticmethod
    def _expected_question_count(activity: LearningActivity, preparation: ActivityPreparation | None) -> int:
        p4_count = {
            "PREREQUISITE_REMEDIATION": settings.P4_REMEDIATION_QUESTION_COUNT_V1,
            "TARGETED_PRACTICE": settings.P4_TARGETED_PRACTICE_QUESTION_COUNT_V1,
            "CHALLENGE": settings.P4_CHALLENGE_QUESTION_COUNT_V1,
        }
        if activity.activity_type in p4_count:
            return p4_count[activity.activity_type]
        concept_count = len(activity.target_concept_ids or [])
        if preparation is not None and preparation.activity_id is None:
            concept_count = len(preparation.target_concept_ids or []) or concept_count
        return max(settings.P2_ASSESSMENT_DEFAULT_QUESTION_COUNT_V1, concept_count)

    def _owned_activity(self, course_id: UUID, activity_id: UUID, owner_id: int, *, lock: bool = False):
        query = (
            self.db.query(LearningActivity)
            .filter(
                LearningActivity.id == activity_id,
                LearningActivity.course_id == course_id,
                LearningActivity.owner_id == owner_id,
            )
        )
        if lock:
            query = query.with_for_update().populate_existing()
        activity = query.first()
        if activity is None:
            raise LearningNotFound(str(activity_id))
        self._activity_out(activity)
        return activity

    def _owned_session(self, course_id: UUID, session_id: UUID, owner_id: int, *, lock: bool = False):
        query = (
            self.db.query(AssessmentSession)
            .join(LearningActivity, LearningActivity.id == AssessmentSession.activity_id)
            .join(CourseVersion, CourseVersion.id == AssessmentSession.course_version_id)
            .filter(
                AssessmentSession.id == session_id,
                LearningActivity.course_id == course_id,
                LearningActivity.owner_id == owner_id,
                LearningActivity.course_version_id == AssessmentSession.course_version_id,
                CourseVersion.course_id == course_id,
                CourseVersion.owner_id == owner_id,
            )
        )
        if lock:
            query = query.with_for_update().populate_existing()
        session = query.first()
        if session is None:
            raise LearningNotFound(str(session_id))
        return session

    def _questions_for_activity(self, activity: LearningActivity) -> list[Question]:
        concept_ids = [UUID(item) if isinstance(item, str) else item for item in activity.target_concept_ids]
        if not concept_ids:
            return []
        preparation = (
            self.db.query(ActivityPreparation)
            .filter(
                ActivityPreparation.activity_id == activity.id,
                ActivityPreparation.include_assessment.is_(True),
            )
            .order_by(ActivityPreparation.created_at.asc())
            .first()
        )
        if preparation is not None:
            prepared_targets = sorted(str(item) for item in preparation.target_concept_ids or [])
            if (
                preparation.activity_purpose != activity.activity_type
                or prepared_targets != sorted(str(item) for item in concept_ids)
            ):
                return []
            if preparation.status != PreparationStatus.READY:
                return []
            prepared_items = (
                self.db.query(PreparedActivityQuestion, Question)
                .join(Question, Question.id == PreparedActivityQuestion.question_id)
                .filter(
                    PreparedActivityQuestion.preparation_id == preparation.id,
                    Question.owner_id == activity.owner_id,
                    Question.course_id == activity.course_id,
                    Question.course_version_id == activity.course_version_id,
                    Question.version == PreparedActivityQuestion.question_version,
                    Question.is_diagnostic == 0,
                )
                .order_by(PreparedActivityQuestion.position)
                .all()
            )
            expected_count = self.db.query(PreparedActivityQuestion.id).filter_by(
                preparation_id=preparation.id
            ).count()
            if len(prepared_items) != expected_count:
                return []
            target_set = set(concept_ids)
            for _, question in prepared_items:
                question_concepts = {
                    item[0]
                    for item in self.db.query(QuestionConcept.concept_id)
                    .filter(QuestionConcept.question_id == question.id)
                    .all()
                }
                if len(question_concepts) != 1 or not question_concepts.issubset(target_set):
                    return []
            return [question for _, question in prepared_items]
        query = self.db.query(Question).join(QuestionConcept).filter(
            Question.course_id == activity.course_id,
            Question.course_version_id == activity.course_version_id,
            Question.owner_id == activity.owner_id,
            QuestionConcept.concept_id.in_(concept_ids),
        )
        if activity.decision_id is not None:
            decision_questions = (
                query.filter(Question.decision_id == activity.decision_id)
                .distinct()
                .order_by(Question.created_at.asc(), Question.id.asc())
                .all()
            )
            if decision_questions:
                return decision_questions[:MAX_SESSION_QUESTIONS]
        return (
            query.distinct()
            .order_by(Question.created_at.asc(), Question.id.asc())
            .limit(MAX_SESSION_QUESTIONS)
            .all()
        )

    def _create_session(
        self,
        activity: LearningActivity,
        assessment_type: str,
        questions: list[Question],
    ) -> AssessmentSession:
        if not questions:
            raise AssessmentUnavailable("No saved questions are available for this activity yet")
        session = AssessmentSession(
            activity_id=activity.id,
            course_version_id=activity.course_version_id,
            assessment_type=assessment_type,
            status=AssessmentStatus.OPEN.value,
        )
        self.db.add(session)
        self.db.flush()
        for position, question in enumerate(questions):
            if (
                question.owner_id != activity.owner_id
                or question.course_id != activity.course_id
                or question.course_version_id != activity.course_version_id
            ):
                raise AssessmentUnavailable("Question does not belong to the activity course version")
            links = (
                self.db.query(QuestionConcept, Concept)
                .join(Concept, Concept.id == QuestionConcept.concept_id)
                .filter(QuestionConcept.question_id == question.id)
                .all()
            )
            if not links or any(
                concept.course_id != activity.course_id
                or concept.course_version_id != activity.course_version_id
                or concept.owner_id != activity.owner_id
                for _, concept in links
            ):
                raise AssessmentUnavailable("Question concepts do not belong to the activity course version")
            self.db.add(
                AssessmentQuestion(
                    session_id=session.id,
                    question_id=question.id,
                    question_version=question.version,
                    position=position,
                )
            )
        self.db.flush()
        return session

    def select_or_resume(self, course_id: UUID, owner_id: int) -> dict:
        lock = self._local_lifecycle_lock(course_id, owner_id)
        with lock:
            return self._select_or_resume_locked(course_id, owner_id)

    def _select_or_resume_locked(self, course_id: UUID, owner_id: int) -> dict:
        course = self._owned_course(course_id, owner_id, lock=True)
        active = self._active_activity(course_id, owner_id)
        if active is not None:
            self._reconcile_submitted_activity(active, course_id, owner_id)
            return self._activity_out(active)

        try:
            recommendation = self.adaptation.recommend_next(course_id, owner_id, persist=False)
        except AdaptationNotFound as exc:
            self.db.rollback()
            raise LearningConflict(str(exc) or "No activity is available") from exc

        selected = recommendation.recommended
        activity = LearningActivity(
            owner_id=owner_id,
            course_id=course_id,
            course_version_id=course.active_version_id,
            decision_id=recommendation.decision_id,
            activity_type=selected["activity_type"],
            target_concept_ids=selected["concept_ids"],
            lesson_id=UUID(selected["lesson_id"]) if selected["lesson_id"] else None,
            reason_text=selected["reason"],
            status=ActivityStatus.READY.value,
            presentation_format=selected.get("presentation_format") or "detailed",
        )
        self.db.add(activity)
        try:
            self.db.commit()
        except IntegrityError:
            # The partial unique index is the final cross-process guard. The
            # losing request returns the winner's persisted activity.
            self.db.rollback()
            active = self._active_activity(course_id, owner_id)
            if active is None:
                raise
            return self._activity_out(active)
        self.db.refresh(activity)
        return self._activity_out(activity)

    def get_activity(self, course_id: UUID, activity_id: UUID, owner_id: int) -> dict:
        self._owned_course(course_id, owner_id)
        activity = self._owned_activity(course_id, activity_id, owner_id)
        self._reconcile_submitted_activity(activity, course_id, owner_id)
        return self._activity_out(activity)

    def update_progress(
        self,
        course_id: UUID,
        activity_id: UUID,
        owner_id: int,
        reading_position: Optional[int] = None,
        presentation_format: Optional[str] = None,
    ) -> dict:
        self._owned_course(course_id, owner_id)
        activity = self._owned_activity(course_id, activity_id, owner_id, lock=True)
        if activity.status == ActivityStatus.COMPLETED.value:
            raise LearningConflict("Completed activity progress is read-only")
        if reading_position is not None:
            activity.reading_position = reading_position
        if presentation_format is not None:
            activity.presentation_format = presentation_format
        if activity.status in {ActivityStatus.SELECTED.value, ActivityStatus.READY.value}:
            activity.status = ActivityStatus.IN_PROGRESS.value
        self.db.commit()
        self.db.refresh(activity)
        return self._activity_out(activity)

    def complete_reading(self, course_id: UUID, activity_id: UUID, owner_id: int) -> dict:
        self._owned_course(course_id, owner_id)
        activity = self._owned_activity(course_id, activity_id, owner_id, lock=True)
        if activity.status == ActivityStatus.COMPLETED.value:
            return self._activity_out(activity)
        if not activity_includes_teaching(activity.activity_type):
            raise LearningConflict("This activity has no lesson reading to complete")
        if activity.activity_type in P2_SUPPORTED_ACTIVITY_TYPES and activity.lesson_id is None:
            raise LearningConflict("This lesson activity has no selected lesson")
        if activity.reading_completed_at is None:
            activity.reading_completed_at = datetime.now(timezone.utc)
            activity.status = ActivityStatus.AWAITING_ASSESSMENT.value
        session = self._session_for_activity(activity.id)
        if session is not None and session.status == AssessmentStatus.SUBMITTED.value:
            self._sync_activity_status(session)
        self.db.commit()
        self.db.refresh(activity)
        return self._activity_out(activity)

    def start_diagnostic(
        self,
        course_id: UUID,
        owner_id: int,
        max_questions: Optional[int] = None,
    ) -> dict:
        lock = self._local_lifecycle_lock(course_id, owner_id)
        with lock:
            return self._start_diagnostic_locked(course_id, owner_id, max_questions)

    def resume_diagnostic(self, course_id: UUID, owner_id: int) -> Optional[dict]:
        lock = self._local_lifecycle_lock(course_id, owner_id)
        with lock:
            self._owned_course(course_id, owner_id, lock=True)
            active = self._active_activity(course_id, owner_id)
            if active is not None:
                self._reconcile_submitted_activity(active, course_id, owner_id)
                active_session = self._session_for_activity(active.id)
                if active.activity_type == "DIAGNOSTIC" and active_session is not None:
                    return self._session_out(active_session, owner_id, course_id)
                raise LearningConflict("Resume the active activity before starting a diagnostic")
            previous = (
                self.db.query(LearningActivity)
                .filter(
                    LearningActivity.owner_id == owner_id,
                    LearningActivity.course_id == course_id,
                    LearningActivity.activity_type == "DIAGNOSTIC",
                )
                .order_by(LearningActivity.created_at.desc())
                .first()
            )
            if previous is None:
                return None
            session = self._session_for_activity(previous.id)
            return self._session_out(session, owner_id, course_id) if session is not None else None

    def _start_diagnostic_locked(
        self,
        course_id: UUID,
        owner_id: int,
        max_questions: Optional[int],
    ) -> dict:
        course = self._owned_course(course_id, owner_id, lock=True)
        active = self._active_activity(course_id, owner_id)
        if active is not None:
            active_session = self._session_for_activity(active.id)
            if active.activity_type == "DIAGNOSTIC" and active_session is not None:
                return self._session_out(active_session, owner_id, course_id)
            raise LearningConflict("Resume the active activity before starting a diagnostic")

        previous = (
            self.db.query(LearningActivity)
            .filter(
                LearningActivity.owner_id == owner_id,
                LearningActivity.course_id == course_id,
                LearningActivity.activity_type == "DIAGNOSTIC",
            )
            .order_by(LearningActivity.created_at.desc())
            .first()
        )
        if previous is not None:
            existing = self._session_for_activity(previous.id)
            if existing is not None:
                return self._session_out(existing, owner_id, course_id)

        questions = self.mastery.generate_diagnostic(course_id, owner_id, max_questions, commit=False)
        if not questions:
            self.db.rollback()
            raise AssessmentUnavailable("No diagnostic questions could be prepared for this course")
        concept_ids = sorted(
            {
                link.concept_id
                for question in questions
                for link in self.db.query(QuestionConcept)
                .filter(QuestionConcept.question_id == question.id)
                .all()
            },
            key=str,
        )
        activity = LearningActivity(
            owner_id=owner_id,
            course_id=course_id,
            course_version_id=course.active_version_id,
            activity_type="DIAGNOSTIC",
            target_concept_ids=[str(item) for item in concept_ids],
            status=ActivityStatus.IN_PROGRESS.value,
            presentation_format="quiz_first",
        )
        self.db.add(activity)
        self.db.flush()
        session = self._create_session(activity, AssessmentType.DIAGNOSTIC.value, questions)
        self.db.commit()
        self.db.refresh(session)
        return self._session_out(session, owner_id, course_id)

    def start_activity_assessment(
        self,
        course_id: UUID,
        activity_id: UUID,
        owner_id: int,
    ) -> dict:
        lock = self._local_lifecycle_lock(activity_id, owner_id)
        with lock:
            return self._start_activity_assessment_locked(course_id, activity_id, owner_id)

    def _start_activity_assessment_locked(
        self,
        course_id: UUID,
        activity_id: UUID,
        owner_id: int,
    ) -> dict:
        self._owned_course(course_id, owner_id, lock=True)
        activity = self._owned_activity(course_id, activity_id, owner_id, lock=True)
        if activity.activity_type not in PREPARED_ACTIVITY_TYPES:
            raise AssessmentUnavailable(
                "This selected activity type has no supported assessment"
            )
        if activity.activity_type in P2_SUPPORTED_ACTIVITY_TYPES and activity.lesson_id is None:
            raise AssessmentUnavailable("This teaching activity has no selected lesson")
        if activity.activity_type == "PREREQUISITE_REMEDIATION" and activity.lesson_id is not None:
            target_ids = [UUID(item) if isinstance(item, str) else item for item in activity.target_concept_ids]
            lesson_has_target = (
                self.db.query(LessonConcept.concept_id)
                .filter(
                    LessonConcept.lesson_id == activity.lesson_id,
                    LessonConcept.concept_id.in_(target_ids),
                )
                .count()
            ) == len(target_ids)
            if not lesson_has_target:
                raise AssessmentUnavailable("The remediation lesson does not cover its selected concept")
        if not activity_includes_teaching(activity.activity_type) and activity.lesson_id is not None:
            raise AssessmentUnavailable("This question-only activity cannot depend on lesson reading")
        existing = self._session_for_activity(activity.id)
        if existing is not None:
            self._reconcile_submitted_activity(activity, course_id, owner_id)
            return self._session_out(existing, owner_id, course_id)
        if activity.status == ActivityStatus.COMPLETED.value:
            raise LearningConflict("Completed activity has no assessment session")
        if activity_includes_teaching(activity.activity_type) and activity.reading_completed_at is None:
            raise LearningConflict("Complete lesson reading before starting its assessment")
        questions = self._questions_for_activity(activity)
        try:
            session = self._create_session(activity, AssessmentType.ACTIVITY.value, questions)
            activity.status = ActivityStatus.IN_PROGRESS.value
            self.db.commit()
        except AssessmentUnavailable:
            self.db.rollback()
            raise
        except IntegrityError:
            self.db.rollback()
            activity = self._owned_activity(course_id, activity_id, owner_id)
            existing = self._session_for_activity(activity.id)
            if existing is None:
                raise
            self._reconcile_submitted_activity(activity, course_id, owner_id)
            return self._session_out(existing, owner_id, course_id)
        return self._session_out(session, owner_id, course_id)

    def get_assessment(self, course_id: UUID, session_id: UUID, owner_id: int) -> dict:
        self._owned_course(course_id, owner_id)
        session = self._owned_session(course_id, session_id, owner_id)
        self._recover_grading(session, owner_id)
        self._reconcile_activity_status(course_id, session_id, owner_id)
        return self._session_out(session, owner_id, course_id)

    def _session_question(self, session: AssessmentSession, question_id: UUID, owner_id: int, course_id: UUID):
        row = (
            self.db.query(AssessmentQuestion, Question)
            .join(Question, Question.id == AssessmentQuestion.question_id)
            .filter(
                AssessmentQuestion.session_id == session.id,
                AssessmentQuestion.question_id == question_id,
                Question.owner_id == owner_id,
                Question.course_id == course_id,
                Question.course_version_id == session.course_version_id,
                Question.version == AssessmentQuestion.question_version,
            )
            .first()
        )
        if row is None:
            raise LearningNotFound(str(question_id))
        return row

    @staticmethod
    def _validate_answer(question: Question, answer: Any) -> None:
        valid = {
            "MCQ": isinstance(answer, str),
            "MULTI_SELECT": isinstance(answer, list) and all(isinstance(value, str) for value in answer),
            "SHORT_TEXT": isinstance(answer, str),
            "NUMERIC": isinstance(answer, (float, int)) and not isinstance(answer, bool),
        }.get(question.question_type, False)
        if not valid:
            raise LearningConflict("Answer type does not match the assessment question")
        if question.question_type == "SHORT_TEXT" and len(answer) > settings.P5_SHORT_ANSWER_MAX_CHARS_V1:
            raise LearningConflict("Short answer is longer than the allowed response length")

    def _dispatch_answer_grading(self, answer_id: UUID, owner_id: int, *, force: bool = False) -> None:
        if self.grading_dispatcher is None:
            return
        answer = (
            self.db.query(AnswerSubmission)
            .join(AssessmentQuestion, AssessmentQuestion.id == AnswerSubmission.assessment_question_id)
            .join(AssessmentSession, AssessmentSession.id == AssessmentQuestion.session_id)
            .join(LearningActivity, LearningActivity.id == AssessmentSession.activity_id)
            .filter(AnswerSubmission.id == answer_id, LearningActivity.owner_id == owner_id)
            .with_for_update()
            .populate_existing()
            .first()
        )
        if answer is None or answer.status != AnswerStatus.AWAITING_GRADING.value:
            return
        now = _now()
        if (
            answer.grading_lease_token is not None
            and answer.grading_lease_expires_at is not None
            and _as_utc(answer.grading_lease_expires_at) > now
        ):
            return
        if answer.grading_attempt_count >= _grading_call_limit(answer):
            answer.status = AnswerStatus.GRADING_FAILED.value
            answer.failure_code = "grading_attempts_exhausted"
            answer.grading_lease_token = None
            answer.grading_lease_expires_at = None
            self.db.commit()
            return
        if (
            not force
            and answer.grading_last_dispatched_at is not None
            and now - _as_utc(answer.grading_last_dispatched_at)
            < timedelta(seconds=settings.P5_GRADING_DISPATCH_COOLDOWN_SECONDS_V1)
        ):
            return
        answer.grading_last_dispatched_at = now
        self.db.commit()
        try:
            self.grading_dispatcher.enqueue(answer_id, owner_id)
        except Exception:
            # The answer is durable. A later poll/resume can safely dispatch it again.
            pending = self.db.query(AnswerSubmission).filter_by(id=answer_id).first()
            if pending is not None and pending.status == AnswerStatus.AWAITING_GRADING.value:
                pending.grading_last_dispatched_at = None
                self.db.commit()

    def _recover_grading(self, session: AssessmentSession, owner_id: int) -> None:
        if self.grading_dispatcher is None:
            return
        answer_ids = [
            row[0]
            for row in (
                self.db.query(AnswerSubmission.id)
                .join(AssessmentQuestion, AssessmentQuestion.id == AnswerSubmission.assessment_question_id)
                .filter(
                    AssessmentQuestion.session_id == session.id,
                    AnswerSubmission.status == AnswerStatus.AWAITING_GRADING.value,
                )
                .order_by(AssessmentQuestion.position)
                .all()
            )
        ]
        for answer_id in answer_ids:
            self._dispatch_answer_grading(answer_id, owner_id)

    def submit_answer(
        self,
        course_id: UUID,
        session_id: UUID,
        question_id: UUID,
        owner_id: int,
        given_answer: Any,
    ) -> dict:
        lock = self._local_lifecycle_lock(session_id, owner_id)
        with lock:
            return self._submit_answer_locked(course_id, session_id, question_id, owner_id, given_answer)

    def _submit_answer_locked(
        self,
        course_id: UUID,
        session_id: UUID,
        question_id: UUID,
        owner_id: int,
        given_answer: Any,
    ) -> dict:
        self._owned_course(course_id, owner_id)
        session = self._owned_session(course_id, session_id, owner_id, lock=True)
        assessment_question, question = self._session_question(session, question_id, owner_id, course_id)
        self._validate_answer(question, given_answer)
        existing = (
            self.db.query(AnswerSubmission)
            .filter(AnswerSubmission.assessment_question_id == assessment_question.id)
            .first()
        )
        if existing is not None:
            if existing.given_answer != given_answer:
                raise LearningConflict("A different answer is already confirmed for this question")
            self._dispatch_answer_grading(existing.id, owner_id)
            self._reconcile_activity_status(course_id, session_id, owner_id)
            return self._session_out(session, owner_id, course_id)
        if session.status != AssessmentStatus.OPEN.value:
            raise LearningConflict("Assessment question set is already submitted")

        answer = AnswerSubmission(
            assessment_question_id=assessment_question.id,
            given_answer=given_answer,
            status=AnswerStatus.AWAITING_GRADING.value,
            grading_call_limit=settings.P5_GRADING_MAX_PROVIDER_CALLS_V1,
        )
        self.db.add(answer)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            answer = (
                self.db.query(AnswerSubmission)
                .filter(AnswerSubmission.assessment_question_id == assessment_question.id)
                .first()
            )
            if answer is None:
                raise
            if answer.given_answer != given_answer:
                raise LearningConflict("A different answer is already confirmed for this question")
            self._dispatch_answer_grading(answer.id, owner_id)
            self._reconcile_activity_status(course_id, session_id, owner_id)
            return self._session_out(session, owner_id, course_id)

        self._dispatch_answer_grading(answer.id, owner_id, force=True)
        self._reconcile_activity_status(course_id, session_id, owner_id)
        return self._session_out(session, owner_id, course_id)

    def retry_grading(self, course_id: UUID, session_id: UUID, question_id: UUID, owner_id: int) -> dict:
        self._owned_course(course_id, owner_id)
        session = self._owned_session(course_id, session_id, owner_id)
        assessment_question, question = self._session_question(session, question_id, owner_id, course_id)
        answer = (
            self.db.query(AnswerSubmission)
            .filter(AnswerSubmission.assessment_question_id == assessment_question.id)
            .with_for_update()
            .populate_existing()
            .first()
        )
        if answer is None:
            raise LearningConflict("Submit an answer before retrying grading")
        if answer.status == AnswerStatus.GRADING_FAILED.value:
            if answer.grading_attempt_count >= _grading_call_limit(answer):
                raise LearningConflict("Grading retries are exhausted for this saved answer")
            answer.status = AnswerStatus.AWAITING_GRADING.value
            answer.failure_code = None
            answer.grading_lease_token = None
            answer.grading_lease_expires_at = None
            answer.grading_last_dispatched_at = None
            self.db.commit()
            self._dispatch_answer_grading(answer.id, owner_id, force=True)
        elif answer.status == AnswerStatus.AWAITING_GRADING.value:
            self._dispatch_answer_grading(answer.id, owner_id)
        self._reconcile_activity_status(course_id, session_id, owner_id)
        return self._session_out(session, owner_id, course_id)

    def report_grading_issue(
        self,
        course_id: UUID,
        session_id: UUID,
        question_id: UUID,
        owner_id: int,
        report_text: str,
    ) -> dict:
        if not report_text.strip() or len(report_text) > settings.P5_REPORT_MAX_CHARS_V1:
            raise LearningConflict("Report text is empty or longer than the allowed limit")
        row = (
            self.db.query(AnswerSubmission, AssessmentQuestion, AssessmentSession, LearningActivity, Question, QuestionAttempt, GradingJudgment)
            .join(AssessmentQuestion, AssessmentQuestion.id == AnswerSubmission.assessment_question_id)
            .join(AssessmentSession, AssessmentSession.id == AssessmentQuestion.session_id)
            .join(LearningActivity, LearningActivity.id == AssessmentSession.activity_id)
            .join(Question, Question.id == AssessmentQuestion.question_id)
            .join(QuestionAttempt, QuestionAttempt.assessment_question_id == AssessmentQuestion.id)
            .join(GradingJudgment, GradingJudgment.answer_submission_id == AnswerSubmission.id)
            .filter(
                AssessmentSession.id == session_id,
                AssessmentSession.course_version_id == LearningActivity.course_version_id,
                Question.course_version_id == AssessmentSession.course_version_id,
                AssessmentSession.status == AssessmentStatus.SUBMITTED.value,
                LearningActivity.course_id == course_id,
                LearningActivity.owner_id == owner_id,
                Question.id == question_id,
                Question.owner_id == owner_id,
                Question.course_id == course_id,
                Question.version == AssessmentQuestion.question_version,
                Question.question_type == "SHORT_TEXT",
                AnswerSubmission.status == AnswerStatus.GRADED.value,
            )
            .first()
        )
        if row is None:
            raise LearningNotFound(str(question_id))
        answer, _assessment_question, _session, _activity, _question, _attempt, _judgment = row
        existing = (
            self.db.query(GradingIssueReport)
            .filter(GradingIssueReport.answer_submission_id == answer.id)
            .first()
        )
        if existing is not None:
            return self._report_ack(existing)
        report = GradingIssueReport(
            answer_submission_id=answer.id,
            owner_id=owner_id,
            course_id=course_id,
            report_text=report_text.strip(),
            status="OPEN",
        )
        self.db.add(report)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            report = (
                self.db.query(GradingIssueReport)
                .filter(GradingIssueReport.answer_submission_id == answer.id)
                .first()
            )
            if report is None:
                raise
        return self._report_ack(report)

    @staticmethod
    def _report_ack(report: GradingIssueReport) -> dict:
        return {"id": report.id, "status": report.status, "received": True, "created_at": report.created_at}

    def list_grading_reviews(
        self, status: str | None = None, limit: int | None = None, offset: int = 0
    ) -> dict:
        query = self.db.query(GradingIssueReport)
        if status is not None:
            query = query.filter(GradingIssueReport.status == status)
        page_size = min(limit or settings.P5_REVIEW_PAGE_SIZE_V1, settings.P5_REVIEW_PAGE_SIZE_V1)
        reports = (
            query.order_by(GradingIssueReport.created_at.asc(), GradingIssueReport.id.asc())
            .offset(max(0, offset))
            .limit(page_size + 1)
            .all()
        )
        has_more = len(reports) > page_size
        items = [self._grading_review_item(report.id) for report in reports[:page_size]]
        return {
            "items": items,
            "limit": page_size,
            "offset": max(0, offset),
            "has_more": has_more,
            "next_offset": max(0, offset) + page_size if has_more else None,
        }

    def get_grading_review(self, report_id: UUID) -> dict:
        item = self._grading_review_item(report_id)
        return item

    def _grading_review_item(self, report_id: UUID) -> dict:
        row = (
            self.db.query(GradingIssueReport, AnswerSubmission, AssessmentQuestion, AssessmentSession, LearningActivity, Question, QuestionAttempt, GradingJudgment)
            .join(AnswerSubmission, AnswerSubmission.id == GradingIssueReport.answer_submission_id)
            .join(AssessmentQuestion, AssessmentQuestion.id == AnswerSubmission.assessment_question_id)
            .join(AssessmentSession, AssessmentSession.id == AssessmentQuestion.session_id)
            .join(LearningActivity, LearningActivity.id == AssessmentSession.activity_id)
            .join(Question, Question.id == AssessmentQuestion.question_id)
            .join(QuestionAttempt, QuestionAttempt.assessment_question_id == AssessmentQuestion.id)
            .join(GradingJudgment, GradingJudgment.answer_submission_id == AnswerSubmission.id)
            .filter(
                GradingIssueReport.id == report_id,
                GradingIssueReport.owner_id == LearningActivity.owner_id,
                GradingIssueReport.course_id == LearningActivity.course_id,
                AssessmentSession.course_version_id == LearningActivity.course_version_id,
                Question.owner_id == LearningActivity.owner_id,
                Question.course_id == LearningActivity.course_id,
                AssessmentSession.status == AssessmentStatus.SUBMITTED.value,
                Question.course_version_id == AssessmentSession.course_version_id,
                Question.version == AssessmentQuestion.question_version,
                Question.question_type == "SHORT_TEXT",
                AnswerSubmission.status == AnswerStatus.GRADED.value,
            )
            .first()
        )
        if row is None:
            raise GradingReviewNotFound(str(report_id))
        report, answer, aq, _session, _activity, question, attempt, judgment = row
        sources = (
            self.db.query(Chunk)
            .join(QuestionSource, QuestionSource.chunk_id == Chunk.id)
            .join(Document, Document.id == Chunk.document_id)
            .filter(
                QuestionSource.question_id == question.id,
                Chunk.course_id == report.course_id,
                Chunk.owner_id == report.owner_id,
                Document.course_id == report.course_id,
                Document.owner_id == report.owner_id,
            )
            .order_by(Chunk.id)
            .all()
        )
        corrections = (
            self.db.query(GradingCorrection)
            .filter(GradingCorrection.report_id == report.id)
            .order_by(GradingCorrection.version)
            .all()
        )
        history = (
            self.db.query(GradingReviewEvent)
            .filter(GradingReviewEvent.report_id == report.id)
            .order_by(GradingReviewEvent.created_at, GradingReviewEvent.id)
            .all()
        )
        latest = corrections[-1] if corrections else None
        return {
            "id": report.id,
            "course_id": report.course_id,
            "status": report.status,
            "report_text": report.report_text,
            "created_at": report.created_at,
            "answer": answer.given_answer,
            "question_id": question.id,
            "question_version": aq.question_version,
            "prompt": question.prompt,
            "rubric": question.rubric or [],
            "rubric_passing_criteria": (
                question.rubric_passing_criteria or settings.P5_RUBRIC_PASSING_CRITERIA_V1
            ),
            "expected_reasoning": question.expected_reasoning or question.explanation or "",
            "original_criteria_met": judgment.criteria_met,
            "original_rubric_score": judgment.rubric_score,
            "original_evidence_correctness": judgment.evidence_correctness,
            "sources": [
                {"chunk_id": source.id, "heading_path": source.heading_path, "text": source.text}
                for source in sources
            ],
            "corrections": [
                {
                    "version": correction.version,
                    "criteria_met": correction.criteria_met,
                    "rubric_score": correction.rubric_score,
                    "effective_correctness": correction.effective_correctness,
                    "reason": correction.reason,
                    "created_at": correction.created_at,
                }
                for correction in corrections
            ],
            "history": [
                {
                    "event_type": event.event_type,
                    "reason": event.reason,
                    "correction_version": event.correction_version,
                    "created_at": event.created_at,
                }
                for event in history
            ],
            "latest_effective_criteria_met": latest.criteria_met if latest is not None else judgment.criteria_met,
            "latest_effective_correctness": (
                latest.effective_correctness if latest is not None else judgment.evidence_correctness
            ),
            "latest_correction_version": latest.version if latest is not None else 0,
        }

    def start_grading_review(self, report_id: UUID, reviewer_id: int, reason: str) -> dict:
        report = (
            self.db.query(GradingIssueReport)
            .filter(GradingIssueReport.id == report_id)
            .with_for_update()
            .populate_existing()
            .first()
        )
        if report is None:
            raise GradingReviewNotFound(str(report_id))
        if report.status == "IN_REVIEW":
            return self._grading_review_item(report_id)
        if report.status != "OPEN":
            raise GradingReviewConflict("This report is already resolved")
        report.status = "IN_REVIEW"
        self.db.add(
            GradingReviewEvent(
                report_id=report.id,
                reviewer_id=reviewer_id,
                event_type="IN_REVIEW",
                reason=reason.strip(),
            )
        )
        self.db.commit()
        return self._grading_review_item(report_id)

    def retain_grading_judgment(self, report_id: UUID, reviewer_id: int, reason: str) -> dict:
        report = (
            self.db.query(GradingIssueReport)
            .filter(GradingIssueReport.id == report_id)
            .with_for_update()
            .populate_existing()
            .first()
        )
        if report is None:
            raise GradingReviewNotFound(str(report_id))
        if report.status not in {"OPEN", "IN_REVIEW"}:
            raise GradingReviewConflict("This report has already been resolved")
        report.status = "RETAINED"
        self.db.add(
            GradingReviewEvent(
                report_id=report.id,
                reviewer_id=reviewer_id,
                event_type="RETAINED",
                reason=reason.strip(),
            )
        )
        self.db.commit()
        return self._grading_review_item(report_id)

    def correct_grading_judgment(
        self,
        report_id: UUID,
        reviewer_id: int,
        criteria_met: list[bool],
        reason: str,
        expected_correction_version: int,
    ) -> dict:
        report = (
            self.db.query(GradingIssueReport)
            .filter(GradingIssueReport.id == report_id)
            .with_for_update()
            .populate_existing()
            .first()
        )
        if report is None:
            raise GradingReviewNotFound(str(report_id))
        if report.status not in {"OPEN", "IN_REVIEW", "CORRECTED"}:
            raise GradingReviewConflict("This report cannot be corrected in its current state")
        latest = (
            self.db.query(GradingCorrection)
            .filter(GradingCorrection.report_id == report.id)
            .order_by(GradingCorrection.version.desc())
            .first()
        )
        current_version = latest.version if latest is not None else 0
        if current_version != expected_correction_version:
            raise GradingReviewConflict("A newer review decision was saved. Reload before correcting it.")
        context = (
            self.db.query(AnswerSubmission, AssessmentQuestion, AssessmentSession, LearningActivity, QuestionAttempt, Question)
            .join(AssessmentQuestion, AssessmentQuestion.id == AnswerSubmission.assessment_question_id)
            .join(AssessmentSession, AssessmentSession.id == AssessmentQuestion.session_id)
            .join(LearningActivity, LearningActivity.id == AssessmentSession.activity_id)
            .join(QuestionAttempt, QuestionAttempt.assessment_question_id == AssessmentQuestion.id)
            .join(Question, Question.id == AssessmentQuestion.question_id)
            .filter(
                AnswerSubmission.id == report.answer_submission_id,
                AssessmentSession.status == AssessmentStatus.SUBMITTED.value,
                LearningActivity.owner_id == report.owner_id,
                LearningActivity.course_id == report.course_id,
                AssessmentSession.course_version_id == LearningActivity.course_version_id,
                QuestionAttempt.owner_id == report.owner_id,
                QuestionAttempt.course_id == report.course_id,
                Question.owner_id == report.owner_id,
                Question.course_id == report.course_id,
                Question.course_version_id == AssessmentSession.course_version_id,
                AssessmentQuestion.question_version == Question.version,
                Question.question_type == "SHORT_TEXT",
            )
            .first()
        )
        if context is None:
            raise GradingReviewNotFound(str(report_id))
        _answer, aq, _session, _activity, attempt, question = context
        if len(criteria_met) != len(question.rubric or []):
            raise GradingReviewConflict("Correction criteria do not match the saved rubric")
        rubric_score = sum(criteria_met)
        passing_criteria = question.rubric_passing_criteria or settings.P5_RUBRIC_PASSING_CRITERIA_V1
        correctness = int(rubric_score >= passing_criteria)
        correction = GradingCorrection(
            report_id=report.id,
            question_attempt_id=attempt.id,
            reviewer_id=reviewer_id,
            version=current_version + 1,
            criteria_met=criteria_met,
            rubric_score=rubric_score,
            effective_correctness=correctness,
            reason=reason.strip(),
        )
        self.db.add(correction)
        report.status = "CORRECTED"
        self.db.add(
            GradingReviewEvent(
                report_id=report.id,
                reviewer_id=reviewer_id,
                event_type="CORRECTED",
                reason=reason.strip(),
                correction_version=correction.version,
            )
        )
        self.db.commit()
        return self._grading_review_item(report_id)

    def _reconcile_activity_status(self, course_id: UUID, session_id: UUID, owner_id: int) -> None:
        # Grading commits independently from assessment submission. Reload under
        # the session lock so either this reconciliation or submit_assessment
        # observes the other's committed state before deriving activity status.
        session = self._owned_session(course_id, session_id, owner_id, lock=True)
        self._sync_activity_status(session)
        self.db.commit()

    def grade_saved_answer(self, answer_id: UUID, owner_id: int) -> None:
        """Grade one immutable answer under a fenced lease and write evidence once."""
        joined = (
            self.db.query(AnswerSubmission, AssessmentQuestion, AssessmentSession, LearningActivity, Question)
            .join(AssessmentQuestion, AssessmentQuestion.id == AnswerSubmission.assessment_question_id)
            .join(AssessmentSession, AssessmentSession.id == AssessmentQuestion.session_id)
            .join(LearningActivity, LearningActivity.id == AssessmentSession.activity_id)
            .join(Question, Question.id == AssessmentQuestion.question_id)
            .filter(
                AnswerSubmission.id == answer_id,
                LearningActivity.owner_id == owner_id,
                AssessmentSession.course_version_id == Question.course_version_id,
                AssessmentQuestion.question_version == Question.version,
            )
            .with_for_update(of=AnswerSubmission)
            .populate_existing()
            .first()
        )
        if joined is None:
            return
        answer, assessment_question, session, activity, question = joined
        if answer.status == AnswerStatus.GRADED.value:
            return
        if answer.status != AnswerStatus.AWAITING_GRADING.value:
            return

        now = _now()
        if (
            answer.grading_lease_token is not None
            and answer.grading_lease_expires_at is not None
            and _as_utc(answer.grading_lease_expires_at) > now
        ):
            return
        call_limit = _grading_call_limit(answer)
        if answer.grading_attempt_count >= call_limit:
            answer.status = AnswerStatus.GRADING_FAILED.value
            answer.failure_code = "grading_attempts_exhausted"
            answer.grading_lease_token = None
            answer.grading_lease_expires_at = None
            self.db.commit()
            self._reconcile_activity_status(activity.course_id, session.id, owner_id)
            return

        token = uuid4()
        answer.grading_lease_token = token
        answer.grading_lease_expires_at = now + timedelta(seconds=settings.JOB_LEASE_SECONDS_V1)
        answer.grading_last_dispatched_at = None
        self.db.commit()

        existing_attempt = (
            self.db.query(QuestionAttempt)
            .filter(QuestionAttempt.assessment_question_id == assessment_question.id)
            .first()
        )
        if existing_attempt is not None:
            current = self.db.query(AnswerSubmission).filter_by(id=answer_id).with_for_update().first()
            if current is not None:
                current.status = AnswerStatus.GRADED.value
                current.failure_code = None
                current.grading_lease_token = None
                current.grading_lease_expires_at = None
                self.db.commit()
            self._reconcile_activity_status(activity.course_id, session.id, owner_id)
            return

        criteria_met: list[bool] | None = None
        failure_code = "grading_unavailable"
        gateway = BudgetedGradingGateway(self.generation, self.db, answer_id, owner_id, token)
        for _ in range(call_limit):
            current = self.db.query(AnswerSubmission).filter_by(id=answer_id).first()
            if current is None:
                return
            if current.grading_attempt_count >= _grading_call_limit(current) and criteria_met is None:
                failure_code = "grading_attempts_exhausted"
                break
            try:
                if question.question_type == "SHORT_TEXT":
                    criteria_met = grade_short_text_criteria(question, answer.given_answer, gateway)
                    rubric_score = sum(criteria_met)
                    passing_criteria = question.rubric_passing_criteria or settings.P5_RUBRIC_PASSING_CRITERIA_V1
                    evidence_correctness = int(rubric_score >= passing_criteria)
                else:
                    evidence_correctness = int(grade_attempt(question, answer.given_answer, self.generation) >= 0.5)
                    rubric_score = 0
                failure_code = ""
                break
            except GradingLeaseLost:
                self.db.rollback()
                return
            except GradingCallsExhausted:
                failure_code = "grading_attempts_exhausted"
                self.db.rollback()
                break
            except ProblemDetailException:
                failure_code = "grading_allowance_exhausted"
                self.db.rollback()
                break
            except GenerationError:
                # The gateway has already used its bounded transport retries.
                # A fresh rubric candidate cannot repair an unavailable provider.
                self.db.rollback()
                break
            except Exception as exc:
                if isinstance(exc, GradingError):
                    logger.warning(
                        "Assessment grading response rejected; answer=%s question=%s details=%s",
                        answer_id, question.id, exc.diagnostics,
                        extra={"answer_id": str(answer_id), "question_id": str(question.id), "grading_details": exc.diagnostics},
                    )
                else:
                    logger.warning("Assessment grading failed (%s)", type(exc).__name__)
                self.db.rollback()
                answer = self.db.query(AnswerSubmission).filter_by(id=answer_id).first()
                if answer is None:
                    return
                if answer.grading_attempt_count >= _grading_call_limit(answer):
                    failure_code = "grading_attempts_exhausted"
                    break

        if failure_code:
            try:
                current = _lock_current_grading_lease(self.db, answer_id, owner_id, token)
            except GradingLeaseLost:
                self.db.rollback()
                return
            current.status = AnswerStatus.GRADING_FAILED.value
            current.failure_code = failure_code
            current.grading_lease_token = None
            current.grading_lease_expires_at = None
            self.db.commit()
            self._reconcile_activity_status(activity.course_id, session.id, owner_id)
            return

        try:
            current = _lock_current_grading_lease(self.db, answer_id, owner_id, token)
            attempt = self.mastery.record_graded_attempt(
                question,
                owner_id,
                current.given_answer,
                evidence_correctness,
                assessment_question_id=assessment_question.id,
                question_version=assessment_question.question_version,
                course_version_id=session.course_version_id,
                commit=False,
            )
            if question.question_type == "SHORT_TEXT":
                judgment = (
                    self.db.query(GradingJudgment)
                    .filter(GradingJudgment.answer_submission_id == answer_id)
                    .first()
                )
                if judgment is None:
                    self.db.add(
                        GradingJudgment(
                            answer_submission_id=answer_id,
                            criteria_met=criteria_met or [],
                            rubric_score=rubric_score,
                            evidence_correctness=evidence_correctness,
                            policy_version=P5_GRADING_POLICY_VERSION,
                            model_id=self.generation.model_name,
                        )
                    )
            current.status = AnswerStatus.GRADED.value
            current.failure_code = None
            current.grading_lease_token = None
            current.grading_lease_expires_at = None
            self.db.commit()
        except GradingLeaseLost:
            self.db.rollback()
            return
        except IntegrityError:
            self.db.rollback()
            current = self.db.query(AnswerSubmission).filter_by(id=answer_id).with_for_update().first()
            attempt_exists = (
                self.db.query(QuestionAttempt.id)
                .filter(QuestionAttempt.assessment_question_id == assessment_question.id)
                .first()
            )
            if current is None or not attempt_exists:
                raise
            current.status = AnswerStatus.GRADED.value
            current.failure_code = None
            current.grading_lease_token = None
            current.grading_lease_expires_at = None
            self.db.commit()
        self._reconcile_activity_status(activity.course_id, session.id, owner_id)

    def submit_assessment(self, course_id: UUID, session_id: UUID, owner_id: int) -> dict:
        self._owned_course(course_id, owner_id)
        session = self._owned_session(course_id, session_id, owner_id, lock=True)
        if session.status == AssessmentStatus.OPEN.value:
            activity = (
                self.db.query(LearningActivity)
                .filter(
                    LearningActivity.id == session.activity_id,
                    LearningActivity.owner_id == owner_id,
                    LearningActivity.course_id == course_id,
                )
                .first()
            )
            if activity is None:
                raise LearningNotFound(str(session.id))
            if activity.lesson_id is not None and activity.reading_completed_at is None:
                raise LearningConflict("Complete lesson reading before submitting its assessment")
            questions = (
                self.db.query(AssessmentQuestion)
                .filter(AssessmentQuestion.session_id == session.id)
                .order_by(AssessmentQuestion.position)
                .all()
            )
            answered_ids = {
                row[0]
                for row in self.db.query(AnswerSubmission.assessment_question_id)
                .filter(AnswerSubmission.assessment_question_id.in_([q.id for q in questions]))
                .all()
            }
            if not questions or len(answered_ids) != len(questions):
                raise LearningConflict("Answer every assessment question before submitting the set")
            session.status = AssessmentStatus.SUBMITTED.value
            session.submitted_at = datetime.now(timezone.utc)
            self.db.flush()
        self._sync_activity_status(session)
        self.db.commit()
        self._recover_grading(session, owner_id)
        return self._session_out(session, owner_id, course_id)

    def _sync_activity_status(self, session: AssessmentSession) -> None:
        activity = (
            self.db.query(LearningActivity)
            .filter(LearningActivity.id == session.activity_id)
            .with_for_update()
            .first()
        )
        if activity is None or session.status != AssessmentStatus.SUBMITTED.value:
            return
        if activity.lesson_id is not None and activity.reading_completed_at is None:
            activity.status = ActivityStatus.IN_PROGRESS.value
            return
        states = [
            row[0]
            for row in self.db.query(AnswerSubmission.status)
            .join(AssessmentQuestion, AssessmentQuestion.id == AnswerSubmission.assessment_question_id)
            .filter(AssessmentQuestion.session_id == session.id)
            .all()
        ]
        if states and all(state == AnswerStatus.GRADED.value for state in states):
            activity.status = ActivityStatus.COMPLETED.value
        else:
            activity.status = ActivityStatus.AWAITING_GRADING.value

    def _session_out(self, session: AssessmentSession, owner_id: int, course_id: UUID) -> dict:
        activity = (
            self.db.query(LearningActivity)
            .filter(
                LearningActivity.id == session.activity_id,
                LearningActivity.owner_id == owner_id,
                LearningActivity.course_id == course_id,
                LearningActivity.course_version_id == session.course_version_id,
            )
            .first()
        )
        if activity is None:
            raise LearningNotFound(str(session.id))
        items = (
            self.db.query(AssessmentQuestion, Question)
            .join(Question, Question.id == AssessmentQuestion.question_id)
            .filter(
                AssessmentQuestion.session_id == session.id,
                Question.owner_id == owner_id,
                Question.course_id == course_id,
                Question.course_version_id == session.course_version_id,
                Question.version == AssessmentQuestion.question_version,
            )
            .order_by(AssessmentQuestion.position)
            .all()
        )
        if not items:
            raise LearningNotFound(str(session.id))
        expected_items = self.db.query(AssessmentQuestion.id).filter(AssessmentQuestion.session_id == session.id).count()
        if len(items) != expected_items:
            raise LearningNotFound(str(session.id))
        answers: dict[UUID, AnswerSubmission] = {}
        attempts: dict[UUID, QuestionAttempt] = {}
        judgments: dict[UUID, GradingJudgment] = {}
        reports: dict[UUID, GradingIssueReport] = {}
        corrections: dict[UUID, GradingCorrection] = {}
        item_ids = [item.id for item, _ in items]
        if item_ids:
            answers = {
                row.assessment_question_id: row
                for row in self.db.query(AnswerSubmission)
                .filter(AnswerSubmission.assessment_question_id.in_(item_ids))
                .all()
            }
            attempts = {
                row.assessment_question_id: row
                for row in self.db.query(QuestionAttempt)
                .filter(QuestionAttempt.assessment_question_id.in_(item_ids))
                .all()
            }
            answer_ids = [answer.id for answer in answers.values()]
            if answer_ids:
                judgments = {
                    row.answer_submission_id: row
                    for row in self.db.query(GradingJudgment)
                    .filter(GradingJudgment.answer_submission_id.in_(answer_ids))
                    .all()
                }
                reports = {
                    row.answer_submission_id: row
                    for row in self.db.query(GradingIssueReport)
                    .filter(GradingIssueReport.answer_submission_id.in_(answer_ids))
                    .all()
                }
            attempt_ids = [attempt.id for attempt in attempts.values()]
            if attempt_ids:
                correction_rows = (
                    self.db.query(GradingCorrection)
                    .filter(GradingCorrection.question_attempt_id.in_(attempt_ids))
                    .order_by(GradingCorrection.version)
                    .all()
                )
                for row in correction_rows:
                    corrections[row.question_attempt_id] = row
        question_out = []
        for assessment_question, question in items:
            answer = answers.get(assessment_question.id)
            attempt = attempts.get(assessment_question.id)
            judgment = judgments.get(answer.id) if answer is not None else None
            report = reports.get(answer.id) if answer is not None else None
            correction = corrections.get(attempt.id) if attempt is not None else None
            result = None
            if session.status == AssessmentStatus.SUBMITTED.value and attempt is not None:
                source_ids = [
                    row[0]
                    for row in self.db.query(QuestionSource.chunk_id)
                    .join(Chunk, Chunk.id == QuestionSource.chunk_id)
                    .join(Document, Document.id == Chunk.document_id)
                    .filter(
                        QuestionSource.question_id == question.id,
                        Chunk.course_id == course_id,
                        Chunk.owner_id == owner_id,
                        Document.course_id == course_id,
                        Document.owner_id == owner_id,
                    )
                    .order_by(QuestionSource.chunk_id)
                    .all()
                ]
                criteria = correction.criteria_met if correction is not None else judgment.criteria_met if judgment else None
                rubric_details = question.rubric_details or []
                rubric_feedback = (
                    [
                        {
                            "criterion": detail["text"],
                            "met": bool(criteria[index]),
                            "expected_reasoning": detail["expected_reasoning"],
                            "source_chunk_ids": [UUID(item) if isinstance(item, str) else item
                                                 for item in detail["source_chunk_ids"]],
                        }
                        for index, detail in enumerate(rubric_details)
                        if criteria is not None and index < len(criteria)
                    ]
                    if criteria is not None and rubric_details
                    else None
                )
                original_rubric_feedback = None
                if correction is not None and judgment is not None and rubric_details:
                    original_rubric_feedback = [
                        {
                            "criterion": detail["text"],
                            "met": bool(judgment.criteria_met[index]),
                            "expected_reasoning": detail["expected_reasoning"],
                            "source_chunk_ids": [UUID(item) if isinstance(item, str) else item
                                                 for item in detail["source_chunk_ids"]],
                        }
                        for index, detail in enumerate(rubric_details)
                        if index < len(judgment.criteria_met)
                    ]
                result = {
                    "correctness": correction.effective_correctness if correction is not None else attempt.correctness,
                    "expected_answer": question.correct_answer,
                    "rubric": question.rubric,
                    "rubric_passing_criteria": (
                        question.rubric_passing_criteria or settings.P5_RUBRIC_PASSING_CRITERIA_V1
                        if question.question_type == "SHORT_TEXT"
                        else None
                    ),
                    "explanation": question.explanation,
                    "source_chunk_ids": source_ids,
                    "expected_reasoning": question.expected_reasoning,
                    "rubric_score": (
                        correction.rubric_score if correction is not None else judgment.rubric_score
                        if judgment is not None else None
                    ),
                    "rubric_feedback": rubric_feedback,
                    "automated_grading": question.question_type == "SHORT_TEXT",
                    "grade_corrected": correction is not None,
                    "correction_reason": correction.reason if correction is not None else None,
                    "original_correctness": (
                        float(judgment.evidence_correctness)
                        if correction is not None and judgment is not None else None
                    ),
                    "original_rubric_score": (
                        judgment.rubric_score if correction is not None and judgment is not None else None
                    ),
                    "original_rubric_feedback": original_rubric_feedback,
                }
            question_out.append(
                {
                    "question_id": question.id,
                    "question_version": assessment_question.question_version,
                    "position": assessment_question.position,
                    "question_type": question.question_type,
                    "prompt": question.prompt,
                    "options": question.options,
                    "difficulty": question.difficulty,
                    "answer": None
                    if answer is None
                    else {
                        "given_answer": answer.given_answer,
                        "status": answer.status,
                        "submitted_at": answer.submitted_at,
                        "grading_failure": (
                            {
                                "grading_attempts_exhausted": "RETRIES_EXHAUSTED",
                                "grading_allowance_exhausted": "ALLOWANCE_UNAVAILABLE",
                            }.get(answer.failure_code, "GRADING_UNAVAILABLE")
                            if answer.status == AnswerStatus.GRADING_FAILED.value
                            else None
                        ),
                        "retry_available": (
                            answer.status == AnswerStatus.GRADING_FAILED.value
                            and answer.grading_attempt_count < _grading_call_limit(answer)
                        ),
                    },
                    "result": result,
                    "grading_issue_report": (
                        {
                            "id": report.id,
                            "status": report.status,
                            "received": True,
                            "created_at": report.created_at,
                        }
                        if report is not None
                        else None
                    ),
                }
            )
        answer_states = [answers[item.id].status for item, _ in items if item.id in answers]
        if not answer_states:
            grading_state = "NOT_STARTED"
        elif any(state == AnswerStatus.AWAITING_GRADING.value for state in answer_states):
            grading_state = "AWAITING_GRADING"
        elif any(state == AnswerStatus.GRADING_FAILED.value for state in answer_states):
            retry_available = any(
                answer.status == AnswerStatus.GRADING_FAILED.value
                and answer.grading_attempt_count < _grading_call_limit(answer)
                for answer in answers.values()
            )
            grading_state = "RETRY_REQUIRED" if retry_available else "EXHAUSTED"
        elif len(answer_states) == len(items):
            grading_state = "COMPLETE"
        else:
            grading_state = "AWAITING_GRADING"
        return {
            "id": session.id,
            "activity_id": activity.id,
            "decision_id": activity.decision_id,
            "lesson_id": activity.lesson_id,
            "assessment_type": session.assessment_type,
            "submission_state": session.status,
            "grading_state": grading_state,
            "submitted_at": session.submitted_at,
            "graded_answer_count": sum(
                1 for answer in answers.values() if answer.status == AnswerStatus.GRADED.value
            ),
            "unresolved_answer_count": len(items)
            - sum(1 for answer in answers.values() if answer.status == AnswerStatus.GRADED.value),
            "concept_progress_reference_at": session.submitted_at,
            "concept_progress": (
                self.mastery.get_assessment_concept_progress(
                    course_id, owner_id, session.id, session.submitted_at
                )
                if session.status == AssessmentStatus.SUBMITTED.value and session.submitted_at is not None
                else None
            ),
            "questions": question_out,
        }

    def get_learning_state(self, course_id: UUID, owner_id: int) -> dict:
        course = self._owned_course(course_id, owner_id)
        version_id = course.active_version_id
        lessons = (
            self.db.query(Lesson)
            .join(Module, Module.id == Lesson.module_id)
            .filter(Module.course_version_id == version_id)
            .order_by(Module.position, Lesson.position)
            .all()
        )
        covered_ids = {
            row[0]
            for row in self.db.query(LearningActivity.lesson_id)
            .filter(
                LearningActivity.owner_id == owner_id,
                LearningActivity.course_id == course_id,
                LearningActivity.course_version_id == version_id,
                LearningActivity.reading_completed_at.isnot(None),
                LearningActivity.lesson_id.isnot(None),
            )
            .all()
        }
        active = self._active_activity(course_id, owner_id)
        if active is not None:
            self._reconcile_submitted_activity(active, course_id, owner_id)
            if active.status == ActivityStatus.COMPLETED.value:
                active = None
        try:
            understanding = self.mastery.get_mastery_report(course_id, owner_id)
        except MasteryNotFound as exc:
            raise LearningNotFound(str(course_id)) from exc
        return {
            "active_activity": self._activity_out(active) if active is not None else None,
            "lesson_coverage": {
                "course_version_id": version_id,
                "lessons_total": len(lessons),
                "lessons_covered": len(covered_ids),
                "covered_lesson_ids": sorted(covered_ids, key=str),
            },
            "concept_understanding": understanding,
        }
