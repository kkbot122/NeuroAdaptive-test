from typing import Optional
from uuid import UUID
from pydantic import BaseModel

class ChunkDetail(BaseModel):
    chunk_id: UUID
    document_id: UUID
    filename: str
    text: str
    heading_path: Optional[str]
    page_start: Optional[int]
    page_end: Optional[int]
    source_course_id: UUID
    source_course_title: str
    source_version_id: Optional[UUID] = None
    is_linked_source: bool = False

class RetrievalOut(BaseModel):
    chunk_id: UUID
    document_id: UUID
    text: str
    heading_path: Optional[str]
    content_type: str
    page_start: Optional[int]
    page_end: Optional[int]
    char_start: Optional[int]
    char_end: Optional[int]
    score: float
    source: str
    source_course_id: UUID
    source_course_title: str
    source_version_id: Optional[UUID] = None
    is_linked_source: bool = False
