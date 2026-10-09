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
    REVIEW_READY = "REVIEW_READY"  # validated version awaits explicit publish
    PUBLISHED = "PUBLISHED"        # explicit review action made it learnable
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

    # Marks that source preparation began. Before publication the owner may
    # still replace or remove material; those changes advance source_revision.
    sources_finalized_at = Column(DateTime(timezone=True), nullable=True)

    # Monotonic fence for pre-publication source replacement. Jobs and review
    # versions record this value so an older worker or outline cannot publish
    # after the source set changes.
    source_revision = Column(Integer, nullable=False, default=0, server_default="0")

    # The only field a curriculum publish action changes on this row, and
    # only via CurriculumService.activate_version() after validation passes.
    # No FK constraint declared here (would create a circular table
    # dependency at migration time between courses and course_versions);
    # referential integrity for this pointer is enforced in the service
    # layer, which is also where every other cross-module write in this
    # codebase already lives.
    active_version_id = Column(Uuid, nullable=True)

    # P8 relationships are explicit and directional. A linked version is
    # pinned at course creation; it is never inferred from a shared subject.
    subject_id = Column(Uuid, ForeignKey("course_subjects.id", ondelete="SET NULL"), nullable=True, index=True)
    linked_course_id = Column(Uuid, ForeignKey("courses.id", ondelete="SET NULL"), nullable=True, index=True)
    linked_version_id = Column(Uuid, ForeignKey("course_versions.id", ondelete="SET NULL"), nullable=True)
    linked_course_title_snapshot = Column(String(200), nullable=True)
    linked_version_number = Column(Integer, nullable=True)
    link_revision = Column(Integer, nullable=False, default=0, server_default="0")
    link_revoked_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    owner = relationship("User")
    documents = relationship(
        "Document", back_populates="course", cascade="all, delete-orphan"
    )

    @property
    def sources_are_immutable(self) -> bool:
        return self.status == CourseStatus.PUBLISHED.value or self.active_version_id is not None

    @property
    def sources_are_finalized(self) -> bool:
        return self.sources_finalized_at is not None


# Keep additive P8 tables registered whenever the canonical Course model is
# imported (including test metadata creation and privacy cleanup paths).
from app.modules.courses.p8_models import CourseSubject, CrossCourseConceptMatch  # noqa: E402,F401
