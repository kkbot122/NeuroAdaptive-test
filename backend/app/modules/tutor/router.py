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
from app.services.providers import generation_gateway, embedding_gateway, vector_store
from app.modules.tutor.schemas import LessonContentOut, TutorEvent, TutorHistoryOut, TutorFailureOut
import json
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse
from fastapi.responses import JSONResponse

from app.core.rate_limit import generation_slot
from app.core.security import get_current_user
from app.db.session import get_db
from app.modules.abuse.service import AbuseControlService
from app.services.ai_usage import ai_usage_scope
from app.modules.abuse.models import AIProviderCall
from app.modules.abuse.usage import usage_totals
from app.modules.auth.models import User
from app.modules.learning.models import AssessmentSession, AssessmentStatus, LearningActivity
from app.modules.tutor.models import GroundingMode
from app.modules.tutor.service import TutorNotFound, TutorService, TutorUnavailable
from app.services.embedding.gemini import GeminiEmbeddingGateway
from app.services.generation.gemini import GeminiGenerationGateway
from app.services.vectorstore.pgvector_store import PgVectorStore

MAX_CONCURRENT_GENERATIONS_PER_USER = 2

router = APIRouter()


def _service(db: Session = Depends(get_db)) -> TutorService:
    return TutorService(
        db,
        generation_gateway(),
        embedding_gateway(),
        vector_store(db),
    )


class TutorQuestionIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    context_lesson_id: Optional[UUID] = None
    conversation_id: Optional[UUID] = None
    # Reproducibility (Phase 7): set when this question follows directly
    # from an AdaptationDecision the caller is acting on.
    decision_id: Optional[UUID] = None


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
            "token_usage": result.token_usage,
            "operation_id": str(result.operation_id),
        },
    )


def _tutor_available(db: Session, user_id: int) -> bool:
    open_session = (
        db.query(AssessmentSession.id)
        .join(LearningActivity, AssessmentSession.activity_id == LearningActivity.id)
        .filter(
            LearningActivity.owner_id == user_id,
            AssessmentSession.status == AssessmentStatus.OPEN.value,
        )
        .first()
    )
    return open_session is None


def _ensure_tutor_available(db: Session, user_id: int) -> None:
    if not _tutor_available(db, user_id):
        raise HTTPException(status_code=409, detail="Tutor assistance is unavailable during an active assessment.")


def _unavailable_response(exc: TutorUnavailable):
    return JSONResponse(status_code=503, content={
        "error_category": exc.category,
        "detail": "The tutor could not verify an answer right now. Your conversation is saved; please try again.",
    })


@router.get("/courses/{course_id}/tutor/history", response_model=TutorHistoryOut)
def tutor_history(
    course_id: UUID, conversation_id: UUID, response: Response,
    context_lesson_id: Optional[UUID] = None, decision_id: Optional[UUID] = None,
    before: Optional[UUID] = None, user: User = Depends(get_current_user),
    service: TutorService = Depends(_service), db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    try:
        history = service.get_history(course_id, user.id, conversation_id, context_lesson_id, decision_id, before)
    except TutorNotFound:
        raise HTTPException(status_code=404, detail="Tutor conversation not found")
    available = _tutor_available(db, user.id)
    return {**history, "available": available, "turns": history["turns"] if available else [],
            "has_more": history["has_more"] if available else False}


class TutorStream(StreamingResponse):
    media_type = "text/event-stream"


@router.post("/courses/{course_id}/tutor", response_class=TutorStream,
             responses={
                 200: {"model": TutorEvent, "description": "SSE frames: event name plus JSON data from TutorEvent. Full answer is validated before emission."},
                 409: {"description": "Tutor assistance is unavailable while an assessment is open."},
                 503: {"model": TutorFailureOut, "description": "Generation or verification is unavailable; no insufficient-evidence turn is saved."},
             })
def ask_tutor(
    course_id: UUID,
    body: TutorQuestionIn,
    user: User = Depends(get_current_user),
    service: TutorService = Depends(_service),
    db: Session = Depends(get_db),
):
    _ensure_tutor_available(db, user.id)
    AbuseControlService(db).enforce_generation_request_controls(user.id)
    try:
        with generation_slot(f"user:{user.id}", MAX_CONCURRENT_GENERATIONS_PER_USER), ai_usage_scope(db, user.id, "tutor", course_id) as usage:
            result = service.ask(
                course_id, user.id, body.question,
                context_lesson_id=body.context_lesson_id, conversation_id=body.conversation_id,
                decision_id=body.decision_id,
            )
            result.token_usage = usage_totals(db.query(AIProviderCall).filter_by(owner_id=user.id, operation_id=usage.operation_id))
            result.operation_id = usage.operation_id
    except TutorNotFound:
        raise HTTPException(status_code=404, detail="Course not found")
    except TutorUnavailable as exc:
        return _unavailable_response(exc)

    _ensure_tutor_available(db, user.id)
    return StreamingResponse(_stream_events(result), media_type="text/event-stream")


_VALID_FORMATS = {"concise", "detailed", "worked_example", "analogy", "diagram", "source_view", "quiz_first"}


@router.get(
    "/courses/{course_id}/lessons/{lesson_id}/content",
    response_model=LessonContentOut,
    responses={409: {"description": "Tutor assistance is unavailable while an assessment is open."},
               503: {"model": TutorFailureOut, "description": "Generation or verification is unavailable"}},
)
def get_lesson_content(
    course_id: UUID,
    lesson_id: UUID,
    format: str = "detailed",
    decision_id: Optional[UUID] = None,
    user: User = Depends(get_current_user),
    service: TutorService = Depends(_service),
    db: Session = Depends(get_db),
):
    """
    Real, grounded lesson content -- not a placeholder. Plain JSON, not SSE:
    unlike the tutor chat, there is no streaming UX need here, so this
    returns TutorService's already-computed result directly.

    `decision_id`: pass the AdaptationDecision id when this lesson is being
    fetched because it was the recommended next activity, so the resulting
    TutorMessage carries that provenance (Phase 7).
    """
    _ensure_tutor_available(db, user.id)
    if format not in _VALID_FORMATS:
        raise HTTPException(status_code=422, detail=f"format must be one of {sorted(_VALID_FORMATS)}")
    AbuseControlService(db).enforce_generation_request_controls(user.id)
    try:
        with generation_slot(f"user:{user.id}", MAX_CONCURRENT_GENERATIONS_PER_USER), ai_usage_scope(db, user.id, "lesson_content", course_id):
            result = service.generate_lesson_content(course_id, user.id, lesson_id, format, decision_id=decision_id)
    except TutorNotFound:
        raise HTTPException(status_code=404, detail="Course or lesson not found")
    except TutorUnavailable as exc:
        return _unavailable_response(exc)

    _ensure_tutor_available(db, user.id)
    return {
        "content_markdown": result.answer_markdown,
        "citations": [
            {"claim": c.claim, "chunk_id": c.chunk_id, "validation_status": c.validation_status}
            for c in result.citations
        ],
        "grounding_mode": result.grounding_mode,
        "retrieved_chunk_ids": result.retrieved_chunk_ids,
    }
