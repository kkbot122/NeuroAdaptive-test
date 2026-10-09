from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.courses.schemas import (
    CourseCreate, CourseOut, CourseSubjectCreate, CourseSubjectOut,
    CourseUpdate, LinkedConceptMatchOut,
)
from app.modules.courses.service import (
    CourseNotFound,
    CourseService,
    RelationshipImmutable,
    RelationshipInvalid,
    SourcesImmutable,
)

router = APIRouter()


def _service(db: Session = Depends(get_db)) -> CourseService:
    return CourseService(db)


@router.post("", response_model=CourseOut, status_code=201)
def create_course(
    body: CourseCreate,
    user: User = Depends(get_current_user),
    service: CourseService = Depends(_service),
):
    try:
        course = service.create(
            owner_id=user.id,
            title=body.title,
            goal=body.goal,
            starting_confidence=body.starting_confidence,
            subject_id=body.subject_id,
            new_subject_name=body.new_subject_name,
            builds_on_course_id=body.builds_on_course_id,
        )
        return service.out(course)
    except RelationshipInvalid as exc:
        service.db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("", response_model=List[CourseOut])
def list_courses(
    user: User = Depends(get_current_user),
    service: CourseService = Depends(_service),
):
    return [service.out(course) for course in service.list_for_owner(user.id)]


@router.get("/subjects", response_model=List[CourseSubjectOut])
def list_subjects(
    user: User = Depends(get_current_user),
    service: CourseService = Depends(_service),
):
    return service.list_subjects_for_owner(user.id)


@router.post("/subjects", response_model=CourseSubjectOut, status_code=201)
def create_subject(
    body: CourseSubjectCreate,
    user: User = Depends(get_current_user),
    service: CourseService = Depends(_service),
):
    try:
        return service.create_subject(user.id, body.name)
    except RelationshipInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/{course_id}/linked-matches", response_model=List[LinkedConceptMatchOut])
def list_linked_matches(
    course_id: UUID,
    user: User = Depends(get_current_user),
    service: CourseService = Depends(_service),
):
    try:
        course = service.get_owned(course_id, user.id)
        return service.linked_match_summaries(course, user.id)
    except CourseNotFound:
        raise HTTPException(status_code=404, detail="Course not found")


@router.get("/{course_id}", response_model=CourseOut)
def get_course(
    course_id: UUID,
    user: User = Depends(get_current_user),
    service: CourseService = Depends(_service),
):
    try:
        return service.out(service.get_owned(course_id, user.id))
    except CourseNotFound:
        # 404, not 403: do not confirm that another learner's course exists.
        raise HTTPException(status_code=404, detail="Course not found")


@router.patch("/{course_id}", response_model=CourseOut)
def update_course(
    course_id: UUID,
    body: CourseUpdate,
    user: User = Depends(get_current_user),
    service: CourseService = Depends(_service),
):
    try:
        course = service.update(
            course_id,
            user.id,
            title=body.title,
            goal=body.goal,
            starting_confidence=body.starting_confidence,
            subject_id=body.subject_id,
            subject_was_set="subject_id" in body.model_fields_set,
            builds_on_course_id=body.builds_on_course_id,
            link_was_set="builds_on_course_id" in body.model_fields_set,
        )
        return service.out(course)
    except CourseNotFound:
        raise HTTPException(status_code=404, detail="Course not found")
    except RelationshipInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RelationshipImmutable:
        raise HTTPException(status_code=409, detail="Course relationships are frozen after publication")


@router.delete("/{course_id}", status_code=204)
def delete_course(
    course_id: UUID,
    user: User = Depends(get_current_user),
    service: CourseService = Depends(_service),
):
    try:
        service.delete(course_id, user.id)
    except CourseNotFound:
        raise HTTPException(status_code=404, detail="Course not found")


@router.post("/{course_id}/finalize-sources", response_model=CourseOut)
def finalize_sources(
    course_id: UUID,
    user: User = Depends(get_current_user),
    service: CourseService = Depends(_service),
):
    try:
        return service.out(service.finalize_sources(course_id, user.id))
    except CourseNotFound:
        raise HTTPException(status_code=404, detail="Course not found")
    except SourcesImmutable:
        raise HTTPException(
            status_code=409,
            detail="Source set is already finalized; create a new course to use different material.",
        )
