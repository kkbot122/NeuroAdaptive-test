from app.modules.abuse.schemas import AIUsageTotalsOut
from typing import Literal
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel

class CitationOut(BaseModel):
    claim: str
    chunk_id: str
    validation_status: str

class TutorTurnOut(BaseModel):
    id: UUID
    question: str
    answer_markdown: str
    citations: list[CitationOut]
    grounding_mode: str
    created_at: datetime

class TutorHistoryOut(BaseModel):
    conversation_id: UUID
    available: bool
    turns: list[TutorTurnOut]
    has_more: bool

class TutorFailureOut(BaseModel):
    detail: str
    error_category: Literal["PROVIDER_UNAVAILABLE", "RESPONSE_INVALID", "VALIDATION_UNAVAILABLE"]

class LessonContentOut(BaseModel):
    content_markdown: str
    citations: list[CitationOut]
    grounding_mode: str
    retrieved_chunk_ids: list[str]

class TutorRetrieval(BaseModel):
    chunk_ids: list[str]

class TutorToken(BaseModel):
    text: str

class TutorDone(BaseModel):
    message_id: UUID
    grounding_mode: str
    token_usage: AIUsageTotalsOut
    operation_id: UUID

class TutorInsufficient(BaseModel):
    message_id: UUID
    text: str

class TutorEvent(BaseModel):
    event: Literal["retrieval", "token", "citation", "done", "insufficient"]
    data: TutorRetrieval | TutorToken | CitationOut | TutorDone | TutorInsufficient
