from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import get_current_user, get_grading_reviewer_user
from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.learning.schemas import (
    ActivityProgressIn,
    ActivityContentResponseOut,
    AnswerIn,
    AssessmentSessionOut,
    GradingIssueReportIn,
    GradingIssueReportOut,
    GradingCorrectionIn,
    GradingReviewItemOut,
    GradingReviewPageOut,
    GradingReviewReasonIn,
    LearningActivityOut,
    LearningStateOut,
    PresentationFormat,
)
from app.modules.learning.service import (
    AssessmentUnavailable,
    LearningConflict,
    LearningNotFound,
    GradingReviewConflict,
    GradingReviewNotFound,
    LearningService,
)
from app.modules.learning.dispatch import GradingDispatcher
from app.modules.learning.activity_types import PREPARED_ACTIVITY_TYPES
from app.modules.preparation.dependencies import get_preparation_service as _preparation_service
from app.modules.preparation.service import (
    ActivityPreparationService,
    PreparationConflict,
    PreparationNotFound,
)
from app.services.providers import embedding_gateway, generation_gateway

router = APIRouter()


def _grading_dispatcher() -> GradingDispatcher:
    return GradingDispatcher()


def _service(
    db: Session = Depends(get_db),
    dispatcher: GradingDispatcher = Depends(_grading_dispatcher),
) -> LearningService:
    return LearningService(db, generation_gateway(), embedding_gateway(), dispatcher)


def _raise_learning_error(exc: Exception) -> None:
    if isinstance(exc, LearningNotFound):
        raise HTTPException(status_code=404, detail="Learning resource not found") from exc
    if isinstance(exc, (LearningConflict, AssessmentUnavailable)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, PreparationNotFound):
        raise HTTPException(status_code=404, detail="Learning resource not found") from exc
    if isinstance(exc, PreparationConflict):
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
    preparation: ActivityPreparationService = Depends(_preparation_service),
):
    try:
        activity = service.select_or_resume(course_id, user.id)
        if (
            preparation is not None
            and activity["activity_type"] in PREPARED_ACTIVITY_TYPES
        ):
            preparation.request_activity(course_id, activity["id"], user.id)
            return service.get_activity(course_id, activity["id"], user.id)
        return activity
    except (LearningNotFound, LearningConflict, PreparationNotFound, PreparationConflict) as exc:
        _raise_learning_error(exc)


@router.get("/courses/{course_id}/activities/{activity_id}", response_model=LearningActivityOut, response_model_exclude_none=True)
def get_activity(
    course_id: UUID,
    activity_id: UUID,
    user: User = Depends(get_current_user),
    service: LearningService = Depends(_service),
    preparation: ActivityPreparationService = Depends(_preparation_service),
):
    try:
        activity = service.get_activity(course_id, activity_id, user.id)
        if (
            preparation is not None
            and activity["activity_type"] in PREPARED_ACTIVITY_TYPES
        ):
            preparation.request_activity(course_id, activity_id, user.id)
            return service.get_activity(course_id, activity_id, user.id)
        return activity
    except (LearningNotFound, LearningConflict, PreparationNotFound, PreparationConflict) as exc:
        _raise_learning_error(exc)


@router.post(
    "/courses/{course_id}/linked-matches/{match_id}/optional-check",
    response_model=LearningActivityOut,
    response_model_exclude_none=True,
)
def start_optional_linked_check(
    course_id: UUID,
    match_id: UUID,
    user: User = Depends(get_current_user),
    service: LearningService = Depends(_service),
    preparation: ActivityPreparationService = Depends(_preparation_service),
):
    try:
        if preparation is None:
            raise PreparationConflict("Preparation is unavailable")
        activity = service.create_optional_linked_check(course_id, user.id, match_id)
        preparation.request_activity(course_id, activity["id"], user.id)
        return service.get_activity(course_id, activity["id"], user.id)
    except (LearningNotFound, LearningConflict, PreparationNotFound, PreparationConflict) as exc:
        _raise_learning_error(exc)


@router.post(
    "/courses/{course_id}/activities/{activity_id}/preparation/retry",
    response_model=LearningActivityOut,
    response_model_exclude_none=True,
)
def retry_activity_preparation(
    course_id: UUID,
    activity_id: UUID,
    format: PresentationFormat | None = None,
    user: User = Depends(get_current_user),
    service: LearningService = Depends(_service),
    preparation: ActivityPreparationService = Depends(_preparation_service),
):
    try:
        if preparation is None:
            raise HTTPException(status_code=409, detail="Preparation is unavailable")
        preparation.retry(course_id, activity_id, user.id, format)
        return service.get_activity(course_id, activity_id, user.id)
    except (LearningNotFound, LearningConflict, PreparationNotFound, PreparationConflict) as exc:
        _raise_learning_error(exc)


@router.get(
    "/courses/{course_id}/activities/{activity_id}/content",
    response_model=ActivityContentResponseOut,
    response_model_exclude_none=True,
    responses={202: {"model": ActivityContentResponseOut, "description": "Preparation is pending or running"}},
)
def get_prepared_activity_content(
    course_id: UUID,
    activity_id: UUID,
    format: PresentationFormat = "detailed",
    user: User = Depends(get_current_user),
    preparation: ActivityPreparationService = Depends(_preparation_service),
):
    try:
        if preparation is None:
            raise PreparationConflict("Preparation is unavailable")
        result = preparation.content_response(course_id, activity_id, user.id, format)
        status_code = 200 if result["status"] in {"READY", "RECOVERABLE_FAILURE"} else 202
        return JSONResponse(status_code=status_code, content=jsonable_encoder(result))
    except (PreparationNotFound, PreparationConflict) as exc:
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
    "/courses/{course_id}/activities/{activity_id}/skip-optional-check",
    response_model=LearningActivityOut,
    response_model_exclude_none=True,
)
def skip_optional_linked_check(
    course_id: UUID,
    activity_id: UUID,
    user: User = Depends(get_current_user),
    service: LearningService = Depends(_service),
):
    try:
        return service.skip_optional_link_check(course_id, activity_id, user.id)
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
    preparation: ActivityPreparationService = Depends(_preparation_service),
):
    try:
        if preparation is not None:
            activity = service.get_activity(course_id, activity_id, user.id)
            if activity["activity_type"] in PREPARED_ACTIVITY_TYPES:
                preparation.require_assessment_ready(course_id, activity_id, user.id)
        return service.start_activity_assessment(course_id, activity_id, user.id)
    except (LearningNotFound, LearningConflict, AssessmentUnavailable, PreparationNotFound, PreparationConflict) as exc:
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
    "/courses/{course_id}/assessment-sessions/{session_id}/questions/{question_id}/grading-issue",
    response_model=GradingIssueReportOut,
    status_code=201,
)
def report_session_grading_issue(
    course_id: UUID,
    session_id: UUID,
    question_id: UUID,
    body: GradingIssueReportIn,
    user: User = Depends(get_current_user),
    service: LearningService = Depends(_service),
):
    try:
        return service.report_grading_issue(
            course_id, session_id, question_id, user.id, body.report_text
        )
    except LearningNotFound as exc:
        _raise_learning_error(exc)
    except LearningConflict as exc:
        _raise_learning_error(exc)


def _raise_review_error(exc: Exception) -> None:
    if isinstance(exc, GradingReviewNotFound):
        raise HTTPException(status_code=404, detail="Review report not found") from exc
    if isinstance(exc, GradingReviewConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


@router.get("/grading-reviews", response_model=GradingReviewPageOut)
def list_grading_reviews(
    status: str | None = Query(default=None, pattern="^(OPEN|IN_REVIEW|RETAINED|CORRECTED)$"),
    limit: int = Query(default=settings.P5_REVIEW_PAGE_SIZE_V1, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _reviewer: User = Depends(get_grading_reviewer_user),
    service: LearningService = Depends(_service),
):
    return service.list_grading_reviews(status, limit, offset)


@router.get("/grading-reviews/{report_id}", response_model=GradingReviewItemOut)
def get_grading_review(
    report_id: UUID,
    _reviewer: User = Depends(get_grading_reviewer_user),
    service: LearningService = Depends(_service),
):
    try:
        return service.get_grading_review(report_id)
    except GradingReviewNotFound as exc:
        _raise_review_error(exc)


@router.post("/grading-reviews/{report_id}/start", response_model=GradingReviewItemOut)
def start_grading_review(
    report_id: UUID,
    body: GradingReviewReasonIn,
    reviewer: User = Depends(get_grading_reviewer_user),
    service: LearningService = Depends(_service),
):
    try:
        return service.start_grading_review(report_id, reviewer.id, body.reason)
    except (GradingReviewNotFound, GradingReviewConflict) as exc:
        _raise_review_error(exc)


@router.post("/grading-reviews/{report_id}/retain", response_model=GradingReviewItemOut)
def retain_grading_judgment(
    report_id: UUID,
    body: GradingReviewReasonIn,
    reviewer: User = Depends(get_grading_reviewer_user),
    service: LearningService = Depends(_service),
):
    try:
        return service.retain_grading_judgment(report_id, reviewer.id, body.reason)
    except (GradingReviewNotFound, GradingReviewConflict) as exc:
        _raise_review_error(exc)


@router.post("/grading-reviews/{report_id}/correct", response_model=GradingReviewItemOut)
def correct_grading_judgment(
    report_id: UUID,
    body: GradingCorrectionIn,
    reviewer: User = Depends(get_grading_reviewer_user),
    service: LearningService = Depends(_service),
):
    try:
        return service.correct_grading_judgment(
            report_id,
            reviewer.id,
            body.criteria_met,
            body.reason,
            body.expected_correction_version,
        )
    except (GradingReviewNotFound, GradingReviewConflict) as exc:
        _raise_review_error(exc)


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
