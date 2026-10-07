"""Durable activity, fixed assessment, and confirmed answer records."""
import enum
import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.sql import func

from app.db.base import Base


class ActivityStatus(str, enum.Enum):
    SELECTED = "SELECTED"
    PREPARING = "PREPARING"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    AWAITING_ASSESSMENT = "AWAITING_ASSESSMENT"
    AWAITING_GRADING = "AWAITING_GRADING"
    COMPLETED = "COMPLETED"
    RECOVERABLE_FAILURE = "RECOVERABLE_FAILURE"


class AssessmentType(str, enum.Enum):
    DIAGNOSTIC = "DIAGNOSTIC"
    ACTIVITY = "ACTIVITY"


class AssessmentStatus(str, enum.Enum):
    OPEN = "OPEN"
    SUBMITTED = "SUBMITTED"


class AnswerStatus(str, enum.Enum):
    AWAITING_GRADING = "AWAITING_GRADING"
    GRADED = "GRADED"
    GRADING_FAILED = "GRADING_FAILED"


class LearningActivity(Base):
    __tablename__ = "learning_activities"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    course_id = Column(Uuid, ForeignKey("courses.id"), nullable=False, index=True)
    course_version_id = Column(Uuid, ForeignKey("course_versions.id"), nullable=False, index=True)
    decision_id = Column(Uuid, ForeignKey("adaptation_decisions.id"), nullable=True, index=True)

    activity_type = Column(String(32), nullable=False)
    target_concept_ids = Column(JSON, nullable=False, default=list)
    lesson_id = Column(Uuid, ForeignKey("lessons.id"), nullable=True, index=True)
    reason_text = Column(String(500), nullable=True)
    status = Column(String(32), nullable=False, default=ActivityStatus.READY.value, index=True)
    presentation_format = Column(String(32), nullable=False, default="detailed")
    reading_position = Column(Integer, nullable=False, default=0)
    reading_completed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index(
            "uq_learning_activities_one_unfinished_per_course",
            "owner_id",
            "course_id",
            unique=True,
            postgresql_where=(status != ActivityStatus.COMPLETED.value),
            sqlite_where=(status != ActivityStatus.COMPLETED.value),
        ),
    )


class AssessmentSession(Base):
    __tablename__ = "assessment_sessions"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    activity_id = Column(Uuid, ForeignKey("learning_activities.id"), nullable=False)
    course_version_id = Column(Uuid, ForeignKey("course_versions.id"), nullable=False, index=True)
    assessment_type = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False, default=AssessmentStatus.OPEN.value)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("activity_id", name="uq_assessment_sessions_activity_id"),
    )


class AssessmentQuestion(Base):
    __tablename__ = "assessment_questions"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id = Column(Uuid, ForeignKey("assessment_sessions.id"), nullable=False, index=True)
    question_id = Column(Uuid, ForeignKey("questions.id"), nullable=False, index=True)
    question_version = Column(Integer, nullable=False)
    position = Column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("session_id", "question_id", name="uq_assessment_questions_session_question"),
        UniqueConstraint("session_id", "position", name="uq_assessment_questions_session_position"),
    )


class AnswerSubmission(Base):
    __tablename__ = "answer_submissions"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    assessment_question_id = Column(
        Uuid,
        ForeignKey("assessment_questions.id"),
        nullable=False,
    )
    given_answer = Column(JSON, nullable=False)
    status = Column(String(24), nullable=False, default=AnswerStatus.AWAITING_GRADING.value)
    failure_code = Column(String(32), nullable=True)
    submitted_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "assessment_question_id",
            name="uq_answer_submissions_assessment_question_id",
        ),
    )


Index("ix_answer_submissions_status", AnswerSubmission.status)
