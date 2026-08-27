import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy.types import JSON

from app.db.base import Base


class LearningEvent(Base):
    """
    One observed learner behaviour.

    This is the table the telemetry path was always meant to write to. Until
    2026-08-28 the three Tracked* components posted to /api/v1/profile/pulse,
    which did not exist, so every pulse 404'd while the client logged success.

    New tables use UUID primary keys per SYSTEM_ARCHITECTURE.md §8. Event ids
    in particular should not be guessable or enumerable.
    """

    __tablename__ = "learning_events"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # e.g. "paragraph_view", "image_view", "code_view"
    event_type = Column(String, nullable=False, index=True)

    # Which presentation dimension this is evidence about: "textual",
    # "visual", "logic", "structural". Nullable because not every event is.
    dimension = Column(String, nullable=True)

    # Seconds of attention this event represents. Bounded by the API layer.
    seconds = Column(Integer, nullable=False, default=0)

    # The element observed, when there is one (paragraph id, image id, ...).
    target_id = Column(String, nullable=True)

    # Anything else the client sent, kept verbatim for later analysis.
    payload = Column(JSON, nullable=False, default=dict)

    occurred_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User")
