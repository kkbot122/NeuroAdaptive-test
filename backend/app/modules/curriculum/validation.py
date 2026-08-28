"""
Validation gate: a course version is checked deterministically before it may
ever activate. Per the mandate, the LLM's own confidence about its proposed
structure never substitutes for this.

Scope note: "citation resolution" for this phase means ConceptSource rows
(concept -> chunk provenance) resolving to real, owned chunks -- no lesson
content with inline citations exists yet (deferred; see SPRINT_LOG). This is
the concrete grounding link this phase actually produces, and it is the
thing the mandate's "every citation points to a real, owned chunk" rule is
protecting: a concept that claims to come from a chunk that doesn't exist, or
belongs to someone else, is exactly as ungrounded as a fabricated citation in
generated prose would be.
"""
from dataclasses import dataclass, field
from typing import List
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.curriculum.graph import ProposedEdge, is_acyclic
from app.modules.curriculum.models import (
    Concept,
    ConceptPrerequisite,
    ConceptSource,
    CourseVersion,
    Lesson,
    LessonConcept,
    Module,
)
from app.modules.documents.chunk_models import Chunk

# Unvalidated default: a concept at or above this importance is "important"
# enough to require assessment coverage.
IMPORTANT_CONCEPT_THRESHOLD = 0.7


@dataclass
class ValidationResult:
    errors: List[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors


def validate_course_version(db: Session, version: CourseVersion) -> ValidationResult:
    errors: List[str] = []

    concepts = db.query(Concept).filter(Concept.course_version_id == version.id).all()
    concept_ids = {c.id for c in concepts}

    lesson_ids = [
        lesson.id
        for module in db.query(Module).filter(Module.course_version_id == version.id).all()
        for lesson in module.lessons
    ]

    covered_concept_ids = {
        lc.concept_id
        for lc in db.query(LessonConcept).filter(LessonConcept.lesson_id.in_(lesson_ids)).all()
    } if lesson_ids else set()

    # 1. Every concept belongs to at least one lesson.
    uncovered = concept_ids - covered_concept_ids
    for concept in concepts:
        if concept.id in uncovered:
            errors.append(f"Concept '{concept.name}' does not belong to any lesson.")

    # 2. Every ConceptSource resolves to a real chunk owned by this course/owner.
    sources = db.query(ConceptSource).filter(ConceptSource.concept_id.in_(concept_ids)).all() if concept_ids else []
    for source in sources:
        chunk = (
            db.query(Chunk)
            .filter(
                Chunk.id == source.chunk_id,
                Chunk.course_id == version.course_id,
                Chunk.owner_id == version.owner_id,
            )
            .first()
        )
        if chunk is None:
            errors.append(
                f"A source reference for concept {source.concept_id} points to a chunk "
                "that does not exist or does not belong to this course."
            )

    # 3. Prerequisite graph acyclicity.
    edges = (
        db.query(ConceptPrerequisite)
        .filter(ConceptPrerequisite.course_version_id == version.id)
        .all()
    )
    proposed = [
        ProposedEdge(
            prerequisite_id=e.prerequisite_concept_id,
            dependent_id=e.dependent_concept_id,
            strength=e.strength,
            confidence=e.confidence,
        )
        for e in edges
    ]
    if not is_acyclic(proposed):
        errors.append("The prerequisite graph contains a cycle.")

    # 4. Every important concept has assessment coverage.
    from app.modules.curriculum.models import AssessmentBlueprint

    blueprinted_concept_ids = {
        b.concept_id
        for b in db.query(AssessmentBlueprint)
        .filter(AssessmentBlueprint.course_version_id == version.id)
        .all()
    }
    for concept in concepts:
        if concept.importance >= IMPORTANT_CONCEPT_THRESHOLD and concept.id not in blueprinted_concept_ids:
            errors.append(
                f"Important concept '{concept.name}' (importance {concept.importance}) "
                "has no assessment coverage."
            )

    return ValidationResult(errors=errors)
