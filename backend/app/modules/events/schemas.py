from typing import List, Optional

from pydantic import BaseModel, Field

# Bounds exist so a client cannot inflate its own engagement signal. A single
# pulse represents at most one minute; a batch carries at most 100 events.
MAX_SECONDS_PER_EVENT = 60
MAX_EVENTS_PER_BATCH = 100

ALLOWED_DIMENSIONS = {"textual", "visual", "logic", "structural"}


class LearningEventIn(BaseModel):
    event_type: str = Field(min_length=1, max_length=64)
    dimension: Optional[str] = Field(default=None, max_length=32)
    seconds: int = Field(default=0, ge=0, le=MAX_SECONDS_PER_EVENT)
    target_id: Optional[str] = Field(default=None, max_length=64)
    payload: dict = Field(default_factory=dict)


class EventBatchIn(BaseModel):
    events: List[LearningEventIn] = Field(min_length=1, max_length=MAX_EVENTS_PER_BATCH)


class EventBatchOut(BaseModel):
    accepted: int
    rejected: int
