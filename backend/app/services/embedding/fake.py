"""
A deterministic, offline EmbeddingGateway for tests.

Never calls a network. The vector for a given text is stable across calls
(same text -> same vector), so tests can assert similarity/ordering without
depending on real model output or a live API key.
"""
import hashlib
from typing import List

from app.services.embedding.gateway import EmbeddingGateway

FAKE_DIMENSIONS = 32


class FakeEmbeddingGateway(EmbeddingGateway):
    def __init__(self, dimensions: int = FAKE_DIMENSIONS):
        self._dimensions = dimensions

    @property
    def model_name(self) -> str:
        return "fake-embedding-gateway"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> List[float]:
        # Deterministic pseudo-embedding: hash the text into `dimensions`
        # floats in [-1, 1]. Not semantically meaningful, but stable and
        # reproducible, which is all a gateway-contract test needs.
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values = []
        for i in range(self._dimensions):
            byte = digest[i % len(digest)]
            values.append((byte / 255.0) * 2 - 1)
        return values
