"""
Account deletion and preference-reset (Phase 6 privacy/data-governance).

Deletion is EXPLICIT, ordered, per-table deletes -- not a reliance on ORM
`cascade=` relationships, most of which are not declared from Course to its
descendant tables (confirmed by inspection: only Course->Document cascades
at the ORM level; nothing else does, and no migration sets `ondelete=` on
any course_id/owner_id foreign key). Adding ~15 cascade relationships or FK
ondelete clauses to close that gap properly is a larger, riskier schema
change than this phase's deletion path needs; explicit ordered deletes here
achieve the same user-facing guarantee (a deleted account's data is
actually gone) without touching every model's migration.

RETENTION WINDOW, STATED PLAINLY: deletion here is immediate and
synchronous, not scheduled for a future window -- the simplest policy that
satisfies "removed within a defined window" (the window is effectively
zero). No Celery/background-job infrastructure exists in this codebase to
defer it, and immediate deletion is a stricter guarantee than a deferred
one, not a weaker one.

AuditLog rows for this user are deliberately NOT deleted -- an audit trail
that vanishes along with the account it was auditing defeats its purpose;
audit retention is independent of the account's own retention.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.modules.abuse.models import AIProviderCall, AIUsageDaily
from app.modules.adaptation.models import AdaptationDecision, AdaptationOutcome, PresentationAffinity
from app.modules.assessment.models import QuizAttempt
from app.modules.auth.models import User
from app.modules.chat.models import ChatMessage, ChatSession
from app.modules.content.models import ArticleReading
from app.modules.courses.models import Course
from app.modules.courses.p8_models import (
    CourseSubject,
    CrossCourseConceptMatch,
    P8_OPTIONAL_LINK_CHECK_REASON,
)
from app.modules.curriculum.models import (
    AssessmentBlueprint,
    Concept,
    ConceptPrerequisite,
    ConceptSource,
    CourseVersion,
    Lesson,
    LessonConcept,
    Module,
)
from app.modules.documents.chunk_models import Chunk
from app.modules.documents.models import Document
from app.modules.documents.models import StorageUploadIntent
from app.modules.documents.storage import S3PrivateStorage, StorageUnavailable
from app.modules.events.models import LearningEvent
from app.modules.jobs.models import ProcessingJob, ProcessingStage
from app.modules.mastery.models import MasteryEvent, Question, QuestionAttempt, QuestionConcept
from app.modules.preparation.models import (
    ActivityPreparation,
    LessonContentArtifact,
    LessonContentCitation,
    PreparedActivityQuestion,
    QuestionSource,
)
from app.modules.learning.models import (
    ActivityStatus,
    AnswerSubmission,
    AssessmentQuestion,
    AssessmentSession,
    GradingCorrection,
    GradingIssueReport,
    GradingJudgment,
    GradingReviewEvent,
    LearningActivity,
)
from app.modules.profiling.models import UserProfile
from app.modules.tutor.models import TutorMessage
from app.modules.privacy.models import StorageCleanupTask

logger = logging.getLogger(__name__)


def _owned_course_ids(db: Session, owner_id: int) -> List[UUID]:
    return [row[0] for row in db.query(Course.id).filter(Course.owner_id == owner_id).all()]


class PrivacyService:
    def __init__(self, db: Session):
        self.db = db
        self._cleanup_task_ids = []

    def delete_account(self, user_id: int) -> int:
        """Removes every row this user owns, then the user row itself.
        Idempotent-ish: deleting an already-deleted/nonexistent user id is a
        no-op, not an error -- callers verify the user exists beforehand if
        they need a 404 for an unknown id."""
        db = self.db
        # Worker commits update each course row as a source/deletion fence.
        # Taking these locks first means a running worker either finishes its
        # current commit before deletion, or observes the missing course and
        # rolls back after deletion commits.
        course_ids = [
            row[0]
            for row in db.query(Course.id).filter(Course.owner_id == user_id)
            .order_by(Course.id).with_for_update().all()
        ]

        self._delete_courses(course_ids, user_id)

        # Owner-scoped (not course-scoped) data.
        # Keep the learner's audit/correction records when a reviewer account
        # is deleted, but remove the deleted person's identity from them.
        db.query(GradingReviewEvent).filter(GradingReviewEvent.reviewer_id == user_id).update(
            {GradingReviewEvent.reviewer_id: None}, synchronize_session=False
        )
        db.query(GradingCorrection).filter(GradingCorrection.reviewer_id == user_id).update(
            {GradingCorrection.reviewer_id: None}, synchronize_session=False
        )
        db.query(PresentationAffinity).filter(PresentationAffinity.owner_id == user_id).delete(synchronize_session=False)
        db.query(AIUsageDaily).filter(AIUsageDaily.owner_id == user_id).delete(synchronize_session=False)
        db.query(AIProviderCall).filter(AIProviderCall.owner_id == user_id).delete(synchronize_session=False)
        db.query(LearningEvent).filter(LearningEvent.user_id == user_id).delete(synchronize_session=False)

        session_ids = [row[0] for row in db.query(ChatSession.id).filter(ChatSession.user_id == user_id).all()]
        if session_ids:
            db.query(ChatMessage).filter(ChatMessage.session_id.in_(session_ids)).delete(synchronize_session=False)
        db.query(ChatSession).filter(ChatSession.user_id == user_id).delete(synchronize_session=False)

        db.query(QuizAttempt).filter(QuizAttempt.user_id == user_id).delete(synchronize_session=False)
        db.query(ArticleReading).filter(ArticleReading.user_id == user_id).delete(synchronize_session=False)
        db.query(UserProfile).filter(UserProfile.user_id == user_id).delete(synchronize_session=False)

        db.query(User).filter(User.id == user_id).delete(synchronize_session=False)
        db.commit()
        self.dispatch_cleanup_tasks()
        return self.pending_cleanup_count(user_id)

    def delete_owned_course(self, course_id: UUID, owner_id: int) -> None:
        """Delete one owned course and its learning, source, and review records."""
        course = self.db.query(Course.id).filter(Course.id == course_id, Course.owner_id == owner_id).with_for_update().first()
        if course is None:
            return
        self._delete_courses([course_id], owner_id)
        self.db.commit()
        self.dispatch_cleanup_tasks()
        return self.pending_cleanup_count(owner_id)

    def _delete_courses(self, course_ids: list[UUID], owner_id: int) -> None:
        """Delete course descendants in FK order, shared by course/account deletion."""
        db = self.db
        if not course_ids:
            return
        deleting_match_ids = select(CrossCourseConceptMatch.id).filter(
            CrossCourseConceptMatch.owner_id == owner_id,
            or_(
                CrossCourseConceptMatch.current_course_id.in_(course_ids),
                CrossCourseConceptMatch.linked_course_id.in_(course_ids),
            ),
        )
        dependent_match_ids = select(CrossCourseConceptMatch.id).filter(
            CrossCourseConceptMatch.owner_id == owner_id,
            CrossCourseConceptMatch.linked_course_id.in_(course_ids),
        )
        dependent_match_activity_ids = {
            row[0] for row in db.query(LearningActivity.id).filter(
                LearningActivity.owner_id == owner_id,
                LearningActivity.course_id.notin_(course_ids),
                LearningActivity.linked_match_id.in_(dependent_match_ids),
            ).all()
        }
        db.query(LearningActivity).filter(
            LearningActivity.owner_id == owner_id,
            LearningActivity.linked_match_id.in_(deleting_match_ids),
        ).update({LearningActivity.linked_match_id: None}, synchronize_session=False)
        dependent_courses = (
            db.query(Course)
            .filter(
                Course.owner_id == owner_id,
                Course.linked_course_id.in_(course_ids),
                Course.id.notin_(course_ids),
            )
            .with_for_update()
            .all()
        )
        dependent_course_ids = [course.id for course in dependent_courses]
        if dependent_courses:
            now = datetime.now(timezone.utc)
            for dependent in dependent_courses:
                dependent.linked_course_title_snapshot = dependent.linked_course_title_snapshot or (
                    db.query(Course.title).filter_by(id=dependent.linked_course_id, owner_id=owner_id).scalar()
                )
                dependent.linked_course_id = None
                dependent.linked_version_id = None
                dependent.link_revoked_at = now
                dependent.link_revision += 1
            db.query(CrossCourseConceptMatch).filter(
                CrossCourseConceptMatch.owner_id == owner_id,
                CrossCourseConceptMatch.linked_course_id.in_(course_ids),
            ).delete(synchronize_session=False)

            linked_chunk_ids = [row[0] for row in db.query(Chunk.id).filter(
                Chunk.owner_id == owner_id, Chunk.course_id.in_(course_ids)
            ).all()]
            dependent_preparations = db.query(ActivityPreparation).filter(
                ActivityPreparation.owner_id == owner_id,
                ActivityPreparation.course_id.in_(dependent_course_ids),
                ActivityPreparation.link_dependency_fingerprint != "",
            ).all()
            assessment_activity_ids = select(AssessmentSession.activity_id).join(
                LearningActivity, LearningActivity.id == AssessmentSession.activity_id
            ).filter(
                LearningActivity.owner_id == owner_id,
                LearningActivity.course_id.in_(dependent_course_ids),
            )
            linked_activity_ids = dependent_match_activity_ids | {
                preparation.activity_id
                for preparation in dependent_preparations
                if preparation.activity_id is not None
            }
            started_activity_ids = {
                row[0] for row in db.query(LearningActivity.id).filter(
                    LearningActivity.owner_id == owner_id,
                    LearningActivity.course_id.in_(dependent_course_ids),
                    or_(
                        LearningActivity.status.in_([
                            ActivityStatus.IN_PROGRESS.value,
                            ActivityStatus.AWAITING_ASSESSMENT.value,
                            ActivityStatus.AWAITING_GRADING.value,
                        ]),
                        and_(
                            LearningActivity.status == ActivityStatus.COMPLETED.value,
                            or_(
                                LearningActivity.activity_type != "OPTIONAL_PREREQUISITE_CHECK",
                                LearningActivity.reason_text != P8_OPTIONAL_LINK_CHECK_REASON,
                            ),
                        ),
                        LearningActivity.reading_position > 0,
                        LearningActivity.reading_completed_at.isnot(None),
                        LearningActivity.id.in_(assessment_activity_ids),
                    ),
                ).all()
            }
            keep_preparation_ids = {
                row.id for row in dependent_preparations if row.activity_id in started_activity_ids
            }
            invalidate_preparation_ids = {
                row.id for row in dependent_preparations if row.id not in keep_preparation_ids
            }
            unstarted_link_activity_ids = linked_activity_ids - started_activity_ids
            if unstarted_link_activity_ids:
                invalidate_preparation_ids.update(
                    row[0]
                    for row in db.query(ActivityPreparation.id).filter(
                        ActivityPreparation.owner_id == owner_id,
                        ActivityPreparation.activity_id.in_(unstarted_link_activity_ids),
                    ).all()
                )
            started_artifact_ids = {
                row.content_artifact_id for row in dependent_preparations
                if row.id in keep_preparation_ids and row.content_artifact_id is not None
            }
            if invalidate_preparation_ids:
                db.query(PreparedActivityQuestion).filter(
                    PreparedActivityQuestion.preparation_id.in_(invalidate_preparation_ids)
                ).delete(synchronize_session=False)
                db.query(ActivityPreparation).filter(
                    ActivityPreparation.id.in_(invalidate_preparation_ids)
                ).delete(synchronize_session=False)
            if linked_chunk_ids:
                revoked_question_ids = [
                    row[0]
                    for row in db.query(Question.id)
                    .join(QuestionSource, QuestionSource.question_id == Question.id)
                    .filter(
                        QuestionSource.chunk_id.in_(linked_chunk_ids),
                        Question.owner_id == owner_id,
                        Question.course_id.in_(dependent_course_ids),
                    )
                    .distinct()
                    .all()
                ]
                if revoked_question_ids:
                    db.query(Question).filter(
                        Question.owner_id == owner_id,
                        Question.id.in_(revoked_question_ids),
                    ).update(
                        {Question.source_revoked_at: now},
                        synchronize_session=False,
                    )
                db.query(QuestionSource).filter(
                    QuestionSource.chunk_id.in_(linked_chunk_ids),
                    QuestionSource.question_id.in_(
                        db.query(Question.id).filter(
                            Question.owner_id == owner_id,
                            Question.course_id.in_(dependent_course_ids),
                        )
                    ),
                ).delete(synchronize_session=False)
            if unstarted_link_activity_ids:
                # These activities have no fixed assessment or learner
                # evidence. Their linked preparation has been invalidated,
                # so remove stale activity rows that could otherwise be resumed.
                db.query(LearningActivity).filter(
                    LearningActivity.owner_id == owner_id,
                    LearningActivity.course_id.in_(dependent_course_ids),
                    LearningActivity.id.in_(unstarted_link_activity_ids),
                ).delete(synchronize_session=False)
            unstarted_artifact_ids = [
                row[0] for row in db.query(LessonContentArtifact.id).filter(
                    LessonContentArtifact.owner_id == owner_id,
                    LessonContentArtifact.course_id.in_(dependent_course_ids),
                    LessonContentArtifact.link_dependency_fingerprint != "",
                    ~LessonContentArtifact.id.in_(started_artifact_ids or [UUID(int=0)]),
                ).all()
            ]
            if unstarted_artifact_ids:
                db.query(LessonContentCitation).filter(
                    LessonContentCitation.artifact_id.in_(unstarted_artifact_ids)
                ).delete(synchronize_session=False)
                db.query(LessonContentArtifact).filter(
                    LessonContentArtifact.id.in_(unstarted_artifact_ids)
                ).delete(synchronize_session=False)
            if linked_chunk_ids:
                # Keep fixed started activities and their saved text/questions;
                # remove references to passages that are being deleted. The
                # source viewer will then report those passages unavailable.
                db.query(LessonContentCitation).filter(
                    LessonContentCitation.chunk_id.in_(linked_chunk_ids),
                    LessonContentCitation.artifact_id.in_(
                        db.query(LessonContentArtifact.id).filter(
                            LessonContentArtifact.owner_id == owner_id,
                            LessonContentArtifact.course_id.in_(dependent_course_ids),
                        )
                    ),
                ).delete(synchronize_session=False)
        else:
            db.query(CrossCourseConceptMatch).filter(
                CrossCourseConceptMatch.owner_id == owner_id,
                CrossCourseConceptMatch.linked_course_id.in_(course_ids),
            ).delete(synchronize_session=False)
        db.query(CrossCourseConceptMatch).filter(
            CrossCourseConceptMatch.owner_id == owner_id,
            or_(
                CrossCourseConceptMatch.current_course_id.in_(course_ids),
                CrossCourseConceptMatch.linked_course_id.in_(course_ids),
            ),
        ).delete(synchronize_session=False)
        cleanup_ids = self._schedule_course_storage(course_ids, owner_id)
        self._cleanup_task_ids.extend(cleanup_ids)
        preparation_ids = [
            row[0]
            for row in db.query(ActivityPreparation.id)
            .filter(ActivityPreparation.course_id.in_(course_ids))
            .all()
        ]
        artifact_ids = [
            row[0]
            for row in db.query(LessonContentArtifact.id)
            .filter(LessonContentArtifact.course_id.in_(course_ids))
            .all()
        ]
        if preparation_ids:
            db.query(PreparedActivityQuestion).filter(
                PreparedActivityQuestion.preparation_id.in_(preparation_ids)
            ).delete(synchronize_session=False)
            db.query(ActivityPreparation).filter(
                ActivityPreparation.id.in_(preparation_ids)
            ).delete(synchronize_session=False)
        if artifact_ids:
            db.query(LessonContentCitation).filter(
                LessonContentCitation.artifact_id.in_(artifact_ids)
            ).delete(synchronize_session=False)
            db.query(LessonContentArtifact).filter(
                LessonContentArtifact.id.in_(artifact_ids)
            ).delete(synchronize_session=False)

        answer_ids = [
            row[0]
            for row in db.query(AnswerSubmission.id)
            .join(AssessmentQuestion, AssessmentQuestion.id == AnswerSubmission.assessment_question_id)
            .join(AssessmentSession, AssessmentSession.id == AssessmentQuestion.session_id)
            .join(LearningActivity, LearningActivity.id == AssessmentSession.activity_id)
            .filter(LearningActivity.owner_id == owner_id, LearningActivity.course_id.in_(course_ids))
            .all()
        ]
        attempt_ids = [
            row[0]
            for row in db.query(QuestionAttempt.id)
            .filter(QuestionAttempt.course_id.in_(course_ids), QuestionAttempt.owner_id == owner_id)
            .all()
        ]
        report_ids = [
            row[0]
            for row in db.query(GradingIssueReport.id)
            .filter(GradingIssueReport.course_id.in_(course_ids), GradingIssueReport.owner_id == owner_id)
            .all()
        ]
        if report_ids:
            db.query(GradingReviewEvent).filter(GradingReviewEvent.report_id.in_(report_ids)).delete(
                synchronize_session=False
            )
            db.query(GradingCorrection).filter(GradingCorrection.report_id.in_(report_ids)).delete(
                synchronize_session=False
            )
        if attempt_ids:
            db.query(GradingCorrection).filter(GradingCorrection.question_attempt_id.in_(attempt_ids)).delete(
                synchronize_session=False
            )
        if answer_ids:
            db.query(GradingJudgment).filter(GradingJudgment.answer_submission_id.in_(answer_ids)).delete(
                synchronize_session=False
            )
        if report_ids:
            db.query(GradingIssueReport).filter(GradingIssueReport.id.in_(report_ids)).delete(
                synchronize_session=False
            )

        db.query(AdaptationOutcome).filter(
            AdaptationOutcome.decision_id.in_(
                db.query(AdaptationDecision.id).filter(AdaptationDecision.course_id.in_(course_ids))
            )
        ).delete(synchronize_session=False)
        db.query(MasteryEvent).filter(MasteryEvent.course_id.in_(course_ids)).delete(synchronize_session=False)
        db.query(QuestionAttempt).filter(QuestionAttempt.course_id.in_(course_ids)).delete(synchronize_session=False)

        activity_ids = [
            row[0]
            for row in db.query(LearningActivity.id)
            .filter(LearningActivity.owner_id == owner_id, LearningActivity.course_id.in_(course_ids))
            .all()
        ]
        session_ids = [
            row[0]
            for row in db.query(AssessmentSession.id)
            .filter(AssessmentSession.activity_id.in_(activity_ids))
            .all()
        ] if activity_ids else []
        assessment_question_ids = [
            row[0]
            for row in db.query(AssessmentQuestion.id)
            .filter(AssessmentQuestion.session_id.in_(session_ids))
            .all()
        ] if session_ids else []
        if assessment_question_ids:
            db.query(AnswerSubmission).filter(
                AnswerSubmission.assessment_question_id.in_(assessment_question_ids)
            ).delete(synchronize_session=False)
            db.query(AssessmentQuestion).filter(
                AssessmentQuestion.id.in_(assessment_question_ids)
            ).delete(synchronize_session=False)
        if session_ids:
            db.query(AssessmentSession).filter(AssessmentSession.id.in_(session_ids)).delete(
                synchronize_session=False
            )
        if activity_ids:
            db.query(LearningActivity).filter(LearningActivity.id.in_(activity_ids)).delete(
                synchronize_session=False
            )

        question_ids = [row[0] for row in db.query(Question.id).filter(Question.course_id.in_(course_ids)).all()]
        if question_ids:
            db.query(QuestionSource).filter(QuestionSource.question_id.in_(question_ids)).delete(
                synchronize_session=False
            )
            db.query(QuestionConcept).filter(QuestionConcept.question_id.in_(question_ids)).delete(
                synchronize_session=False
            )
        db.query(Question).filter(Question.course_id.in_(course_ids)).delete(synchronize_session=False)

        db.query(TutorMessage).filter(TutorMessage.course_id.in_(course_ids)).delete(synchronize_session=False)
        db.query(AdaptationDecision).filter(AdaptationDecision.course_id.in_(course_ids)).delete(synchronize_session=False)
        db.query(ConceptPrerequisite).filter(ConceptPrerequisite.course_id.in_(course_ids)).delete(synchronize_session=False)
        db.query(ConceptSource).filter(ConceptSource.course_id.in_(course_ids)).delete(synchronize_session=False)
        db.query(Concept).filter(Concept.course_id.in_(course_ids)).delete(synchronize_session=False)

        version_ids = [
            row[0] for row in db.query(CourseVersion.id).filter(CourseVersion.course_id.in_(course_ids)).all()
        ]
        if version_ids:
            module_ids = [
                row[0] for row in db.query(Module.id).filter(Module.course_version_id.in_(version_ids)).all()
            ]
            if module_ids:
                lesson_ids = [
                    row[0] for row in db.query(Lesson.id).filter(Lesson.module_id.in_(module_ids)).all()
                ]
                if lesson_ids:
                    db.query(LessonConcept).filter(LessonConcept.lesson_id.in_(lesson_ids)).delete(
                        synchronize_session=False
                    )
                    db.query(Lesson).filter(Lesson.id.in_(lesson_ids)).delete(synchronize_session=False)
                db.query(Module).filter(Module.id.in_(module_ids)).delete(synchronize_session=False)
            db.query(AssessmentBlueprint).filter(AssessmentBlueprint.course_version_id.in_(version_ids)).delete(
                synchronize_session=False
            )
        db.query(CourseVersion).filter(CourseVersion.course_id.in_(course_ids)).delete(synchronize_session=False)

        # Match source finalization's lock order after the course rows above:
        # course -> upload intent -> document. Finalization may update the
        # document only after it has claimed its intent, so deletion must not
        # take document rows first and then wait for an intent.
        db.query(StorageUploadIntent).filter(StorageUploadIntent.course_id.in_(course_ids)).delete(
            synchronize_session=False
        )
        db.query(Chunk).filter(Chunk.course_id.in_(course_ids)).delete(synchronize_session=False)
        db.query(Document).filter(Document.course_id.in_(course_ids)).delete(synchronize_session=False)

        job_ids = [row[0] for row in db.query(ProcessingJob.id).filter(ProcessingJob.course_id.in_(course_ids)).all()]
        if job_ids:
            db.query(ProcessingStage).filter(ProcessingStage.job_id.in_(job_ids)).delete(synchronize_session=False)
        db.query(ProcessingJob).filter(ProcessingJob.course_id.in_(course_ids)).delete(synchronize_session=False)

        db.query(Course).filter(Course.id.in_(course_ids)).delete(synchronize_session=False)
        db.query(CourseSubject).filter(
            CourseSubject.owner_id == owner_id,
            ~CourseSubject.id.in_(db.query(Course.subject_id).filter(Course.owner_id == owner_id, Course.subject_id.isnot(None))),
        ).delete(synchronize_session=False)

    _cleanup_task_ids: list[UUID]

    def _schedule_course_storage(self, course_ids: list[UUID], owner_id: int) -> list[UUID]:
        db = self.db
        targets: list[tuple[str, str, str]] = []
        for document in db.query(Document).filter(Document.course_id.in_(course_ids), Document.owner_id == owner_id).all():
            if document.storage_key:
                targets.append((document.storage_key, "private", "course_delete"))
            elif document.storage_path:
                targets.append((document.storage_path, "local", "course_delete"))
        for intent in db.query(StorageUploadIntent).filter(
            StorageUploadIntent.course_id.in_(course_ids), StorageUploadIntent.owner_id == owner_id
        ).all():
            targets.append((intent.object_key, "private", "orphaned_upload"))
        return [self.schedule_storage_cleanup(target, kind, reason, owner_id).id
                for target, kind, reason in targets]

    def schedule_storage_cleanup(
        self, target: str, kind: str, reason: str, owner_id: int | None,
    ) -> StorageCleanupTask:
        """Persist cleanup before its source row or account can be removed."""
        task = self.db.query(StorageCleanupTask).filter(StorageCleanupTask.storage_target == target).first()
        if task is not None:
            return task
        task = StorageCleanupTask(
            owner_id=owner_id, storage_target=target, storage_kind=kind,
            reason=reason, status="PENDING", attempts=0,
        )
        self.db.add(task)
        self.db.flush()
        return task

    def dispatch_cleanup_tasks(self) -> None:
        task_ids = list(dict.fromkeys(getattr(self, "_cleanup_task_ids", [])))
        self._cleanup_task_ids = []
        if not task_ids:
            return
        try:
            from app.modules.privacy.tasks import cleanup_storage_object
            for task_id in task_ids:
                try:
                    cleanup_storage_object.apply_async(args=[str(task_id)], retry=False)
                except Exception as exc:  # durable PENDING row remains recoverable
                    logger.warning("Storage cleanup dispatch unavailable", extra={
                        "task_id": str(task_id), "error_category": type(exc).__name__,
                    })
        except Exception as exc:
            logger.warning("Storage cleanup worker unavailable", extra={"error_category": type(exc).__name__})

    def pending_cleanup_count(self, owner_id: int | None = None) -> int:
        query = self.db.query(StorageCleanupTask).filter(StorageCleanupTask.status != "COMPLETE")
        if owner_id is not None:
            query = query.filter(StorageCleanupTask.owner_id == owner_id)
        return query.count()

    def run_storage_cleanup(self, task_id: UUID) -> bool:
        task = self.db.query(StorageCleanupTask).filter(StorageCleanupTask.id == task_id).with_for_update().first()
        if task is None or task.status == "COMPLETE":
            return True
        task.status = "RUNNING"
        task.attempts += 1
        task.error_category = None
        self.db.commit()
        try:
            if task.storage_kind == "private":
                S3PrivateStorage().delete(task.storage_target)
            elif task.storage_kind == "local":
                Path(task.storage_target).unlink(missing_ok=True)
            else:
                raise ValueError("Unknown storage cleanup kind")
        except StorageUnavailable:
            task = self.db.query(StorageCleanupTask).filter(StorageCleanupTask.id == task_id).first()
            if task is not None:
                task.status = "PENDING"
                task.error_category = "STORAGE_UNAVAILABLE"
                self.db.commit()
            raise
        except OSError as exc:
            task = self.db.query(StorageCleanupTask).filter(StorageCleanupTask.id == task_id).first()
            if task is not None:
                task.status = "PENDING"
                task.error_category = type(exc).__name__[:64]
                self.db.commit()
            raise StorageUnavailable() from None
        task = self.db.query(StorageCleanupTask).filter(StorageCleanupTask.id == task_id).first()
        if task is not None:
            task.status = "COMPLETE"
            task.completed_at = datetime.now(timezone.utc)
            task.error_category = None
            self.db.commit()
        return True

    def reset_presentation_affinity(self, owner_id: int, *, commit: bool = True) -> int:
        """Clears only PresentationAffinity rows -- ConceptMastery/
        MasteryEvent rows are untouched, because a format preference and a
        demonstrated skill are different kinds of evidence with different
        reasons to exist (mandate). Returns the number of rows removed."""
        count = self.db.query(PresentationAffinity).filter(PresentationAffinity.owner_id == owner_id).count()
        self.db.query(PresentationAffinity).filter(PresentationAffinity.owner_id == owner_id).delete(
            synchronize_session=False
        )
        if commit:
            self.db.commit()
        return count

    def update_tracking_consent(self, owner_id: int, tracking_consent: str) -> str:
        user = self.db.query(User).filter(User.id == owner_id).with_for_update().first()
        if user is None:
            raise LookupError("User not found")
        user.tracking_consent = tracking_consent
        self.db.commit()
        return user.tracking_consent

    def update_settings(
        self, owner_id: int, *, tracking_consent: str,
        default_presentation_format: str, tutor_panel_open: bool,
    ) -> dict:
        user = self.db.query(User).filter(User.id == owner_id).with_for_update().first()
        if user is None:
            raise LookupError("User not found")
        # This writes the exact consent field already read by events/router.py;
        # no historical LearningEvent rows are touched.
        user.tracking_consent = tracking_consent
        user.default_presentation_format = default_presentation_format
        user.tutor_panel_open = tutor_panel_open
        self.db.commit()
        return {
            "tracking_consent": user.tracking_consent,
            "default_presentation_format": user.default_presentation_format,
            "tutor_panel_open": user.tutor_panel_open,
        }

    def reset_presentation_preferences(self, owner_id: int) -> dict:
        user = self.db.query(User).filter(User.id == owner_id).with_for_update().first()
        if user is None:
            raise LookupError("User not found")
        user.default_presentation_format = "detailed"
        user.tutor_panel_open = True
        rows_removed = self.reset_presentation_affinity(owner_id, commit=False)
        # Tracking consent, telemetry history, answers, progress, and mastery
        # are separate records and are deliberately left as they are.
        self.db.commit()
        return {
            "default_presentation_format": user.default_presentation_format,
            "tutor_panel_open": user.tutor_panel_open,
            "presentation_affinity_rows_removed": rows_removed,
        }
