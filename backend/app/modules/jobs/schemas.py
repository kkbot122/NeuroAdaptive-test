from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel

class StageOut(BaseModel):
    name: str
    position: int
    status: str
    attempts: int
    error_category: Optional[str]
    input_count: int
    output_count: int
    provider_call_count: int
    started_at: Optional[datetime]
    finished_at: Optional[datetime]

class JobSummary(BaseModel):
    id: UUID
    status: str
    current_stage: Optional[str]
    error_category: Optional[str]

class JobOut(JobSummary):
    course_id: UUID
    source_revision: int = 0
    retry_available: bool = False
    retry_count: int
    error_detail: Optional[str]
    stages: list[StageOut]
