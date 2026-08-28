"""
Vector store abstraction over Qdrant.

Retrieval code depends on this interface, not on qdrant_client directly, for
the same reason the embedding gateway is abstracted: swappable provider,
testable without a running service.

The one property every method here must preserve: an owner/course filter is
part of the query sent to the store, never a filter applied to results after
they come back. Post-filtering can leak another user's data through timing,
counts, or partial results even when the filtered-out text is never returned
-- this is the mandate's explicit reasoning and it is enforced structurally
here, not by convention at each call site.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional
from uuid import UUID


@dataclass
class VectorPoint:
    id: UUID
    vector: List[float]
    payload: Dict


@dataclass
class ScoredPoint:
    id: UUID
    score: float
    payload: Dict


class VectorStoreError(Exception):
    """Raised on a store-level failure (connection, collection mismatch)."""


class VectorStore(ABC):
    @abstractmethod
    def ensure_collection(self, name: str, dimensions: int) -> None:
        """Idempotent: create the collection if absent, no-op if it already
        exists with a compatible configuration."""

    @abstractmethod
    def upsert(self, collection: str, points: List[VectorPoint]) -> None:
        """Insert or overwrite by id. Re-upserting the same id replaces it,
        which is what makes chunk re-indexing after a reprocess idempotent."""

    @abstractmethod
    def delete(self, collection: str, point_ids: List[UUID]) -> None:
        """Remove points, e.g. chunks a rerun no longer produces."""

    @abstractmethod
    def search(
        self,
        collection: str,
        query_vector: List[float],
        owner_id: int,
        course_id: str,
        limit: int = 10,
    ) -> List[ScoredPoint]:
        """
        Vector similarity search, with owner_id and course_id applied INSIDE
        the query as a mandatory filter -- never as a post-filter over an
        unscoped result set. An implementation that filters afterward
        violates this interface's contract even if it happens to return the
        right rows in a given test.
        """
