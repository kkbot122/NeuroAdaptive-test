"""Owner-scoped subjects and version-pinned cross-course concept provenance."""
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Uuid, UniqueConstraint
from sqlalchemy.sql import func

from app.db.base import Base

P8_OPTIONAL_LINK_CHECK_REASON = "Optional prerequisite check for a possible match to an earlier course."


class CourseSubject(Base):
    __tablename__ = "course_subjects"
    __table_args__ = (UniqueConstraint("owner_id", "normalized_name", name="uq_course_subject_owner_name"),)

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    normalized_name = Column(String(120), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CrossCourseConceptMatch(Base):
    """Auditable one-hop mapping; evidence remains on its original concept."""
    __tablename__ = "cross_course_concept_matches"
    __table_args__ = (
        UniqueConstraint(
            "current_course_id", "current_version_id", "current_concept_id",
            "linked_course_id", "linked_version_id", "linked_concept_id",
            name="uq_cross_course_concept_match_versions",
        ),
    )

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    current_course_id = Column(Uuid, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    current_version_id = Column(Uuid, ForeignKey("course_versions.id", ondelete="CASCADE"), nullable=False)
    current_concept_id = Column(Uuid, ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False, index=True)
    linked_course_id = Column(Uuid, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    linked_version_id = Column(Uuid, ForeignKey("course_versions.id", ondelete="CASCADE"), nullable=False)
    linked_concept_id = Column(Uuid, ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(16), nullable=False)  # RELIABLE | UNCERTAIN | UNSUPPORTED
    provenance = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
