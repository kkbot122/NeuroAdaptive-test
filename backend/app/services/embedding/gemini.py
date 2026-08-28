"""
Gemini implementation of EmbeddingGateway.

The frozen stack names gemini-embedding-001 (architecture.md, AGENTS.md §5).
The model id is read from settings, not hardcoded, so a model change is
configuration.
"""
import time
from typing import List

from app.core.config import settings
from app.services.embedding.gateway import EmbeddingError, EmbeddingGateway

# gemini-embedding-001's native output size. Recorded here as a named
# constant because Qdrant's collection vector size must match it exactly at
# collection-creation time; there is no per-request negotiation.
GEMINI_EMBEDDING_DIMENSIONS = 3072

# Verified empirically against the live free-tier API on 2026-08-29: a batch
# of 50 realistic-sized chunks raised ResourceExhausted, while repeated
# batches of 20 also failed after the first call, and batches of 10 sent
# every ~2s stayed reliable. Kept well under all of those.
_MAX_BATCH_SIZE = 10

# Retry budget for a rate-limited (not a genuinely unavailable) provider.
# This is ordinary resilience against a transient, expected free-tier limit,
# not the "automatic provider fallback" frozen-scope.md forbids -- it is the
# same provider, retried after backing off, never a different one.
_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = (10, 20, 40)


def _is_rate_limit_error(exc: Exception) -> bool:
    # google.api_core.exceptions.ResourceExhausted is the specific type, but
    # matched by name here rather than imported, so this gateway does not
    # need a direct dependency on google.api_core's exception module layout.
    return type(exc).__name__ == "ResourceExhausted"


class GeminiEmbeddingGateway(EmbeddingGateway):
    def __init__(self, api_key: str = None, model: str = None):
        self._api_key = api_key or settings.GEMINI_API_KEY
        self._model = model or settings.GEMINI_EMBEDDING_MODEL
        self._client = None  # built lazily; see _ensure_configured

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return GEMINI_EMBEDDING_DIMENSIONS

    def _ensure_configured(self):
        # Imported and configured lazily, for the same reason the Groq client
        # became lazy (K-14): importing this module must not require a live
        # API key, or the whole app becomes unimportable without one.
        import google.generativeai as genai

        if self._client is None:
            genai.configure(api_key=self._api_key)
            self._client = genai
        return self._client

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        if len(texts) > _MAX_BATCH_SIZE:
            raise EmbeddingError(
                f"Batch of {len(texts)} exceeds the {_MAX_BATCH_SIZE}-text limit "
                "this gateway sends per request."
            )

        client = self._ensure_configured()
        model_path = (
            self._model if self._model.startswith("models/") else f"models/{self._model}"
        )

        result = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                result = client.embed_content(model=model_path, content=texts)
                break
            except Exception as exc:
                is_last_attempt = attempt == _MAX_RETRIES
                if _is_rate_limit_error(exc) and not is_last_attempt:
                    time.sleep(_RETRY_BACKOFF_SECONDS[attempt])
                    continue
                if is_last_attempt and _is_rate_limit_error(exc):
                    raise EmbeddingError(
                        f"Gemini embedding call failed after {_MAX_RETRIES} retries: "
                        f"{type(exc).__name__}"
                    ) from exc
                raise EmbeddingError(
                    f"Gemini embedding call failed: {type(exc).__name__}"
                ) from exc

        embeddings = result.get("embedding")
        if embeddings is None:
            raise EmbeddingError("Gemini response carried no embedding field.")

        # Verified empirically (not assumed): passing `content` as a list,
        # even a list of one, always returns a list of vectors -- never one
        # flat vector for a single-item batch.
        if len(embeddings) != len(texts):
            raise EmbeddingError(
                f"Gemini returned {len(embeddings)} embeddings for {len(texts)} inputs."
            )

        return embeddings
