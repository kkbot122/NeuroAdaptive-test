"""
Coarse, safe-to-surface classification of a provider (Gemini) failure.

Never inspects raw provider response text -- only exception type names.
GeminiEmbeddingGateway/GeminiGenerationGateway already sanitize their own
EmbeddingError/GenerationError messages down to "Gemini ... call failed:
{type(exc).__name__}" (see those modules), and preserve the real
google.api_core exception as __cause__ via `raise ... from exc`. This walks
that chain looking for a recognized type name so a paused job's
error_detail can tell a learner *why* in one honest, authored sentence,
without ever logging or displaying anything the provider itself said.
"""
from enum import Enum


class ProviderErrorCategory(str, Enum):
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    AUTHENTICATION = "AUTHENTICATION"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


# google.api_core.exceptions type names, matched by name (not imported) --
# the same pattern gemini.py's own _is_rate_limit_error already uses, so
# this gateway layer stays free of a direct dependency on that module's
# exception hierarchy.
_QUOTA_TYPES = {"ResourceExhausted"}
_AUTH_TYPES = {"PermissionDenied", "Unauthenticated", "Unauthorized"}
_UNAVAILABLE_TYPES = {
    "ServiceUnavailable", "DeadlineExceeded", "InternalServerError",
    "RetryError", "ConnectionError", "Timeout", "GatewayTimeout",
}

# Authored, human-facing text -- never provider output -- suitable for
# ProcessingJob.error_detail alongside NoExtractableText's own strings
# (jobs/service.py), which already establish that this column may carry a
# message as long as it is one of ours.
PROVIDER_ERROR_MESSAGES = {
    ProviderErrorCategory.QUOTA_EXCEEDED: (
        "The AI provider's usage quota has been used up for now. This "
        "usually resets within a day -- try again later, or switch to a "
        "different API key/plan."
    ),
    ProviderErrorCategory.AUTHENTICATION: (
        "The AI provider rejected the configured credentials. This is a "
        "configuration issue, not something retrying will fix."
    ),
    ProviderErrorCategory.UNAVAILABLE: (
        "The AI provider is temporarily unavailable or slow to respond. "
        "Retrying in a few minutes usually resolves this."
    ),
    ProviderErrorCategory.UNKNOWN: (
        "The AI provider could not complete this step right now. Retrying "
        "may resolve it; if it keeps happening, this needs investigation."
    ),
}


def classify_provider_error(exc: BaseException) -> ProviderErrorCategory:
    """Walks exc's __cause__ chain for a recognized provider exception type
    name. A name outside the known buckets classifies as UNKNOWN rather
    than guessed."""
    seen = set()
    current: "BaseException | None" = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        name = type(current).__name__
        if name in _QUOTA_TYPES:
            return ProviderErrorCategory.QUOTA_EXCEEDED
        if name in _AUTH_TYPES:
            return ProviderErrorCategory.AUTHENTICATION
        if name in _UNAVAILABLE_TYPES:
            return ProviderErrorCategory.UNAVAILABLE
        current = current.__cause__
    return ProviderErrorCategory.UNKNOWN
