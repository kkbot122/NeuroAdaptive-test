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

from app.modules.courses.models import Course
from app.modules.courses.service import CourseNotFound, CourseService
from app.modules.documents.chunk_models import Chunk
from app.modules.documents.models import Document
from app.modules.retrieval.lexical import search_lexical
from app.services.embedding.gateway import EmbeddingError, EmbeddingGateway
from app.services.vectorstore.store import CHUNKS_COLLECTION, VectorStore, VectorStoreError


@dataclass
class ChunkDetail:
    id: UUID
    document_id: UUID
    filename: str
    text: str
    heading_path: Optional[str]
    page_start: Optional[int]
    page_end: Optional[int]
    source_course_id: UUID
    source_course_title: str
    source_version_id: Optional[UUID]
    is_linked_source: bool


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
    source_course_id: UUID
    source_course_title: str
    source_version_id: Optional[UUID]
    is_linked_source: bool


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
            current = self.courses.get_owned(course_id, owner_id)
        except CourseNotFound:
            raise RetrievalNotAuthorized(str(course_id))

        allowed_courses = self._authorized_course_scope(current, owner_id)
        course_ids = list(allowed_courses)

        vector_hits: dict = {}
        try:
            query_vector = self.embeddings.embed_texts([query])[0]
            for scoped_course_id in course_ids:
                for point in self.vectors.search(
                    CHUNKS_COLLECTION, query_vector, owner_id, str(scoped_course_id), limit=limit
                ):
                    vector_hits[str(point.id)] = point.score
        except (EmbeddingError, VectorStoreError):
            # A retrieval-quality degradation, not an authorization or
            # correctness failure: lexical search still runs. Nothing here
            # widens what is returned -- only vector recall is reduced.
            vector_hits = {}

        lexical_hits: dict = {}
        for scoped_course_id in course_ids:
            for chunk, score in search_lexical(self.db, owner_id, scoped_course_id, query, limit=limit):
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
        hydrated = (
            self.db.query(Chunk, Document.filename, Course.title)
            .join(Document, Document.id == Chunk.document_id)
            .join(Course, Course.id == Chunk.course_id)
            .filter(
                Chunk.id.in_(chunk_uuids), Chunk.owner_id == owner_id,
                Chunk.course_id.in_(course_ids), Document.owner_id == owner_id,
                Document.course_id.in_(course_ids), Course.owner_id == owner_id,
            ).all()
        )
        chunks_by_id = {str(chunk.id): (chunk, filename, title) for chunk, filename, title in hydrated}

        results = []
        for chunk_id in all_ids:
            hydrated_row = chunks_by_id.get(chunk_id)
            if hydrated_row is None:
                continue  # id came back from a store but the row is gone/not ours
            chunk, _filename, title = hydrated_row
            source_course_id = chunk.course_id
            source_metadata = allowed_courses.get(source_course_id)
            if source_metadata is None:
                continue

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
                    source_course_id=source_course_id,
                    source_course_title=title,
                    source_version_id=source_metadata[0],
                    is_linked_source=source_metadata[1],
                )
            )

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def get_chunk(self, course_id: UUID, owner_id: int, chunk_id: UUID) -> ChunkDetail:
        """
        Open one cited chunk by id -- the source-viewer's read. Same
        ownership-inside-the-query property as search(): a chunk from a
        different course or a different owner is indistinguishable from a
        chunk that does not exist, both raise RetrievalNotAuthorized.
        """
        try:
            current = self.courses.get_owned(course_id, owner_id)
        except CourseNotFound:
            raise RetrievalNotAuthorized(str(course_id))

        allowed_courses = self._authorized_course_scope(current, owner_id)
        allowed_ids = list(allowed_courses)

        row = (
            self.db.query(Chunk, Document.filename, Course.title)
            .join(Document, Chunk.document_id == Document.id)
            .join(Course, Course.id == Chunk.course_id)
            .filter(
                Chunk.id == chunk_id,
                Chunk.course_id.in_(allowed_ids),
                Chunk.owner_id == owner_id,
                Document.owner_id == owner_id,
                Document.course_id.in_(allowed_ids),
                Course.owner_id == owner_id,
            )
            .first()
        )
        if row is None:
            raise RetrievalNotAuthorized(str(chunk_id))
        chunk, filename, course_title = row
        source_version_id, linked_source = allowed_courses[chunk.course_id]
        return ChunkDetail(
            id=chunk.id, document_id=chunk.document_id, filename=filename, text=chunk.text,
            heading_path=chunk.heading_path, page_start=chunk.page_start, page_end=chunk.page_end,
            source_course_id=chunk.course_id, source_course_title=course_title,
            source_version_id=source_version_id, is_linked_source=linked_source,
        )

    def _authorized_course_scope(self, current, owner_id: int) -> dict[UUID, tuple[UUID | None, bool]]:
        """Return current plus the one exact, direct published link, if valid."""
        from app.modules.curriculum.models import CourseVersion

        current_version = None
        if current.active_version_id is not None:
            current_version = self.db.query(CourseVersion.id).filter_by(
                id=current.active_version_id, course_id=current.id, owner_id=owner_id, status="READY"
            ).scalar()
        allowed: dict[UUID, tuple[UUID | None, bool]] = {
            current.id: (current_version, False),
        }
        if (
            current.status != "PUBLISHED" or current.link_revoked_at is not None
            or current.linked_course_id is None or current.linked_version_id is None
        ):
            return allowed
        linked_is_valid = self.db.query(CourseVersion.id).filter(
            CourseVersion.id == current.linked_version_id,
            CourseVersion.course_id == current.linked_course_id,
            CourseVersion.owner_id == owner_id,
            CourseVersion.status == "READY",
            self.db.query(Course.id).filter(
                Course.id == current.linked_course_id,
                Course.owner_id == owner_id,
                Course.status == "PUBLISHED",
            ).exists(),
        ).scalar()
        if linked_is_valid is not None:
            allowed[current.linked_course_id] = (current.linked_version_id, True)
        return allowed
