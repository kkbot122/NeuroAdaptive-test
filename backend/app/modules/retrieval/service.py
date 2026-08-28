"""
Retrieval service: the one place a query is turned into chunks.

The ownership filter (course_id/owner_id) is applied INSIDE both the vector
query (VectorStore.search's mandatory owner_id/course_id parameters) and the
lexical query (search_lexical's WHERE clause) -- never as a filter over
results returned from an unscoped query. This is the mandate's specific,
structural security property: a post-filter can leak another user's data
through timing, counts, or partial results even when the filtered-out text
is never shown.

Course ownership is verified once, up front, via CourseService.get_owned --
the same accessor every other module uses -- so an unowned course raises
before either search runs.

No reranking or fusion yet (explicitly out of scope this phase per the
mandate): results are a simple union of the two result sets, vector first,
deduplicated by chunk id.
"""
from dataclasses import dataclass
from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.courses.service import CourseNotFound, CourseService
from app.modules.documents.chunk_models import Chunk
from app.modules.retrieval.lexical import search_lexical
from app.services.embedding.gateway import EmbeddingError, EmbeddingGateway
from app.services.vectorstore.store import VectorStore, VectorStoreError

CHUNKS_COLLECTION = "course_chunks"


@dataclass
class RetrievedChunk:
    id: UUID
    document_id: UUID
    text: str
    heading_path: Optional[str]
    content_type: str
    page_start: Optional[int]
    page_end: Optional[int]
    char_start: Optional[int]
    char_end: Optional[int]
    score: float
    source: str  # "vector" | "lexical" | "both"


class RetrievalNotAuthorized(Exception):
    """Course does not exist, or is not owned by the caller."""


class RetrievalService:
    def __init__(self, db: Session, embeddings: EmbeddingGateway, vectors: VectorStore):
        self.db = db
        self.embeddings = embeddings
        self.vectors = vectors
        self.courses = CourseService(db)

    def search(
        self, course_id: UUID, owner_id: int, query: str, limit: int = 10
    ) -> List[RetrievedChunk]:
        try:
            self.courses.get_owned(course_id, owner_id)
        except CourseNotFound:
            raise RetrievalNotAuthorized(str(course_id))

        vector_hits: dict = {}
        try:
            query_vector = self.embeddings.embed_texts([query])[0]
            for point in self.vectors.search(
                CHUNKS_COLLECTION, query_vector, owner_id, str(course_id), limit=limit
            ):
                vector_hits[str(point.id)] = point.score
        except (EmbeddingError, VectorStoreError):
            # A retrieval-quality degradation, not an authorization or
            # correctness failure: lexical search still runs. Nothing here
            # widens what is returned -- only vector recall is reduced.
            vector_hits = {}

        lexical_hits: dict = {}
        for chunk, score in search_lexical(self.db, owner_id, course_id, query, limit=limit):
            lexical_hits[str(chunk.id)] = score

        all_ids = list(vector_hits.keys())
        for chunk_id in lexical_hits:
            if chunk_id not in vector_hits:
                all_ids.append(chunk_id)

        if not all_ids:
            return []

        # Defense in depth: re-applied here even though both upstream
        # searches already filtered by owner/course. If either store's filter
        # were ever wrong, this final hydration step still cannot return a
        # chunk belonging to a different owner or course.
        from uuid import UUID as _UUID

        chunk_uuids = [_UUID(cid) if not isinstance(cid, _UUID) else cid for cid in all_ids]
        chunks_by_id = {
            str(c.id): c
            for c in self.db.query(Chunk)
            .filter(Chunk.id.in_(chunk_uuids), Chunk.owner_id == owner_id, Chunk.course_id == course_id)
            .all()
        }

        results = []
        for chunk_id in all_ids:
            chunk = chunks_by_id.get(chunk_id)
            if chunk is None:
                continue  # id came back from a store but the row is gone/not ours

            in_vector = chunk_id in vector_hits
            in_lexical = chunk_id in lexical_hits
            source = "both" if (in_vector and in_lexical) else ("vector" if in_vector else "lexical")
            score = vector_hits.get(chunk_id, lexical_hits.get(chunk_id, 0.0))

            results.append(
                RetrievedChunk(
                    id=chunk.id,
                    document_id=chunk.document_id,
                    text=chunk.text,
                    heading_path=chunk.heading_path,
                    content_type=chunk.content_type,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    score=score,
                    source=source,
                )
            )

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]
