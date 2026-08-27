from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.courses.schemas import CourseCreate, CourseOut, CourseUpdate
from app.modules.courses.service import (
    CourseNotFound,
    CourseService,
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
    return service.create(
        owner_id=user.id,
        title=body.title,
        goal=body.goal,
        starting_confidence=body.starting_confidence,
    )


@router.get("", response_model=List[CourseOut])
def list_courses(
    user: User = Depends(get_current_user),
    service: CourseService = Depends(_service),
):
    return service.list_for_owner(user.id)


@router.get("/{course_id}", response_model=CourseOut)
def get_course(
    course_id: UUID,
    user: User = Depends(get_current_user),
    service: CourseService = Depends(_service),
):
    try:
        return service.get_owned(course_id, user.id)
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
        return service.update(
            course_id,
            user.id,
            title=body.title,
            goal=body.goal,
            starting_confidence=body.starting_confidence,
        )
    except CourseNotFound:
        raise HTTPException(status_code=404, detail="Course not found")


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
        return service.finalize_sources(course_id, user.id)
    except CourseNotFound:
        raise HTTPException(status_code=404, detail="Course not found")
    except SourcesImmutable:
        raise HTTPException(
            status_code=409,
            detail="Source set is already finalized; create a new course to use different material.",
        )
