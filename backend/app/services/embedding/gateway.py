"""
Embedding provider abstraction.

Business logic (the indexing stage, retrieval) depends only on
EmbeddingGateway, never on a vendor SDK directly, so the provider is
swappable and a unit test can substitute a fake without touching real
network or a real API key.
"""
from abc import ABC, abstractmethod
from typing import List


class EmbeddingError(Exception):
    """Raised when the provider cannot produce an embedding for the input."""


class EmbeddingGateway(ABC):
    """One embedding vector per input string, in the same order as the input."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Identifier recorded on every chunk this gateway embeds, so a later
        model change is visible per-row rather than assumed uniform."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Vector length this gateway produces. Must match the Qdrant
        collection's configured vector size."""

    @abstractmethod
    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Embed a batch. Raises EmbeddingError on provider failure; never
        returns a partial or padded result -- the caller must be able to
        zip(texts, result) safely or know nothing succeeded."""
