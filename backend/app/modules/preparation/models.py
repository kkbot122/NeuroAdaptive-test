"""Durable preparation jobs and source-grounded artifacts."""
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.sql import func

from app.db.base import Base


class PreparationStatus:
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    READY = "READY"
    RECOVERABLE_FAILURE = "RECOVERABLE_FAILURE"


class PreparationStage:
    CONTENT = "CONTENT"
    QUESTIONS = "QUESTIONS"
    COMPLETE = "COMPLETE"


class LessonContentArtifact(Base):
    """Only complete, semantically validated text is stored as an artifact."""

    __tablename__ = "lesson_content_artifacts"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    artifact_key = Column(String(64), nullable=False, unique=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    course_id = Column(Uuid, ForeignKey("courses.id"), nullable=False, index=True)
    course_version_id = Column(Uuid, ForeignKey("course_versions.id"), nullable=False, index=True)
    lesson_id = Column(Uuid, ForeignKey("lessons.id"), nullable=True, index=True)
    activity_purpose = Column(String(32), nullable=False, default="NEW_LESSON")
    target_concept_ids = Column(JSON, nullable=False, default=list)
    source_fingerprint = Column(String(64), nullable=False)
    curriculum_fingerprint = Column(String(64), nullable=False)
    presentation_format = Column(String(32), nullable=False)
    sections = Column(JSON, nullable=False)
    source_chunk_ids = Column(JSON, nullable=False)
    model_id = Column(String(128), nullable=False)
    validation_model_id = Column(String(128), nullable=False)
    prompt_version = Column(String(32), nullable=False)
    schema_version = Column(String(32), nullable=False)
    validation_policy_version = Column(String(32), nullable=False)
    validation_status = Column(String(16), nullable=False, default="PASSED")
    validated_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class LessonContentCitation(Base):
    __tablename__ = "lesson_content_citations"
    __table_args__ = (UniqueConstraint("artifact_id", "chunk_id", name="uq_lesson_content_citation"),)

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    artifact_id = Column(Uuid, ForeignKey("lesson_content_artifacts.id"), nullable=False, index=True)
    chunk_id = Column(Uuid, ForeignKey("chunks.id"), nullable=False, index=True)


class ActivityPreparation(Base):
    """A resumable preparation run; only activity preparations create MCQs."""

    __tablename__ = "activity_preparations"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    preparation_key = Column(String(200), nullable=False, unique=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    course_id = Column(Uuid, ForeignKey("courses.id"), nullable=False, index=True)
    course_version_id = Column(Uuid, ForeignKey("course_versions.id"), nullable=False, index=True)
    activity_id = Column(Uuid, ForeignKey("learning_activities.id"), nullable=True, index=True)
    lesson_id = Column(Uuid, ForeignKey("lessons.id"), nullable=True, index=True)
    activity_purpose = Column(String(32), nullable=False, default="NEW_LESSON")
    target_concept_ids = Column(JSON, nullable=False, default=list)
    presentation_format = Column(String(32), nullable=False)
    include_assessment = Column(Boolean, nullable=False, default=False)
    is_speculative = Column(Boolean, nullable=False, default=False)
    status = Column(String(32), nullable=False, default=PreparationStatus.PENDING, index=True)
    stage = Column(String(24), nullable=False, default=PreparationStage.CONTENT)
    progress = Column(Integer, nullable=False, default=0)
    retry_count = Column(Integer, nullable=False, default=0)
    candidate_count = Column(Integer, nullable=False, default=0)
    provider_call_count = Column(Integer, nullable=False, default=0)
    error_category = Column(String(64), nullable=True)
    artifact_keys = Column(JSON, nullable=False, default=dict)
    content_artifact_id = Column(Uuid, ForeignKey("lesson_content_artifacts.id"), nullable=True)
    lease_token = Column(Uuid, nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    last_dispatched_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)


class PreparedActivityQuestion(Base):
    """Question membership and order fixed for one preparation/activity."""

    __tablename__ = "prepared_activity_questions"
    __table_args__ = (
        UniqueConstraint("preparation_id", "question_id", name="uq_prepared_activity_question"),
        UniqueConstraint("preparation_id", "position", name="uq_prepared_activity_position"),
    )

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    preparation_id = Column(Uuid, ForeignKey("activity_preparations.id"), nullable=False, index=True)
    question_id = Column(Uuid, ForeignKey("questions.id"), nullable=False, index=True)
    question_version = Column(Integer, nullable=False)
    position = Column(Integer, nullable=False)


class QuestionSource(Base):
    """Source provenance for an immutable, generated question version."""

    __tablename__ = "question_sources"
    __table_args__ = (UniqueConstraint("question_id", "chunk_id", name="uq_question_source"),)

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    question_id = Column(Uuid, ForeignKey("questions.id"), nullable=False, index=True)
    chunk_id = Column(Uuid, ForeignKey("chunks.id"), nullable=False, index=True)
