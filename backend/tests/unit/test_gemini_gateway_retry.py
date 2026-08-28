"""
Unit tests for GeminiEmbeddingGateway's rate-limit retry behaviour.

Verified live against the real free-tier API on 2026-08-29: a batch of 50
realistic chunks raised ResourceExhausted, and repeated batches of 20 failed
after the first call. These tests pin the retry logic added in response,
without waiting on real backoff delays or touching the network.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.services.embedding.gateway import EmbeddingError
from app.services.embedding.gemini import (
    _MAX_BATCH_SIZE,
    _MAX_RETRIES,
    GeminiEmbeddingGateway,
)


class ResourceExhausted(Exception):
    """Stands in for google.api_core.exceptions.ResourceExhausted. Matched
    by type name in the gateway, not by import, so this fake is sufficient."""


class SomeOtherError(Exception):
    pass


def _gateway_with_mock_client():
    gateway = GeminiEmbeddingGateway(api_key="fake-key-for-testing")
    mock_genai = MagicMock()
    gateway._client = mock_genai
    return gateway, mock_genai


class TestBatchSizeLimit:
    def test_rejects_a_batch_larger_than_the_limit(self):
        gateway, _ = _gateway_with_mock_client()
        with pytest.raises(EmbeddingError, match="exceeds"):
            gateway.embed_texts(["x"] * (_MAX_BATCH_SIZE + 1))

    def test_batch_size_is_conservative_relative_to_the_provider_max(self):
        """
        Regression pin: this was 100 (the API's documented ceiling) until a
        real batch of 50 was rate-limited. Kept well under what live testing
        showed was reliable.
        """
        assert _MAX_BATCH_SIZE <= 10


class TestRateLimitRetry:
    def test_retries_on_resource_exhausted_then_succeeds(self):
        gateway, client = _gateway_with_mock_client()
        client.embed_content.side_effect = [
            ResourceExhausted("quota"),
            ResourceExhausted("quota"),
            {"embedding": [[0.1, 0.2]]},
        ]

        with patch("app.services.embedding.gemini.time.sleep") as sleep:
            result = gateway.embed_texts(["one text"])

        assert result == [[0.1, 0.2]]
        assert client.embed_content.call_count == 3
        assert sleep.call_count == 2  # one sleep per retry, not per attempt

    def test_gives_up_after_the_retry_budget_is_exhausted(self):
        gateway, client = _gateway_with_mock_client()
        client.embed_content.side_effect = ResourceExhausted("quota")

        with patch("app.services.embedding.gemini.time.sleep"):
            with pytest.raises(EmbeddingError, match="after"):
                gateway.embed_texts(["one text"])

        assert client.embed_content.call_count == _MAX_RETRIES + 1

    def test_non_rate_limit_errors_are_not_retried(self):
        """A genuinely broken request should fail fast, not spend the full
        retry budget on something backoff cannot fix."""
        gateway, client = _gateway_with_mock_client()
        client.embed_content.side_effect = SomeOtherError("bad request")

        with patch("app.services.embedding.gemini.time.sleep") as sleep:
            with pytest.raises(EmbeddingError):
                gateway.embed_texts(["one text"])

        assert client.embed_content.call_count == 1
        sleep.assert_not_called()

    def test_backoff_delays_increase(self):
        gateway, client = _gateway_with_mock_client()
        client.embed_content.side_effect = ResourceExhausted("quota")

        with patch("app.services.embedding.gemini.time.sleep") as sleep:
            with pytest.raises(EmbeddingError):
                gateway.embed_texts(["one text"])

        delays = [call.args[0] for call in sleep.call_args_list]
        assert delays == sorted(delays)
        assert len(set(delays)) > 1


class TestResponseShapeValidation:
    def test_mismatched_embedding_count_is_an_error(self):
        gateway, client = _gateway_with_mock_client()
        client.embed_content.return_value = {"embedding": [[0.1]]}  # 1 for 2 inputs

        with pytest.raises(EmbeddingError, match="returned"):
            gateway.embed_texts(["a", "b"])

    def test_missing_embedding_field_is_an_error(self):
        gateway, client = _gateway_with_mock_client()
        client.embed_content.return_value = {}

        with pytest.raises(EmbeddingError, match="no embedding field"):
            gateway.embed_texts(["a"])

    def test_empty_input_never_calls_the_provider(self):
        gateway, client = _gateway_with_mock_client()
        assert gateway.embed_texts([]) == []
        client.embed_content.assert_not_called()
