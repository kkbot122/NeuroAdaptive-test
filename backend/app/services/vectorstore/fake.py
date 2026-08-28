"""
An in-memory VectorStore for tests. No network, no Qdrant instance required.

Implements real cosine similarity and a real filter step, so a test against
this fake is exercising the same *contract* -- ownership filtering happens
before ranking, not after -- as a test against real Qdrant would, just
without the running service.
"""
import math
from typing import Dict, List
from uuid import UUID

from app.services.vectorstore.store import ScoredPoint, VectorPoint, VectorStore


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class FakeVectorStore(VectorStore):
    def __init__(self):
        self._collections: Dict[str, Dict[str, VectorPoint]] = {}

    def ensure_collection(self, name: str, dimensions: int) -> None:
        self._collections.setdefault(name, {})

    def upsert(self, collection: str, points: List[VectorPoint]) -> None:
        store = self._collections.setdefault(collection, {})
        for point in points:
            store[str(point.id)] = point

    def delete(self, collection: str, point_ids: List[UUID]) -> None:
        store = self._collections.get(collection, {})
        for point_id in point_ids:
            store.pop(str(point_id), None)

    def search(
        self,
        collection: str,
        query_vector: List[float],
        owner_id: int,
        course_id: str,
        limit: int = 10,
    ) -> List[ScoredPoint]:
        store = self._collections.get(collection, {})

        # The filter is applied to the candidate set BEFORE scoring, exactly
        # mirroring what Qdrant's query_filter does server-side -- there is
        # no step here where an unfiltered result set exists and gets pared
        # down afterward.
        candidates = [
            point
            for point in store.values()
            if point.payload.get("owner_id") == owner_id
            and point.payload.get("course_id") == str(course_id)
        ]

        scored = [
            ScoredPoint(id=point.id, score=_cosine(query_vector, point.vector), payload=point.payload)
            for point in candidates
        ]
        scored.sort(key=lambda p: p.score, reverse=True)
        return scored[:limit]
