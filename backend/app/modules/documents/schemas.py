from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel

class DocumentOut(BaseModel):
    id: UUID
    course_id: UUID
    filename: str
    role: str
    source_kind: str
    status: str
    size_bytes: int
    page_count: Optional[int]
    needs_input_reason: Optional[str]
    created_at: Optional[datetime]

class UploadIntentOut(BaseModel):
    intent_id: UUID
    upload_url: str
    required_headers: dict[str, str]
    expires_at: datetime


class DocumentMutationOut(DocumentOut):
    source_changed: bool = False
    replaced_filename: Optional[str] = None
    cleanup_pending: bool = False
    rebuild_job_id: Optional[UUID] = None
