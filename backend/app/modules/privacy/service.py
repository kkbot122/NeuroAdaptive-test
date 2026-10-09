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
from typing import List
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.abuse.models import AIProviderCall, AIUsageDaily
from app.modules.adaptation.models import AdaptationDecision, AdaptationOutcome, PresentationAffinity
from app.modules.assessment.models import QuizAttempt
from app.modules.auth.models import User
from app.modules.chat.models import ChatMessage, ChatSession
from app.modules.content.models import ArticleReading
from app.modules.courses.models import Course
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


def _owned_course_ids(db: Session, owner_id: int) -> List[UUID]:
    return [row[0] for row in db.query(Course.id).filter(Course.owner_id == owner_id).all()]


class PrivacyService:
    def __init__(self, db: Session):
        self.db = db

    def delete_account(self, user_id: int) -> None:
        """Removes every row this user owns, then the user row itself.
        Idempotent-ish: deleting an already-deleted/nonexistent user id is a
        no-op, not an error -- callers verify the user exists beforehand if
        they need a 404 for an unknown id."""
        db = self.db
        course_ids = _owned_course_ids(db, user_id)

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

    def delete_owned_course(self, course_id: UUID, owner_id: int) -> None:
        """Delete one owned course and its learning, source, and review records."""
        course = self.db.query(Course.id).filter(Course.id == course_id, Course.owner_id == owner_id).first()
        if course is None:
            return
        self._delete_courses([course_id], owner_id)
        self.db.commit()

    def _delete_courses(self, course_ids: list[UUID], owner_id: int) -> None:
        """Delete course descendants in FK order, shared by course/account deletion."""
        db = self.db
        if not course_ids:
            return
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

        db.query(Chunk).filter(Chunk.course_id.in_(course_ids)).delete(synchronize_session=False)
        db.query(Document).filter(Document.course_id.in_(course_ids)).delete(synchronize_session=False)

        job_ids = [row[0] for row in db.query(ProcessingJob.id).filter(ProcessingJob.course_id.in_(course_ids)).all()]
        if job_ids:
            db.query(ProcessingStage).filter(ProcessingStage.job_id.in_(job_ids)).delete(synchronize_session=False)
        db.query(ProcessingJob).filter(ProcessingJob.course_id.in_(course_ids)).delete(synchronize_session=False)

        db.query(Course).filter(Course.id.in_(course_ids)).delete(synchronize_session=False)

    def reset_presentation_affinity(self, owner_id: int) -> int:
        """Clears only PresentationAffinity rows -- ConceptMastery/
        MasteryEvent rows are untouched, because a format preference and a
        demonstrated skill are different kinds of evidence with different
        reasons to exist (mandate). Returns the number of rows removed."""
        count = self.db.query(PresentationAffinity).filter(PresentationAffinity.owner_id == owner_id).count()
        self.db.query(PresentationAffinity).filter(PresentationAffinity.owner_id == owner_id).delete(
            synchronize_session=False
        )
        self.db.commit()
        return count
