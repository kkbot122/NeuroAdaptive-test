import enum
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class CourseStatus(str, enum.Enum):
    """
    Lifecycle of a course, mirroring frozen-scope.md's processing pipeline
    terminal states. Stored as a string rather than a native enum so the same
    schema works on SQLite in tests and PostgreSQL in production.
    """

    DRAFT = "DRAFT"            # created, sources not finalized
    PROCESSING = "PROCESSING"  # source set finalized, pipeline running
    READY = "READY"            # validated course version published
    NEEDS_INPUT = "NEEDS_INPUT"
    FAILED = "FAILED"


class Course(Base):
    """
    A learner's private course, built from their own uploaded material.

    Distinct from the legacy Article/Paragraph content model, which is
    pre-loaded reading content with no upload path. Nothing here reads from
    that model, or from the FSLSM/archetype profile: per the frozen scope the
    adaptive path is driven by per-concept mastery, not a static learner label.
    """

    __tablename__ = "courses"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    title = Column(String(200), nullable=False)
    goal = Column(Text, nullable=True)

    # Learner self-reported starting confidence, 1-5. Recorded at creation and
    # used as a weak prior only; it is never mastery evidence.
    starting_confidence = Column(Integer, nullable=True)

    status = Column(String(32), nullable=False, default=CourseStatus.DRAFT.value, index=True)

    # Set once the source set is finalized. Documents are immutable thereafter
    # (frozen-scope.md: "Course documents become immutable once the course is
    # created").
    sources_finalized_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    owner = relationship("User")
    documents = relationship(
        "Document", back_populates="course", cascade="all, delete-orphan"
    )

    @property
    def sources_are_immutable(self) -> bool:
        return self.sources_finalized_at is not None
