from app.modules.retrieval.schemas import ChunkDetail, RetrievalOut
from app.services.providers import generation_gateway, embedding_gateway, vector_store
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.services.ai_usage import ai_usage_scope
from app.modules.auth.models import User
from app.modules.retrieval.service import RetrievalNotAuthorized, RetrievalService
from app.services.embedding.gemini import GeminiEmbeddingGateway
from app.services.vectorstore.pgvector_store import PgVectorStore

router = APIRouter()


def _service(db: Session = Depends(get_db)) -> RetrievalService:
    return RetrievalService(db, embedding_gateway(), vector_store(db))


@router.get("/courses/{course_id}/retrieval", response_model=list[RetrievalOut])
def retrieve(
    course_id: UUID,
    q: str = Query(min_length=1, max_length=1000),
    limit: int = Query(default=10, ge=1, le=50),
    user: User = Depends(get_current_user),
    service: RetrievalService = Depends(_service),
    db: Session = Depends(get_db),
):
    """
    Query chunks scoped to one course the caller owns.

    frozen-scope.md: retrieval is restricted to documents attached to the
    current learner's current course. The ownership filter lives inside
    RetrievalService's queries, not here -- this route only translates the
    result to JSON.
    """
    try:
        with ai_usage_scope(db, user.id, "retrieval", course_id):
            results = service.search(course_id, user.id, q, limit=limit)
    except RetrievalNotAuthorized:
        raise HTTPException(status_code=404, detail="Course not found")

    return [
        {
            "chunk_id": str(r.id),
            "document_id": str(r.document_id),
            "text": r.text,
            "heading_path": r.heading_path,
            "content_type": r.content_type,
            "page_start": r.page_start,
            "page_end": r.page_end,
            "char_start": r.char_start,
            "char_end": r.char_end,
            "score": r.score,
            "source": r.source,
            "source_course_id": str(r.source_course_id),
            "source_course_title": r.source_course_title,
            "source_version_id": str(r.source_version_id) if r.source_version_id else None,
            "is_linked_source": r.is_linked_source,
        }
        for r in results
    ]


@router.get("/courses/{course_id}/chunks/{chunk_id}", response_model=ChunkDetail)
def get_chunk(
    course_id: UUID,
    chunk_id: UUID,
    user: User = Depends(get_current_user),
    service: RetrievalService = Depends(_service),
):
    """The source-viewer's read: open one cited chunk by id."""
    try:
        chunk = service.get_chunk(course_id, user.id, chunk_id)
    except RetrievalNotAuthorized:
        raise HTTPException(status_code=404, detail="Chunk not found")

    return {
        "chunk_id": str(chunk.id),
        "document_id": str(chunk.document_id),
        "filename": chunk.filename,
        "text": chunk.text,
        "heading_path": chunk.heading_path,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "source_course_id": str(chunk.source_course_id),
        "source_course_title": chunk.source_course_title,
        "source_version_id": str(chunk.source_version_id) if chunk.source_version_id else None,
        "is_linked_source": chunk.is_linked_source,
    }
