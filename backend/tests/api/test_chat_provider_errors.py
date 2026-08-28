"""
Regression tests for the Groq model outage.

Two distinct faults produced the 404 a learner saw in the UI:
  1. the model id was a hardcoded literal, so a provider retirement
     needed a code change in two files;
  2. the provider's raw error was interpolated into the HTTP detail, so
     that code change was announced to the learner as JSON.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings
from tests.conftest import auth_headers

ENDPOINT = "/api/v1/chat/message"


class ProviderError(Exception):
    """Stands in for an openai.APIStatusError, which carries status_code."""

    def __init__(self, status_code: int):
        super().__init__(f"Error code: {status_code}")
        self.status_code = status_code


def send(client, email, prompt="hello"):
    return client.post(ENDPOINT, data={"prompt": prompt}, headers=auth_headers(email))


class TestModelIsConfigurable:
    def test_model_comes_from_settings(self):
        """A retirement should be a config change, not a code edit."""
        assert settings.GROQ_MODEL
        assert settings.GROQ_MODEL != "llama-3.3-70b-versatile"

    def test_no_hardcoded_model_literal_remains(self):
        """The literal lived in two call sites; neither may reintroduce it."""
        from pathlib import Path

        import app

        root = Path(app.__file__).resolve().parent
        for module in ("modules/chat/router.py", "services/adaptation.py"):
            source = (root / module).read_text(encoding="utf-8")
            # As a *string literal*, i.e. actually used. Mentioning the retired
            # id in a comment is fine and worth keeping as history.
            assert '"llama-3.3-70b-versatile"' not in source, module
            assert "'llama-3.3-70b-versatile'" not in source, module
            assert "settings.GROQ_MODEL" in source, module

    def test_the_configured_model_is_sent_to_the_provider(self, client, owner):
        captured = {}

        async def _create(**kwargs):
            captured.update(kwargs)
            raise ProviderError(500)  # short-circuit; we only want the request

        with patch("app.modules.chat.router.get_client") as get_client:
            get_client.return_value.chat.completions.create = _create
            send(client, owner.email)

        assert captured["model"] == settings.GROQ_MODEL


class TestProviderErrorsAreNotLeaked:
    @pytest.mark.parametrize(
        "status,expected_phrase",
        [
            (401, "credentials"),
            (403, "credentials"),
            (404, "model is unavailable"),
            (429, "rate limiting"),
            (500, "did not respond"),
        ],
    )
    def test_learner_sees_a_category_not_provider_text(
        self, client, owner, status, expected_phrase
    ):
        with patch("app.modules.chat.router.get_client") as get_client:
            get_client.return_value.chat.completions.create = AsyncMock(
                side_effect=ProviderError(status)
            )
            response = send(client, owner.email)

        assert response.status_code == 502
        detail = response.json()["detail"]
        assert expected_phrase in detail

    def test_the_exact_leaked_string_can_no_longer_appear(self, client, owner):
        """
        The precise shape the learner saw: provider JSON naming our model.
        """
        with patch("app.modules.chat.router.get_client") as get_client:
            get_client.return_value.chat.completions.create = AsyncMock(
                side_effect=ProviderError(404)
            )
            detail = send(client, owner.email).json()["detail"]

        assert "LLM call failed" not in detail
        assert "invalid_request_error" not in detail
        assert "model_not_found" not in detail
        assert settings.GROQ_MODEL not in detail  # our config is not the learner's business

    def test_configuration_failures_say_it_is_not_the_learners_fault(self, client, owner):
        with patch("app.modules.chat.router.get_client") as get_client:
            get_client.return_value.chat.completions.create = AsyncMock(
                side_effect=ProviderError(404)
            )
            detail = send(client, owner.email).json()["detail"]
        assert "not something you did" in detail

    def test_upstream_failure_is_502_not_500(self, client, owner):
        """The API is healthy; its dependency is not."""
        with patch("app.modules.chat.router.get_client") as get_client:
            get_client.return_value.chat.completions.create = AsyncMock(
                side_effect=ProviderError(404)
            )
            assert send(client, owner.email).status_code == 502

    def test_a_413_from_the_provider_is_reported_as_413_not_502(self, client, owner):
        """A large attachment produced Groq's own 413, which the generic
        else-branch used to relabel as an opaque 502 'Bad Gateway'."""
        with patch("app.modules.chat.router.get_client") as get_client:
            get_client.return_value.chat.completions.create = AsyncMock(
                side_effect=ProviderError(413)
            )
            response = send(client, owner.email)
        assert response.status_code == 413
        assert "too large" in response.json()["detail"]


class TestOversizedMessageIsRejectedBeforeCallingTheProvider:
    """A large PDF's extracted text could push a request past Groq's own
    limit; catching that up front avoids the round trip Groq was always
    going to reject, and gives the learner an actionable message instead of
    a generic 'Bad Gateway'."""

    def test_an_oversized_message_never_reaches_the_provider(self, client, owner):
        from app.modules.chat.router import MAX_MESSAGE_CHARS

        with patch("app.modules.chat.router.get_client") as get_client:
            create = AsyncMock(side_effect=AssertionError("must not call the provider"))
            get_client.return_value.chat.completions.create = create
            response = send(client, owner.email, prompt="x" * (MAX_MESSAGE_CHARS + 1))

        create.assert_not_called()
        assert response.status_code == 413
        assert "too long" in response.json()["detail"]

    def test_a_message_within_the_limit_is_unaffected(self, client, owner):
        with patch("app.modules.chat.router.get_client") as get_client:
            get_client.return_value.chat.completions.create = AsyncMock(
                side_effect=ProviderError(500)  # short-circuit; only checking it was called
            )
            response = send(client, owner.email, prompt="a normal short question")
        assert response.status_code == 502  # reached the provider call, unlike the oversized case
