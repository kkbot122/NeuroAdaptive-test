from app.core.provider_errors import PROVIDER_ERROR_MESSAGES, ProviderErrorCategory, classify_provider_error


class _Wrapped(Exception):
    """Mimics EmbeddingError/GenerationError: the sanitized message never
    contains provider text, but __cause__ preserves the real exception."""


def _wrap(cause: Exception) -> Exception:
    try:
        raise _Wrapped(f"Gemini call failed: {type(cause).__name__}") from cause
    except _Wrapped as wrapped:
        return wrapped


class ResourceExhausted(Exception):
    pass


class PermissionDenied(Exception):
    pass


class DeadlineExceeded(Exception):
    pass


class SomethingElseEntirely(Exception):
    pass


class TestClassifyProviderError:
    def test_resource_exhausted_is_quota_exceeded(self):
        assert classify_provider_error(_wrap(ResourceExhausted())) == ProviderErrorCategory.QUOTA_EXCEEDED

    def test_permission_denied_is_authentication(self):
        assert classify_provider_error(_wrap(PermissionDenied())) == ProviderErrorCategory.AUTHENTICATION

    def test_deadline_exceeded_is_unavailable(self):
        assert classify_provider_error(_wrap(DeadlineExceeded())) == ProviderErrorCategory.UNAVAILABLE

    def test_unrecognized_type_is_unknown_not_guessed(self):
        assert classify_provider_error(_wrap(SomethingElseEntirely())) == ProviderErrorCategory.UNKNOWN

    def test_an_exception_with_no_cause_at_all_is_unknown(self):
        assert classify_provider_error(Exception("plain")) == ProviderErrorCategory.UNKNOWN

    def test_every_category_has_an_authored_message(self):
        for category in ProviderErrorCategory:
            assert PROVIDER_ERROR_MESSAGES[category]

    def test_messages_never_echo_an_exception_type_name(self):
        """The whole point is a human-facing sentence, not a leaked class name."""
        leaked_names = ["ResourceExhausted", "PermissionDenied", "DeadlineExceeded", "Exception"]
        for message in PROVIDER_ERROR_MESSAGES.values():
            for name in leaked_names:
                assert name not in message
