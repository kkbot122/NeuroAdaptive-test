"""Qdrant implementation of VectorStore."""
from typing import List
from uuid import UUID

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from app.core.config import settings
from app.services.vectorstore.store import (
    ScoredPoint,
    VectorPoint,
    VectorStore,
    VectorStoreError,
)


class QdrantVectorStore(VectorStore):
    def __init__(self, url: str = None):
        self._url = url or settings.QDRANT_URL
        self._client = None

    def _ensure_client(self) -> QdrantClient:
        # Lazy, same reasoning as the Groq and Gemini clients: importing or
        # constructing this must not require a reachable Qdrant instance.
        if self._client is None:
            self._client = QdrantClient(url=self._url)
        return self._client

    def ensure_collection(self, name: str, dimensions: int) -> None:
        client = self._ensure_client()
        try:
            if client.collection_exists(name):
                return
            client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=dimensions, distance=Distance.COSINE),
            )
        except Exception as exc:
            raise VectorStoreError(f"Could not ensure collection '{name}': {type(exc).__name__}") from exc

    def upsert(self, collection: str, points: List[VectorPoint]) -> None:
        if not points:
            return
        client = self._ensure_client()
        try:
            client.upsert(
                collection_name=collection,
                points=[
                    PointStruct(id=str(p.id), vector=p.vector, payload=p.payload)
                    for p in points
                ],
            )
        except Exception as exc:
            raise VectorStoreError(f"Qdrant upsert failed: {type(exc).__name__}") from exc

    def delete(self, collection: str, point_ids: List[UUID]) -> None:
        if not point_ids:
            return
        client = self._ensure_client()
        try:
            client.delete(collection_name=collection, points_selector=[str(i) for i in point_ids])
        except Exception as exc:
            raise VectorStoreError(f"Qdrant delete failed: {type(exc).__name__}") from exc

    def search(
        self,
        collection: str,
        query_vector: List[float],
        owner_id: int,
        course_id: str,
        limit: int = 10,
    ) -> List[ScoredPoint]:
        client = self._ensure_client()

        # The ownership filter is built into the query object itself and
        # passed to Qdrant's own filtering engine -- Qdrant excludes
        # non-matching points before scoring/ranking, not after. There is no
        # separate step here that could be skipped or gotten wrong per call
        # site.
        query_filter = Filter(
            must=[
                FieldCondition(key="owner_id", match=MatchValue(value=owner_id)),
                FieldCondition(key="course_id", match=MatchValue(value=str(course_id))),
            ]
        )

        try:
            response = client.query_points(
                collection_name=collection,
                query=query_vector,
                query_filter=query_filter,
                limit=limit,
            )
        except Exception as exc:
            raise VectorStoreError(f"Qdrant search failed: {type(exc).__name__}") from exc

        return [
            ScoredPoint(id=point.id, score=point.score, payload=point.payload or {})
            for point in response.points
        ]
