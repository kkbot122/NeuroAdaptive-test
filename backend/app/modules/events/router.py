from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.events.models import LearningEvent
from app.modules.events.schemas import (
    ALLOWED_DIMENSIONS,
    EventBatchIn,
    EventBatchOut,
    LearningEventIn,
)

router = APIRouter()


def _persist(event: LearningEventIn, user: User) -> LearningEvent:
    # An unrecognised dimension is stored as null rather than rejected: the
    # event still carries timing evidence, and dropping the whole batch because
    # one label was misspelled loses more than it protects.
    dimension = event.dimension if event.dimension in ALLOWED_DIMENSIONS else None
    return LearningEvent(
        user_id=user.id,
        event_type=event.event_type,
        dimension=dimension,
        seconds=event.seconds,
        target_id=event.target_id,
        payload=event.payload or {},
    )


@router.post("/batch", response_model=EventBatchOut, status_code=202)
def record_batch(
    body: EventBatchIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Record a batch of learner events against the authenticated user.

    The user is never taken from the request body. 202 rather than 201: this is
    fire-and-forget telemetry and the client must not block on it.

    Privacy (Phase 6): a user with tracking_consent="minimal" declined
    behavioral telemetry -- this is purely LearningEvent/personalization
    data, never the core learning loop (mastery, adaptation, and
    assessment all live in separate tables this endpoint never touches), so
    declining costs nothing functional. Accepted with rejected=len(events)
    rather than a 403: the client sent a legitimate request, the server is
    just honoring a standing preference, not refusing the caller.
    """
    if user.tracking_consent == "minimal":
        return EventBatchOut(accepted=0, rejected=len(body.events))

    rows = [_persist(event, user) for event in body.events]
    db.add_all(rows)
    db.commit()
    return EventBatchOut(accepted=len(rows), rejected=0)
