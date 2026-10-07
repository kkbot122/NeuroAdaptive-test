from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.learning.schemas import (
    ActivityProgressIn,
    AnswerIn,
    AssessmentSessionOut,
    LearningActivityOut,
    LearningStateOut,
)
from app.modules.learning.service import (
    AssessmentUnavailable,
    LearningConflict,
    LearningNotFound,
    LearningService,
)
from app.services.providers import embedding_gateway, generation_gateway

router = APIRouter()


def _service(db: Session = Depends(get_db)) -> LearningService:
    return LearningService(db, generation_gateway(), embedding_gateway())


def _raise_learning_error(exc: Exception) -> None:
    if isinstance(exc, LearningNotFound):
        raise HTTPException(status_code=404, detail="Learning resource not found") from exc
    if isinstance(exc, (LearningConflict, AssessmentUnavailable)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


@router.get("/courses/{course_id}/learning-state", response_model=LearningStateOut, response_model_exclude_none=True)
def get_learning_state(
    course_id: UUID,
    user: User = Depends(get_current_user),
    service: LearningService = Depends(_service),
):
    try:
        return service.get_learning_state(course_id, user.id)
    except (LearningNotFound, LearningConflict) as exc:
        _raise_learning_error(exc)


@router.post("/courses/{course_id}/activities/next", response_model=LearningActivityOut, response_model_exclude_none=True)
def select_or_resume_activity(
    course_id: UUID,
    user: User = Depends(get_current_user),
    service: LearningService = Depends(_service),
):
    try:
        return service.select_or_resume(course_id, user.id)
    except (LearningNotFound, LearningConflict) as exc:
        _raise_learning_error(exc)


@router.get("/courses/{course_id}/activities/{activity_id}", response_model=LearningActivityOut, response_model_exclude_none=True)
def get_activity(
    course_id: UUID,
    activity_id: UUID,
    user: User = Depends(get_current_user),
    service: LearningService = Depends(_service),
):
    try:
        return service.get_activity(course_id, activity_id, user.id)
    except (LearningNotFound, LearningConflict) as exc:
        _raise_learning_error(exc)


@router.patch("/courses/{course_id}/activities/{activity_id}", response_model=LearningActivityOut, response_model_exclude_none=True)
def save_activity_progress(
    course_id: UUID,
    activity_id: UUID,
    body: ActivityProgressIn,
    user: User = Depends(get_current_user),
    service: LearningService = Depends(_service),
):
    try:
        return service.update_progress(
            course_id,
            activity_id,
            user.id,
            reading_position=body.reading_position,
            presentation_format=body.presentation_format,
        )
    except (LearningNotFound, LearningConflict) as exc:
        _raise_learning_error(exc)


@router.post("/courses/{course_id}/activities/{activity_id}/reading-complete", response_model=LearningActivityOut, response_model_exclude_none=True)
def complete_reading(
    course_id: UUID,
    activity_id: UUID,
    user: User = Depends(get_current_user),
    service: LearningService = Depends(_service),
):
    try:
        return service.complete_reading(course_id, activity_id, user.id)
    except (LearningNotFound, LearningConflict) as exc:
        _raise_learning_error(exc)


@router.post(
    "/courses/{course_id}/activities/{activity_id}/assessment",
    response_model=AssessmentSessionOut,
    response_model_exclude_none=True,
)
def start_activity_assessment(
    course_id: UUID,
    activity_id: UUID,
    user: User = Depends(get_current_user),
    service: LearningService = Depends(_service),
):
    try:
        return service.start_activity_assessment(course_id, activity_id, user.id)
    except (LearningNotFound, LearningConflict, AssessmentUnavailable) as exc:
        _raise_learning_error(exc)


@router.get(
    "/courses/{course_id}/assessment-sessions/{session_id}",
    response_model=AssessmentSessionOut,
    response_model_exclude_none=True,
)
def resume_assessment(
    course_id: UUID,
    session_id: UUID,
    user: User = Depends(get_current_user),
    service: LearningService = Depends(_service),
):
    try:
        return service.get_assessment(course_id, session_id, user.id)
    except (LearningNotFound, LearningConflict) as exc:
        _raise_learning_error(exc)


@router.post(
    "/courses/{course_id}/assessment-sessions/{session_id}/questions/{question_id}/answer",
    response_model=AssessmentSessionOut,
    response_model_exclude_none=True,
)
def submit_session_answer(
    course_id: UUID,
    session_id: UUID,
    question_id: UUID,
    body: AnswerIn,
    user: User = Depends(get_current_user),
    service: LearningService = Depends(_service),
):
    try:
        return service.submit_answer(course_id, session_id, question_id, user.id, body.given_answer)
    except (LearningNotFound, LearningConflict) as exc:
        _raise_learning_error(exc)


@router.post(
    "/courses/{course_id}/assessment-sessions/{session_id}/questions/{question_id}/retry-grading",
    response_model=AssessmentSessionOut,
    response_model_exclude_none=True,
)
def retry_session_grading(
    course_id: UUID,
    session_id: UUID,
    question_id: UUID,
    user: User = Depends(get_current_user),
    service: LearningService = Depends(_service),
):
    try:
        return service.retry_grading(course_id, session_id, question_id, user.id)
    except (LearningNotFound, LearningConflict) as exc:
        _raise_learning_error(exc)


@router.post(
    "/courses/{course_id}/assessment-sessions/{session_id}/submit",
    response_model=AssessmentSessionOut,
    response_model_exclude_none=True,
)
def submit_assessment(
    course_id: UUID,
    session_id: UUID,
    user: User = Depends(get_current_user),
    service: LearningService = Depends(_service),
):
    try:
        return service.submit_assessment(course_id, session_id, user.id)
    except (LearningNotFound, LearningConflict) as exc:
        _raise_learning_error(exc)
