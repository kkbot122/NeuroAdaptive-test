"""
CurriculumService: orchestrates concept extraction through course-version
activation. Each step it calls (extraction, normalization, edge proposal,
cycle resolution, validation, carryover) is independently pure/testable;
this module's own job is persistence and sequencing, not decision logic.

Module clustering and lesson planning are deterministic and heuristic in
this phase, not LLM-driven: concepts are grouped into modules by their
originating document section, in document order -- "the source document's
own section order as a strong prior" (the mandate's own words) is easiest to
honor by literally using it, rather than asking an LLM to reconstruct
structure the source already had. This is a scope simplification recorded
here and in SPRINT_LOG: a graph-community-detection pass or an LLM-polished
lesson objective is future work, not attempted this phase.
"""
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.courses.models import Course
from app.modules.courses.service import CourseNotFound, CourseService
from app.modules.curriculum.carryover import CarryoverCandidate, compute_carryover
from app.modules.curriculum.edges import ConceptForEdges, propose_edges
from app.modules.curriculum.extraction import (
    group_chunks_into_sections,
    propose_concepts_for_section,
)
from app.modules.curriculum.graph import resolve_cycles
from app.modules.curriculum.models import (
    AssessmentBlueprint,
    Concept,
    ConceptPrerequisite,
    ConceptSource,
    CourseVersion,
    CourseVersionStatus,
    Lesson,
    LessonConcept,
    Module,
)
from app.modules.curriculum.normalization import canonical_key, normalize_concepts
from app.modules.curriculum.validation import (
    IMPORTANT_CONCEPT_THRESHOLD,
    validate_course_version,
)
from app.modules.documents.chunk_models import Chunk
from app.modules.documents.models import Document
from app.services.embedding.gateway import EmbeddingGateway
from app.services.generation.gateway import GenerationGateway

# Minimum lesson weight: LessonConcept.weight is documented as (0, 1], so a
# concept with importance 0.0 still needs a strictly positive weight.
_MIN_LESSON_WEIGHT = 0.05


class CurriculumNotFound(Exception):
    """Course, version, module or lesson not found, or not owned by caller."""


class VersionNotReady(Exception):
    """Attempted to activate a version that has not passed validation."""


@dataclass
class GraphView:
    concepts: List[Concept]
    edges: List[ConceptPrerequisite]


class CurriculumService:
    def __init__(self, db: Session, generation: GenerationGateway, embeddings: EmbeddingGateway):
        self.db = db
        self.generation = generation
        self.embeddings = embeddings
        self.courses = CourseService(db)

    # -- generation -------------------------------------------------------

    def generate_version(self, course_id: UUID, owner_id: int) -> CourseVersion:
        """
        Runs the whole pipeline and persists a new, immutable CourseVersion.
        Never touches course.active_version_id -- see activate_version().
        """
        try:
            course = self.courses.get_owned(course_id, owner_id)
        except CourseNotFound:
            raise CurriculumNotFound(str(course_id))

        chunks = (
            self.db.query(Chunk)
            .filter(Chunk.course_id == course_id, Chunk.owner_id == owner_id)
            .all()
        )

        candidates = []
        for section in group_chunks_into_sections(chunks):
            candidates.extend(
                propose_concepts_for_section(section, self.generation, self.embeddings)
            )

        normalized = normalize_concepts(candidates, self.generation)

        # A concept's "home section" is its first source chunk's heading path
        # in document order -- what module clustering groups by below.
        chunk_by_id = {c.id: c for c in chunks}

        previous_version = self._active_version(course)
        old_candidates = self._carryover_candidates(previous_version.id) if previous_version else []

        version_number = self._next_version_number(course_id)
        version = CourseVersion(
            course_id=course_id,
            owner_id=owner_id,
            version_number=version_number,
            status=CourseVersionStatus.DRAFT.value,
            source_fingerprint=self._source_fingerprint(course_id),
            validation_errors=[],
        )
        self.db.add(version)
        self.db.flush()

        concepts: List[Concept] = []
        for item in normalized:
            concept = Concept(
                course_id=course_id,
                course_version_id=version.id,
                owner_id=owner_id,
                canonical_key=canonical_key(item.name),
                name=item.name,
                definition=item.definition,
                aliases=item.aliases,
                importance=item.importance,
                bloom_level=item.bloom_level,
                embedding=item.embedding,
            )
            self.db.add(concept)
            self.db.flush()
            concepts.append(concept)
            for chunk_id in item.source_chunk_ids:
                if chunk_id in chunk_by_id:
                    self.db.add(
                        ConceptSource(
                            concept_id=concept.id,
                            chunk_id=chunk_id,
                            course_id=course_id,
                            owner_id=owner_id,
                        )
                    )

        # Carryover, computed against the *previous* version's concepts.
        if previous_version:
            new_candidates = [
                CarryoverCandidate(id=c.id, canonical_key=c.canonical_key, embedding=c.embedding)
                for c in concepts
            ]
            version.concept_carryover_map = compute_carryover(old_candidates, new_candidates)

        # Prerequisite graph.
        edge_inputs = [ConceptForEdges(id=c.id, name=c.name, definition=c.definition) for c in concepts]
        proposed = propose_edges(edge_inputs, self.generation)
        acyclic, _dropped = resolve_cycles(proposed)
        for edge in acyclic:
            self.db.add(
                ConceptPrerequisite(
                    course_id=course_id,
                    course_version_id=version.id,
                    prerequisite_concept_id=edge.prerequisite_id,
                    dependent_concept_id=edge.dependent_id,
                    strength=edge.strength,
                    confidence=edge.confidence,
                )
            )

        # Module/lesson clustering -- deterministic, from document structure.
        self._build_modules_and_lessons(version, concepts, chunk_by_id)

        # Assessment blueprints -- deterministic default: one MCQ per
        # important concept. See module docstring for the scope note.
        for concept in concepts:
            if concept.importance >= IMPORTANT_CONCEPT_THRESHOLD:
                self.db.add(
                    AssessmentBlueprint(
                        course_version_id=version.id,
                        concept_id=concept.id,
                        question_type="MCQ",
                        difficulty="medium",
                        target_count=1,
                    )
                )

        self.db.commit()
        self.db.refresh(version)

        result = validate_course_version(self.db, version)
        version.status = (
            CourseVersionStatus.READY.value if result.is_valid else CourseVersionStatus.FAILED.value
        )
        version.validation_errors = result.errors
        self.db.commit()
        self.db.refresh(version)
        return version

    def _build_modules_and_lessons(self, version, concepts, chunk_by_id) -> None:
        """One module per top-level heading segment, one lesson per
        second-level segment (or one catch-all lesson if headings are
        shallower), in the order sections first appeared in the source."""
        module_order: List[str] = []
        module_lessons: dict = {}  # module_title -> {lesson_title: [concept]}

        for concept in concepts:
            home_chunk_id = concept.sources[0].chunk_id if concept.sources else None
            heading = None
            if home_chunk_id and home_chunk_id in chunk_by_id:
                heading = chunk_by_id[home_chunk_id].heading_path
            parts = [p.strip() for p in (heading or "General").split(">")] or ["General"]
            module_title = parts[0] or "General"
            lesson_title = parts[1] if len(parts) > 1 else module_title

            if module_title not in module_lessons:
                module_order.append(module_title)
                module_lessons[module_title] = {}
            module_lessons[module_title].setdefault(lesson_title, []).append(concept)

        for module_position, module_title in enumerate(module_order):
            module = Module(course_version_id=version.id, position=module_position, title=module_title)
            self.db.add(module)
            self.db.flush()

            for lesson_position, (lesson_title, lesson_concepts) in enumerate(
                module_lessons[module_title].items()
            ):
                lesson = Lesson(
                    module_id=module.id,
                    position=lesson_position,
                    title=lesson_title,
                    objective=f"Understand: {', '.join(c.name for c in lesson_concepts)}.",
                )
                self.db.add(lesson)
                self.db.flush()

                for concept in lesson_concepts:
                    self.db.add(
                        LessonConcept(
                            lesson_id=lesson.id,
                            concept_id=concept.id,
                            role="INTRODUCES",
                            weight=max(concept.importance, _MIN_LESSON_WEIGHT),
                        )
                    )

    # -- activation ---------------------------------------------------------

    def activate_version(self, course_id: UUID, owner_id: int, version_id: UUID) -> Course:
        """
        The only place course.active_version_id changes. Requires the
        version to have passed validation; the pointer swap and its commit
        are the last thing this method does, so a failure here leaves the
        previously active version untouched.
        """
        try:
            course = self.courses.get_owned(course_id, owner_id)
        except CourseNotFound:
            raise CurriculumNotFound(str(course_id))

        version = (
            self.db.query(CourseVersion)
            .filter(CourseVersion.id == version_id, CourseVersion.course_id == course_id)
            .first()
        )
        if version is None:
            raise CurriculumNotFound(str(version_id))
        if version.status != CourseVersionStatus.READY.value:
            raise VersionNotReady(version.status)

        course.active_version_id = version.id
        version.activated_at = datetime.now(timezone.utc)
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        self.db.refresh(course)
        return course

    # -- reads ----------------------------------------------------------------

    def get_version(self, course_id: UUID, owner_id: int, version_id: UUID) -> CourseVersion:
        self.courses.get_owned(course_id, owner_id)  # raises CourseNotFound
        version = (
            self.db.query(CourseVersion)
            .filter(CourseVersion.id == version_id, CourseVersion.course_id == course_id)
            .first()
        )
        if version is None:
            raise CurriculumNotFound(str(version_id))
        return version

    def get_active_structure(self, course_id: UUID, owner_id: int) -> Optional[CourseVersion]:
        course = self.courses.get_owned(course_id, owner_id)
        if course.active_version_id is None:
            return None
        return self.get_version(course_id, owner_id, course.active_version_id)

    def get_graph(self, course_id: UUID, owner_id: int, version_id: Optional[UUID] = None) -> GraphView:
        """Defaults to the active version's graph. A course with no active
        version yet (nothing generated, or the only version failed
        validation) returns an empty graph rather than raising."""
        course = self.courses.get_owned(course_id, owner_id)
        target_version_id = version_id or course.active_version_id
        if target_version_id is None:
            return GraphView(concepts=[], edges=[])

        concepts = (
            self.db.query(Concept)
            .filter(Concept.course_version_id == target_version_id)
            .all()
        )
        edges = (
            self.db.query(ConceptPrerequisite)
            .filter(ConceptPrerequisite.course_version_id == target_version_id)
            .all()
        )
        return GraphView(concepts=concepts, edges=edges)

    def rename_lesson(self, course_id: UUID, owner_id: int, lesson_id: UUID, title: str) -> Lesson:
        """The outline review gate's simplest edit: PUT .../structure."""
        self.courses.get_owned(course_id, owner_id)
        lesson = (
            self.db.query(Lesson)
            .join(Module, Lesson.module_id == Module.id)
            .join(CourseVersion, Module.course_version_id == CourseVersion.id)
            .filter(Lesson.id == lesson_id, CourseVersion.course_id == course_id)
            .first()
        )
        if lesson is None:
            raise CurriculumNotFound(str(lesson_id))
        lesson.title = title
        self.db.commit()
        self.db.refresh(lesson)
        return lesson

    # -- helpers ------------------------------------------------------------

    def _active_version(self, course: Course) -> Optional[CourseVersion]:
        if course.active_version_id is None:
            return None
        return (
            self.db.query(CourseVersion)
            .filter(CourseVersion.id == course.active_version_id)
            .first()
        )

    def _carryover_candidates(self, previous_version_id: UUID) -> List[CarryoverCandidate]:
        return [
            CarryoverCandidate(id=c.id, canonical_key=c.canonical_key, embedding=c.embedding)
            for c in self.db.query(Concept)
            .filter(Concept.course_version_id == previous_version_id)
            .all()
        ]

    def _next_version_number(self, course_id: UUID) -> int:
        latest = (
            self.db.query(CourseVersion)
            .filter(CourseVersion.course_id == course_id)
            .order_by(CourseVersion.version_number.desc())
            .first()
        )
        return (latest.version_number + 1) if latest else 1

    def _source_fingerprint(self, course_id: UUID) -> str:
        checksums = sorted(
            d.checksum_sha256
            for d in self.db.query(Document).filter(Document.course_id == course_id).all()
        )
        return hashlib.sha256("|".join(checksums).encode()).hexdigest()
