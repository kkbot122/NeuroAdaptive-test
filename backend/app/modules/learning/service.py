"""Durable learning activity and fixed assessment lifecycle."""
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.adaptation.models import AdaptationDecision
from app.modules.adaptation.service import AdaptationNotFound, AdaptationService
from app.modules.courses.models import Course, CourseStatus
from app.modules.courses.service import CourseNotFound, CourseService
from app.modules.curriculum.models import Concept, CourseVersion, Lesson, LessonConcept, Module
from app.modules.learning.models import (
    ActivityStatus,
    AnswerStatus,
    AnswerSubmission,
    AssessmentQuestion,
    AssessmentSession,
    AssessmentStatus,
    AssessmentType,
    LearningActivity,
)
from app.modules.learning.activity_types import (
    P2_TEACHING_ACTIVITY_TYPES,
    PREPARED_ACTIVITY_TYPES,
    activity_includes_teaching,
)
from app.modules.mastery.grading import grade_attempt
from app.modules.mastery.models import Question, QuestionAttempt, QuestionConcept
from app.modules.mastery.service import MasteryNotFound, MasteryService
from app.modules.preparation.models import ActivityPreparation, PreparationStatus, PreparedActivityQuestion, QuestionSource
from app.services.embedding.gateway import EmbeddingGateway
from app.services.generation.gateway import GenerationGateway

logger = logging.getLogger(__name__)
MAX_SESSION_QUESTIONS = 50
_LIFECYCLE_LOCKS = tuple(threading.Lock() for _ in range(64))
P2_SUPPORTED_ACTIVITY_TYPES = P2_TEACHING_ACTIVITY_TYPES


class LearningNotFound(Exception):
    """The requested course-scoped learning resource is absent or foreign."""


class LearningConflict(Exception):
    """The requested transition is not legal in the current lifecycle."""


class AssessmentUnavailable(Exception):
    """No persisted question set is available for this activity yet."""


class LearningService:
    def __init__(self, db: Session, generation: GenerationGateway, embeddings: Optional[EmbeddingGateway] = None):
        self.db = db
        self.generation = generation
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
            self._reconcile_activity_status(course_id, session_id, owner_id)
            return self._session_out(session, owner_id, course_id)
        if session.status != AssessmentStatus.OPEN.value:
            raise LearningConflict("Assessment question set is already submitted")

        answer = AnswerSubmission(
            assessment_question_id=assessment_question.id,
            given_answer=given_answer,
            status=AnswerStatus.AWAITING_GRADING.value,
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
            self._reconcile_activity_status(course_id, session_id, owner_id)
            return self._session_out(session, owner_id, course_id)

        self._grade_saved_answer(answer.id, session, assessment_question, question, owner_id)
        self._reconcile_activity_status(course_id, session_id, owner_id)
        return self._session_out(session, owner_id, course_id)

    def retry_grading(self, course_id: UUID, session_id: UUID, question_id: UUID, owner_id: int) -> dict:
        self._owned_course(course_id, owner_id)
        session = self._owned_session(course_id, session_id, owner_id)
        assessment_question, question = self._session_question(session, question_id, owner_id, course_id)
        answer = (
            self.db.query(AnswerSubmission)
            .filter(AnswerSubmission.assessment_question_id == assessment_question.id)
            .first()
        )
        if answer is None:
            raise LearningConflict("Submit an answer before retrying grading")
        if answer.status != AnswerStatus.GRADED.value:
            self._grade_saved_answer(answer.id, session, assessment_question, question, owner_id)
        self._reconcile_activity_status(course_id, session_id, owner_id)
        return self._session_out(session, owner_id, course_id)

    def _reconcile_activity_status(self, course_id: UUID, session_id: UUID, owner_id: int) -> None:
        # Grading commits independently from assessment submission. Reload under
        # the session lock so either this reconciliation or submit_assessment
        # observes the other's committed state before deriving activity status.
        session = self._owned_session(course_id, session_id, owner_id, lock=True)
        self._sync_activity_status(session)
        self.db.commit()

    def _grade_saved_answer(
        self,
        answer_id: UUID,
        session: AssessmentSession,
        assessment_question: AssessmentQuestion,
        question: Question,
        owner_id: int,
    ) -> None:
        answer = self.db.query(AnswerSubmission).filter(AnswerSubmission.id == answer_id).first()
        if answer is None or answer.status == AnswerStatus.GRADED.value:
            return
        try:
            correctness = grade_attempt(question, answer.given_answer, self.generation)
        except Exception as exc:
            logger.warning("Assessment grading failed (%s)", type(exc).__name__)
            self.db.rollback()
            answer = self.db.query(AnswerSubmission).filter(AnswerSubmission.id == answer_id).first()
            if answer is not None and answer.status != AnswerStatus.GRADED.value:
                answer.status = AnswerStatus.GRADING_FAILED.value
                answer.failure_code = "grading_unavailable"
                self.db.commit()
            return

        try:
            self.mastery.record_graded_attempt(
                question,
                owner_id,
                answer.given_answer,
                correctness,
                assessment_question_id=assessment_question.id,
                question_version=assessment_question.question_version,
                course_version_id=session.course_version_id,
                commit=False,
            )
            answer.status = AnswerStatus.GRADED.value
            answer.failure_code = None
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            # A simultaneous retry may win the unique attempt constraint;
            # the answer and evidence from that transaction are authoritative.
            attempt_exists = (
                self.db.query(QuestionAttempt.id)
                .filter(QuestionAttempt.assessment_question_id == assessment_question.id)
                .first()
            )
            answer = self.db.query(AnswerSubmission).filter(AnswerSubmission.id == answer_id).first()
            if attempt_exists and answer is not None:
                answer.status = AnswerStatus.GRADED.value
                answer.failure_code = None
                self.db.commit()
            else:
                raise

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
        question_out = []
        for assessment_question, question in items:
            answer = answers.get(assessment_question.id)
            attempt = attempts.get(assessment_question.id)
            result = None
            if session.status == AssessmentStatus.SUBMITTED.value:
                source_ids = [
                    row[0]
                    for row in self.db.query(QuestionSource.chunk_id)
                    .filter(QuestionSource.question_id == question.id)
                    .order_by(QuestionSource.chunk_id)
                    .all()
                ]
                result = {
                    "correctness": attempt.correctness if attempt else None,
                    "expected_answer": question.correct_answer,
                    "rubric": question.rubric,
                    "explanation": question.explanation,
                    "source_chunk_ids": source_ids,
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
                    },
                    "result": result,
                }
            )
        answer_states = [answers[item.id].status for item, _ in items if item.id in answers]
        if not answer_states:
            grading_state = "NOT_STARTED"
        elif any(state == AnswerStatus.AWAITING_GRADING.value for state in answer_states):
            grading_state = "AWAITING_GRADING"
        elif any(state == AnswerStatus.GRADING_FAILED.value for state in answer_states):
            grading_state = "RETRY_REQUIRED"
        elif len(answer_states) == len(items):
            grading_state = "COMPLETE"
        else:
            grading_state = "AWAITING_GRADING"
        return {
            "id": session.id,
            "activity_id": activity.id,
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
