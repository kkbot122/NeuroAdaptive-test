"""
Curriculum API surface.

CONFLICT WITH THE PHASE 2 PACK, DECLARED: the pack's test cases reference
`PATCH /courses/{id}/outline` and `GET /courses/{id}/concept-graph`.
architecture.md's own API surface -- which states "the API specification is
authoritative" -- names `GET/PUT /courses/{courseId}/structure`,
`GET/PUT /courses/{courseId}/graph` and `POST .../publish-structure`. Per
AGENTS.md's authority order, architecture.md's naming is used here; the
underlying behaviour the pack's tests check (a lesson rename persists and is
reflected on the next read; a course's graph is owner-scoped) is what is
actually implemented and tested, under the frozen names.

PUT /structure this phase supports lesson renames only -- dropping a concept
or reordering a module is not implemented yet (deferred, see SPRINT_LOG).
PUT /graph (editing prerequisite edges) is not implemented this phase either;
GET is, which is what ownership scoping actually needs to be proven for.
"""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.curriculum.service import (
    CurriculumNotFound,
    CurriculumService,
    VersionNotReady,
)
from app.services.embedding.gemini import GeminiEmbeddingGateway
from app.services.generation.gemini import GeminiGenerationGateway

router = APIRouter()


def _service(db: Session = Depends(get_db)) -> CurriculumService:
    # Lazy clients (see K-14 and its Gemini/Qdrant equivalents): constructing
    # these per request touches no network until a route actually generates.
    return CurriculumService(db, GeminiGenerationGateway(), GeminiEmbeddingGateway())


def _version_out(version) -> dict:
    modules_out = []
    for module in version.modules:
        lessons_out = []
        for lesson in module.lessons:
            lessons_out.append({
                "id": str(lesson.id),
                "title": lesson.title,
                "objective": lesson.objective,
                "concepts": [
                    {
                        "concept_id": str(lc.concept_id),
                        "role": lc.role,
                        "weight": lc.weight,
                    }
                    for lc in lesson.concepts
                ],
            })
        modules_out.append({"id": str(module.id), "title": module.title, "lessons": lessons_out})

    return {
        "version_id": str(version.id),
        "version_number": version.version_number,
        "status": version.status,
        "validation_errors": version.validation_errors,
        "concept_carryover_map": version.concept_carryover_map,
        "modules": modules_out,
    }


def _graph_out(graph) -> dict:
    return {
        "concepts": [
            {
                "id": str(c.id),
                "canonical_key": c.canonical_key,
                "name": c.name,
                "definition": c.definition,
                "aliases": c.aliases,
                "importance": c.importance,
                "bloom_level": c.bloom_level,
            }
            for c in graph.concepts
        ],
        "edges": [
            {
                "prerequisite_concept_id": str(e.prerequisite_concept_id),
                "dependent_concept_id": str(e.dependent_concept_id),
                "strength": e.strength,
                "confidence": e.confidence,
            }
            for e in graph.edges
        ],
    }


class LessonRename(BaseModel):
    lesson_id: UUID
    title: str = Field(min_length=1, max_length=300)


class StructureUpdateIn(BaseModel):
    """Minimal edit surface this phase supports: renaming lessons."""

    lesson_renames: List[LessonRename] = Field(default_factory=list)


@router.get("/courses/{course_id}/structure")
def get_structure(
    course_id: UUID,
    user: User = Depends(get_current_user),
    service: CurriculumService = Depends(_service),
):
    """The outline review gate's read side: the latest generated version,
    whatever its status, so a learner can review a draft before publishing."""
    try:
        version = service.get_review_version(course_id, user.id)
    except CurriculumNotFound:
        raise HTTPException(status_code=404, detail="Course not found")

    if version is None:
        raise HTTPException(status_code=404, detail="No course structure has been generated yet.")
    return _version_out(version)


@router.put("/courses/{course_id}/structure")
def update_structure(
    course_id: UUID,
    body: StructureUpdateIn,
    user: User = Depends(get_current_user),
    service: CurriculumService = Depends(_service),
):
    """Cheap correction before expensive [re]generation: edit the reviewed
    draft's lesson titles. Reordering modules and dropping concepts are not
    implemented yet."""
    try:
        for rename in body.lesson_renames:
            service.rename_lesson(course_id, user.id, rename.lesson_id, rename.title)
        version = service.get_review_version(course_id, user.id)
    except CurriculumNotFound:
        raise HTTPException(status_code=404, detail="Course or lesson not found")

    if version is None:
        raise HTTPException(status_code=404, detail="No course structure has been generated yet.")
    return _version_out(version)


@router.get("/courses/{course_id}/graph")
def get_graph(
    course_id: UUID,
    version_id: Optional[UUID] = None,
    user: User = Depends(get_current_user),
    service: CurriculumService = Depends(_service),
):
    try:
        graph = service.get_graph(course_id, user.id, version_id)
    except CurriculumNotFound:
        raise HTTPException(status_code=404, detail="Course not found")
    return _graph_out(graph)


@router.post("/courses/{course_id}/publish-structure")
def publish_structure(
    course_id: UUID,
    version_id: Optional[UUID] = None,
    user: User = Depends(get_current_user),
    service: CurriculumService = Depends(_service),
):
    """
    Explicit confirmation, per the mandate: this is the only route that ever
    changes course.active_version_id. Defaults to publishing the latest
    generated version (the one the review gate showed).
    """
    try:
        target_version_id = version_id
        if target_version_id is None:
            latest = service.get_review_version(course_id, user.id)
            if latest is None:
                raise HTTPException(
                    status_code=404, detail="No course structure has been generated yet."
                )
            target_version_id = latest.id

        course = service.activate_version(course_id, user.id, target_version_id)
    except CurriculumNotFound:
        raise HTTPException(status_code=404, detail="Course or version not found")
    except VersionNotReady as exc:
        raise HTTPException(
            status_code=409,
            detail=f"This version has not passed validation (status: {exc}) and cannot be published.",
        )

    return {"course_id": str(course.id), "active_version_id": str(course.active_version_id)}
