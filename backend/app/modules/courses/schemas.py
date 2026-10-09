from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from app.modules.jobs.schemas import JobSummary


class CourseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    goal: Optional[str] = Field(default=None, max_length=2000)
    starting_confidence: Optional[int] = Field(default=None, ge=1, le=5)
    subject_id: Optional[UUID] = None
    new_subject_name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    builds_on_course_id: Optional[UUID] = None


class CourseUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    goal: Optional[str] = Field(default=None, max_length=2000)
    starting_confidence: Optional[int] = Field(default=None, ge=1, le=5)
    subject_id: Optional[UUID] = None
    builds_on_course_id: Optional[UUID] = None


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
    source_revision: int = 0
    source_count: int = 0
    latest_job: Optional[JobSummary] = None
    latest_review_version_id: Optional[UUID] = None
    active_version_id: Optional[UUID] = None
    eligible_as_earlier_course: bool = False
    subject_id: Optional[UUID] = None
    subject_name: Optional[str] = None
    builds_on_course_id: Optional[UUID] = None
    builds_on_course_title: Optional[str] = None
    builds_on_version_number: Optional[int] = None
    builds_on_sources_available: bool = False
    link_revision: int = 0
    reliable_linked_match_count: int = 0
    uncertain_linked_match_count: int = 0
    unsupported_linked_match_count: int = 0
    created_at: Optional[datetime]


class CourseSubjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class CourseSubjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str


class LinkedConceptMatchOut(BaseModel):
    id: UUID
    current_concept_id: UUID
    current_concept_name: str
    linked_concept_id: UUID
    linked_concept_name: str
    status: str
    has_source_support: bool
