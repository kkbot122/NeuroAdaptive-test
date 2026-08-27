"""
Regression tests for K-1: fail-open secret defaults.

INTERNAL_API_KEY and SECRET_KEY used to ship working defaults
("dev_secret_key_123", "CHANGE_ME_TO_A_RANDOM_SECRET_KEY"), so a deployment
that set neither still authenticated — against a value in the git history.

These tests assert the app now refuses to start instead.
"""
import pytest
from pydantic import ValidationError

from app.core.config import Settings

STRONG = "a-sufficiently-long-generated-secret-value"
OTHER_STRONG = "another-sufficiently-long-secret-value-xyz"


def _settings(**overrides):
    """Build Settings without inheriting values from a developer's .env."""
    base = {
        "INTERNAL_API_KEY": STRONG,
        "SECRET_KEY": OTHER_STRONG,
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)


class TestStrongSecretsAccepted:
    def test_valid_secrets_construct(self):
        settings = _settings()
        assert settings.INTERNAL_API_KEY == STRONG
        assert settings.SECRET_KEY == OTHER_STRONG

    def test_exactly_32_chars_is_accepted(self):
        assert len(_settings(INTERNAL_API_KEY="x" * 32).INTERNAL_API_KEY) == 32


class TestKnownPlaceholdersRejected:
    @pytest.mark.parametrize(
        "value",
        ["dev_secret_key_123", "CHANGE_ME_TO_A_RANDOM_SECRET_KEY", "changeme", "secret"],
    )
    def test_internal_api_key_rejects_placeholder(self, value):
        with pytest.raises(ValidationError) as exc:
            _settings(INTERNAL_API_KEY=value)
        assert "publicly-known placeholder" in str(exc.value) or "at least 32" in str(exc.value)

    def test_secret_key_rejects_the_shipped_placeholder(self):
        with pytest.raises(ValidationError):
            _settings(SECRET_KEY="CHANGE_ME_TO_A_RANDOM_SECRET_KEY")

    def test_the_exact_historic_default_is_rejected(self):
        """The specific literal that was live in config.py and auth.ts."""
        with pytest.raises(ValidationError):
            _settings(INTERNAL_API_KEY="dev_secret_key_123")


class TestWeakSecretsRejected:
    @pytest.mark.parametrize("value", ["", "short", "x" * 31])
    def test_rejects_values_under_32_chars(self, value):
        with pytest.raises(ValidationError):
            _settings(INTERNAL_API_KEY=value)

    def test_error_names_the_offending_field(self):
        with pytest.raises(ValidationError) as exc:
            _settings(SECRET_KEY="tooshort")
        assert "SECRET_KEY" in str(exc.value)


class TestSecretsAreRequired:
    """
    Both `_env_file=None` and a cleared process environment are needed here:
    pydantic-settings reads env vars regardless of the env_file setting, and
    the test session itself exports these two (see conftest).
    """

    @staticmethod
    def _clear(monkeypatch, *names):
        for name in names:
            monkeypatch.delenv(name, raising=False)

    def test_missing_internal_api_key_fails(self, monkeypatch):
        self._clear(monkeypatch, "INTERNAL_API_KEY")
        with pytest.raises(ValidationError, match="INTERNAL_API_KEY"):
            Settings(SECRET_KEY=OTHER_STRONG, _env_file=None)

    def test_missing_secret_key_fails(self, monkeypatch):
        self._clear(monkeypatch, "SECRET_KEY")
        with pytest.raises(ValidationError, match="SECRET_KEY"):
            Settings(INTERNAL_API_KEY=STRONG, _env_file=None)

    def test_neither_supplied_fails(self, monkeypatch):
        """There is no default to fall back to. This is the whole point."""
        self._clear(monkeypatch, "INTERNAL_API_KEY", "SECRET_KEY")
        with pytest.raises(ValidationError):
            Settings(_env_file=None)
