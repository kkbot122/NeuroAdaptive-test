from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CourseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    goal: Optional[str] = Field(default=None, max_length=2000)
    starting_confidence: Optional[int] = Field(default=None, ge=1, le=5)


class CourseUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    goal: Optional[str] = Field(default=None, max_length=2000)
    starting_confidence: Optional[int] = Field(default=None, ge=1, le=5)


class CourseOut(BaseModel):
    # owner_id is deliberately absent: the caller is always the owner, so
    # returning it adds nothing and leaks an internal identifier.
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    goal: Optional[str]
    starting_confidence: Optional[int]
    status: str
    sources_finalized_at: Optional[datetime]
    created_at: Optional[datetime]
