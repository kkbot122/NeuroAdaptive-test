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
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.modules.adaptation.service import (
    AdaptationNotFound,
    AdaptationPersistenceError,
    AdaptationService,
)
from app.modules.auth.models import User
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
