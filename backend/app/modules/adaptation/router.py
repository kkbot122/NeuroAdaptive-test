"""
Adaptive-sequencing API surface.

CONFLICT WITH THE PHASE 4 PACK, DECLARED: the pack's own test cases name
`GET /courses/{id}/recommendations/next`. architecture.md's API surface
list -- which curriculum/router.py's own docstring already treats as
authoritative for this exact kind of naming question -- names
`GET /courses/{courseId}/next-activity` instead. Per AGENTS.md's authority
order, architecture.md wins: the route below is `next-activity`. The
behavior the pack's tests actually check (decision_id, a recommended object
with a reason, an alternatives array) is implemented and tested under this
name.
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.modules.adaptation.outcome_service import (
    AdaptationOutcomeNotFound,
    AdaptationOutcomeService,
    InvalidOutcome,
)
from app.modules.adaptation.service import (
    AdaptationNotFound,
    AdaptationPersistenceError,
    AdaptationService,
)
from app.modules.auth.models import User
from app.modules.mastery.service import MasteryService
from app.modules.privacy.service import PrivacyService
from app.services.embedding.gemini import GeminiEmbeddingGateway
from app.services.generation.gemini import GeminiGenerationGateway

router = APIRouter()


class PresentationOutcomeIn(BaseModel):
    format: str
    success: bool


class PresentationSwitchIn(BaseModel):
    from_format: str
    to_format: str


def _service(db: Session = Depends(get_db)) -> AdaptationService:
    return AdaptationService(db, GeminiGenerationGateway(), GeminiEmbeddingGateway())


@router.get("/courses/{course_id}/next-activity")
def get_next_activity(
    course_id: UUID,
    user: User = Depends(get_current_user),
    service: AdaptationService = Depends(_service),
):
    """
    Every call persists an AdaptationDecision before this returns anything
    -- if that write fails, the caller gets a 500, never a silently
    unlogged recommendation (mandate section D).
    """
    try:
        result = service.recommend_next(course_id, user.id)
    except AdaptationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc) or "Course not found")
    except AdaptationPersistenceError:
        raise HTTPException(status_code=500, detail="Could not record the adaptation decision.")

    return {
        "decision_id": str(result.decision_id),
        "recommended": result.recommended,
        "alternatives": result.alternatives,
    }


@router.post("/presentation-affinity/outcome")
def record_presentation_outcome(
    body: PresentationOutcomeIn,
    user: User = Depends(get_current_user),
    service: AdaptationService = Depends(_service),
):
    """The next checkpoint outcome after viewing a block in `format`."""
    row = service.record_presentation_outcome(user.id, body.format, body.success)
    return {"format": row.format, "effectiveness": row.effectiveness, "exposure_count": row.exposure_count}


@router.post("/presentation-affinity/switch")
def record_presentation_switch(
    body: PresentationSwitchIn,
    user: User = Depends(get_current_user),
    service: AdaptationService = Depends(_service),
):
    """A learner's manual format override -- weaker evidence than an
    outcome, but never ignored, and never treated as a contradiction to
    correct (guardrail)."""
    service.record_manual_switch(user.id, body.from_format, body.to_format)
    return {"status": "recorded"}


@router.post("/presentation-affinity/reset")
def reset_presentation_affinity(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Clears only presentation-format preference evidence -- ConceptMastery/
    MasteryEvent rows (what the learner has actually demonstrated knowing)
    are untouched. A learner resetting "which format works for me" and a
    learner erasing "what I've proven I know" are different requests with
    different reasons to exist; conflating them would strand real progress
    behind a preference-reset button.
    """
    removed = PrivacyService(db).reset_presentation_affinity(user.id)
    return {"status": "reset", "rows_removed": removed}


def _outcome_service(db: Session = Depends(get_db)) -> AdaptationOutcomeService:
    return AdaptationOutcomeService(db, MasteryService(db, GeminiGenerationGateway(), GeminiEmbeddingGateway()))


class EngagementOutcomeIn(BaseModel):
    outcome_type: str = Field(pattern="^(VIEWED|COMPLETED|ABANDONED|REJECTED)$")
    extra: Optional[dict] = None


class HelpfulnessFeedbackIn(BaseModel):
    rating: int = Field(ge=-1, le=1)


class AssessmentOutcomeIn(BaseModel):
    question_attempt_id: UUID
    is_transfer_question: bool = False
    baseline_question_attempt_id: Optional[UUID] = None


@router.post("/courses/{course_id}/adaptation-decisions/{decision_id}/outcomes/engagement")
def record_engagement_outcome(
    course_id: UUID,
    decision_id: UUID,
    body: EngagementOutcomeIn,
    user: User = Depends(get_current_user),
    service: AdaptationOutcomeService = Depends(_outcome_service),
):
    """Plain activity completion/abandonment/viewing, or an explicit
    rejection (the learner picked a different alternative). Never carries a
    mastery_delta or transfer_success -- those only come from
    /outcomes/assessment, which requires real QuestionAttempt evidence."""
    try:
        outcome = service.record_engagement(decision_id, user.id, body.outcome_type, body.extra)
    except AdaptationOutcomeNotFound:
        raise HTTPException(status_code=404, detail="Decision not found")
    except InvalidOutcome as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"outcome_id": str(outcome.id), "outcome_type": outcome.outcome_type, "signal_category": outcome.signal_category}


@router.post("/courses/{course_id}/adaptation-decisions/{decision_id}/outcomes/helpfulness")
def record_helpfulness_outcome(
    course_id: UUID,
    decision_id: UUID,
    body: HelpfulnessFeedbackIn,
    user: User = Depends(get_current_user),
    service: AdaptationOutcomeService = Depends(_outcome_service),
):
    """Explicit learner feedback -- self-reported, kept in its own
    signal_category, never averaged with measured mastery change."""
    try:
        outcome = service.record_helpfulness_feedback(decision_id, user.id, body.rating)
    except AdaptationOutcomeNotFound:
        raise HTTPException(status_code=404, detail="Decision not found")
    except InvalidOutcome as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"outcome_id": str(outcome.id), "helpfulness_rating": outcome.helpfulness_rating}


@router.post("/courses/{course_id}/adaptation-decisions/{decision_id}/outcomes/assessment")
def record_assessment_outcome(
    course_id: UUID,
    decision_id: UUID,
    body: AssessmentOutcomeIn,
    user: User = Depends(get_current_user),
    service: AdaptationOutcomeService = Depends(_outcome_service),
):
    """
    The pedagogical-effect path: mastery_delta is computed server-side
    (current mastery minus the value already frozen in the decision's own
    input_snapshot), never taken from the client. Set is_transfer_question
    when the attempt is on a *different* question testing the same concept
    in a new context, not a repeat of the original question.
    """
    try:
        outcome = service.record_assessment_outcome(
            decision_id, user.id, body.question_attempt_id,
            is_transfer_question=body.is_transfer_question,
            baseline_question_attempt_id=body.baseline_question_attempt_id,
        )
    except AdaptationOutcomeNotFound:
        raise HTTPException(status_code=404, detail="Decision or question attempt not found")
    except InvalidOutcome as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "outcome_id": str(outcome.id),
        "outcome_type": outcome.outcome_type,
        "mastery_delta": outcome.mastery_delta,
        "transfer_success": bool(outcome.transfer_success) if outcome.transfer_success is not None else None,
        "hint_usage_delta": outcome.hint_usage_delta,
        "time_to_correct_delta": outcome.time_to_correct_delta,
    }


@router.get("/courses/{course_id}/adaptation-history")
def get_adaptation_history(
    course_id: UUID,
    user: User = Depends(get_current_user),
    service: AdaptationOutcomeService = Depends(_outcome_service),
):
    """
    What was recommended, why, and what happened afterward -- in
    chronological order. Ownership-checked the same way every other
    course-scoped resource is: a course the caller doesn't own is 404, not
    a filtered-down empty list.
    """
    try:
        return service.get_adaptation_history(course_id, user.id)
    except AdaptationOutcomeNotFound:
        raise HTTPException(status_code=404, detail="Course not found")
