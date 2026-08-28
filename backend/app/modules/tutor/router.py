"""
Grounded-tutor API surface.

CONFLICT WITH THE PHASE 5 PACK, DECLARED: the pack's own test cases name
`POST /conversations/{id}/messages`. architecture.md's API surface list --
already treated as authoritative for this exact question in curriculum/
router.py and adaptation/router.py -- names `POST /courses/{courseId}/tutor`
instead, and its own schema section lists no `conversations` table at all.
Per AGENTS.md's authority order, architecture.md wins: the route below is
`/courses/{course_id}/tutor`. `conversation_id` is accepted as an optional,
client-supplied grouping key on the request body rather than a path segment
of a server-owned resource.

STREAMING SIMPLIFICATION, DECLARED: GenerationGateway (Phase 1) is a
one-shot `generate() -> str` interface, not a token stream. The SSE contract
below (retrieval -> token -> citation* -> done, or insufficient) is
implemented with a single `token` event carrying the complete, already
citation-validated answer, rather than incremental token chunks -- true
token-level streaming would require a new gateway capability this phase
does not add. The event *sequence and ordering* the mandate requires is
real; the granularity of the `token` event is coarser than the name
suggests.
"""
import json
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from app.core.security import get_current_user
from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.tutor.models import GroundingMode
from app.modules.tutor.service import TutorNotFound, TutorService
from app.services.embedding.gemini import GeminiEmbeddingGateway
from app.services.generation.gemini import GeminiGenerationGateway
from app.services.vectorstore.qdrant_store import QdrantVectorStore

router = APIRouter()


def _service(db: Session = Depends(get_db)) -> TutorService:
    return TutorService(db, GeminiGenerationGateway(), GeminiEmbeddingGateway(), QdrantVectorStore())


class TutorQuestionIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    context_lesson_id: Optional[UUID] = None
    conversation_id: Optional[UUID] = None


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _stream_events(result):
    yield _sse("retrieval", {"chunk_ids": result.retrieved_chunk_ids})

    if result.grounding_mode == GroundingMode.INSUFFICIENT.value:
        yield _sse("insufficient", {"message_id": str(result.message_id), "text": result.answer_markdown})
        return

    yield _sse("token", {"text": result.answer_markdown})
    for citation in result.citations:
        yield _sse(
            "citation",
            {"claim": citation.claim, "chunk_id": citation.chunk_id, "validation_status": citation.validation_status},
        )
    yield _sse(
        "done",
        {
            "message_id": str(result.message_id),
            "grounding_mode": result.grounding_mode,
            "token_usage": {"note": "not tracked -- GenerationGateway does not report usage this phase"},
        },
    )


@router.post("/courses/{course_id}/tutor")
def ask_tutor(
    course_id: UUID,
    body: TutorQuestionIn,
    user: User = Depends(get_current_user),
    service: TutorService = Depends(_service),
):
    try:
        result = service.ask(
            course_id, user.id, body.question,
            context_lesson_id=body.context_lesson_id, conversation_id=body.conversation_id,
        )
    except TutorNotFound:
        raise HTTPException(status_code=404, detail="Course not found")

    return StreamingResponse(_stream_events(result), media_type="text/event-stream")


_VALID_FORMATS = {"concise", "detailed", "worked_example", "analogy", "diagram", "source_view", "quiz_first"}


@router.get("/courses/{course_id}/lessons/{lesson_id}/content")
def get_lesson_content(
    course_id: UUID,
    lesson_id: UUID,
    format: str = "detailed",
    user: User = Depends(get_current_user),
    service: TutorService = Depends(_service),
):
    """
    Real, grounded lesson content -- not a placeholder. Plain JSON, not SSE:
    unlike the tutor chat, there is no streaming UX need here, so this
    returns TutorService's already-computed result directly.
    """
    if format not in _VALID_FORMATS:
        raise HTTPException(status_code=422, detail=f"format must be one of {sorted(_VALID_FORMATS)}")
    try:
        result = service.generate_lesson_content(course_id, user.id, lesson_id, format)
    except TutorNotFound:
        raise HTTPException(status_code=404, detail="Course or lesson not found")

    return {
        "content_markdown": result.answer_markdown,
        "citations": [
            {"claim": c.claim, "chunk_id": c.chunk_id, "validation_status": c.validation_status}
            for c in result.citations
        ],
        "grounding_mode": result.grounding_mode,
        "retrieved_chunk_ids": result.retrieved_chunk_ids,
    }
