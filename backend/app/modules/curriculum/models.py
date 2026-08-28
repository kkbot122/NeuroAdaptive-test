"""
Concept graph and curriculum domain models.

Own module per AGENTS.md's domain-ownership rule: this is the first phase
adding genuinely new modelling, not extending Phase 1's ingestion module.

CONFLICT WITH THE PHASE 2 PACK, DECLARED: the pack asks for hard prerequisite
edges that "gate readiness" (block access) versus soft edges that only
influence scoring. frozen-scope.md is explicit and repeated: "Prerequisite
weakness produces a warning and influences scoring but does not block access
to a dependent concept" and "Prerequisites always remain eligible but
generate warnings and lower readiness." There is no gating prerequisite in
the frozen product. Per AGENTS.md's authority order, frozen-scope wins:
EdgeStrength.HARD/SOFT is retained as stored metadata (a hard edge pulls the
readiness score down harder when unmet, matching the paper's R(c) = min over
prerequisites), but no code path may use it to block lesson or content
access. See docs/adaptation-spec.md §5 for the readiness function this feeds.
"""
import enum
import uuid

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class CourseVersionStatus(str, enum.Enum):
    DRAFT = "DRAFT"           # generation in progress
    VALIDATING = "VALIDATING"
    READY = "READY"           # validation passed; may be activated
    FAILED = "FAILED"         # validation failed; never activated


class EdgeStrength(str, enum.Enum):
    HARD = "HARD"
    SOFT = "SOFT"


class ConceptRole(str, enum.Enum):
    INTRODUCES = "INTRODUCES"
    REINFORCES = "REINFORCES"
    REVIEWS = "REVIEWS"


class CourseVersion(Base):
    """
    One immutable, versioned snapshot of a course's generated structure.

    Never mutated after creation. Regenerating a course always produces a new
    row; course.active_version_id is the only thing that changes, and only
    through an explicit, validated activation step (CurriculumService).
    """

    __tablename__ = "course_versions"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    course_id = Column(Uuid, ForeignKey("courses.id"), nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    version_number = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False, default=CourseVersionStatus.DRAFT.value)

    # Fingerprint of the document set this version was generated from
    # (e.g. a hash of sorted document checksums), so it's possible to tell
    # whether a stale version was built from sources that have since changed.
    source_fingerprint = Column(String(64), nullable=True)

    # Populated by CurriculumService.validate(): a list of plain-language
    # validation failure reasons. Empty when status is READY.
    validation_errors = Column(JSON, nullable=False, default=list)

    # {new_concept_id: {"from": old_concept_id | None, "status": "carried" |
    # "new"}} -- computed when this version is generated as a regeneration of
    # a previous one. Null for a course's first version.
    concept_carryover_map = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    activated_at = Column(DateTime(timezone=True), nullable=True)

    modules = relationship(
        "Module", back_populates="course_version", cascade="all, delete-orphan",
        order_by="Module.position",
    )


class Concept(Base):
    """
    A small, independently teachable and assessable unit (frozen-scope.md).

    canonical_key is a normalized (lowercased, whitespace-collapsed) slug.
    It is what regeneration uses to carry mastery forward across course
    versions: two concepts with the same canonical_key in different versions
    are treated as the same concept for carryover purposes, before falling
    back to embedding similarity.
    """

    __tablename__ = "concepts"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    course_id = Column(Uuid, ForeignKey("courses.id"), nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    canonical_key = Column(String(200), nullable=False, index=True)
    name = Column(String(300), nullable=False)
    definition = Column(Text, nullable=False)

    # Alternate phrasings merged into this concept during normalization.
    # Retained rather than discarded, per the mandate.
    aliases = Column(JSON, nullable=False, default=list)

    importance = Column(Float, nullable=False, default=0.5)  # [0, 1], unvalidated default
    bloom_level = Column(String(32), nullable=True)  # e.g. "remember", "apply", "analyze"

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    sources = relationship(
        "ConceptSource", back_populates="concept", cascade="all, delete-orphan"
    )


class ConceptSource(Base):
    """
    Provenance: which chunk(s) is this concept actually grounded in.

    This is what makes "source-grounded" a checkable claim rather than a
    description -- every concept must resolve to at least one real, owned
    chunk (enforced by CurriculumService.validate(), not by this table alone).
    """

    __tablename__ = "concept_sources"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    concept_id = Column(Uuid, ForeignKey("concepts.id"), nullable=False, index=True)
    chunk_id = Column(Uuid, ForeignKey("chunks.id"), nullable=False, index=True)
    course_id = Column(Uuid, ForeignKey("courses.id"), nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    concept = relationship("Concept", back_populates="sources")


class ConceptPrerequisite(Base):
    """
    A directed edge: prerequisite_concept_id must be understood before
    dependent_concept_id. Confidence-scored, versioned, always acyclic in
    storage (CurriculumService enforces this before any edge set is saved).

    strength is stored metadata only -- see the module docstring's declared
    conflict. No code path may use HARD to block access.
    """

    __tablename__ = "concept_prerequisites"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    course_id = Column(Uuid, ForeignKey("courses.id"), nullable=False, index=True)
    graph_version = Column(Integer, nullable=False, default=1)

    prerequisite_concept_id = Column(Uuid, ForeignKey("concepts.id"), nullable=False, index=True)
    dependent_concept_id = Column(Uuid, ForeignKey("concepts.id"), nullable=False, index=True)

    strength = Column(String(8), nullable=False, default=EdgeStrength.SOFT.value)
    confidence = Column(Float, nullable=False, default=0.5)  # [0, 1], unvalidated default

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Module(Base):
    __tablename__ = "modules"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    course_version_id = Column(Uuid, ForeignKey("course_versions.id"), nullable=False, index=True)

    position = Column(Integer, nullable=False, default=0)
    title = Column(String(300), nullable=False)

    course_version = relationship("CourseVersion", back_populates="modules")
    lessons = relationship(
        "Lesson", back_populates="module", cascade="all, delete-orphan",
        order_by="Lesson.position",
    )


class Lesson(Base):
    __tablename__ = "lessons"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    module_id = Column(Uuid, ForeignKey("modules.id"), nullable=False, index=True)

    position = Column(Integer, nullable=False, default=0)
    title = Column(String(300), nullable=False)
    objective = Column(Text, nullable=True)

    module = relationship("Module", back_populates="lessons")
    concepts = relationship("LessonConcept", back_populates="lesson", cascade="all, delete-orphan")


class LessonConcept(Base):
    """Which concepts a lesson covers, and how (role + weight)."""

    __tablename__ = "lesson_concepts"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    lesson_id = Column(Uuid, ForeignKey("lessons.id"), nullable=False, index=True)
    concept_id = Column(Uuid, ForeignKey("concepts.id"), nullable=False, index=True)

    role = Column(String(16), nullable=False, default=ConceptRole.INTRODUCES.value)
    weight = Column(Float, nullable=False, default=1.0)  # (0, 1]

    lesson = relationship("Lesson", back_populates="concepts")


class AssessmentBlueprint(Base):
    """
    A plan for how many questions of what type/difficulty a concept should
    get, decided BEFORE any question is generated (frozen-scope.md;
    AGENTS.md's "prefer a plan, not a bare prompt" pattern already used for
    the Groq quiz protocol). No question text lives here.
    """

    __tablename__ = "assessment_blueprints"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    course_version_id = Column(Uuid, ForeignKey("course_versions.id"), nullable=False, index=True)
    concept_id = Column(Uuid, ForeignKey("concepts.id"), nullable=False, index=True)

    question_type = Column(String(16), nullable=False, default="MCQ")
    difficulty = Column(String(8), nullable=False, default="medium")
    target_count = Column(Integer, nullable=False, default=1)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
