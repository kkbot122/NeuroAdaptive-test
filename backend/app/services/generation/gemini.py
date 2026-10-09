"""Gemini implementation of GenerationGateway."""
import time
import logging
from typing import Optional

from app.core.config import settings
from app.services.generation.gateway import GenerationError, GenerationGateway
from app.services.ai_usage import current_usage_scope, provider_attempt

# Same reasoning as the embedding gateway: this is ordinary resilience
# against an expected, transient free-tier limit, not the "automatic
# provider fallback" frozen-scope.md forbids -- the same provider, retried
# after backing off.
logger = logging.getLogger(__name__)


def _is_rate_limit_error(exc: Exception) -> bool:
    return type(exc).__name__ == "ResourceExhausted"


def _sdk_response_schema(schema: dict) -> dict:
    """Translate array bounds for the installed SDK without changing property names."""
    converted = dict(schema)
    for public, sdk in (("minItems", "min_items"), ("maxItems", "max_items")):
        if public in converted:
            converted[sdk] = converted.pop(public)
    if "items" in converted:
        converted["items"] = _sdk_response_schema(converted["items"])
    if "properties" in converted:
        converted["properties"] = {
            name: _sdk_response_schema(value) for name, value in converted["properties"].items()
        }
    return converted


class GeminiGenerationGateway(GenerationGateway):
    reports_provider_attempts = True

    def __init__(self, api_key: str = None, model: str = None, *, require_accounting: bool = False):
        self._api_key = api_key or settings.GEMINI_API_KEY
        self._model_name = model or settings.GEMINI_GENERATION_MODEL
        self._genai = None  # lazy: importing/constructing must not need a key
        self.require_accounting = require_accounting

    @property
    def model_name(self) -> str:
        return self._model_name

    def _ensure_configured(self):
        import google.generativeai as genai

        if self._genai is None:
            genai.configure(api_key=self._api_key)
            self._genai = genai
        return self._genai

    def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.2,
        max_output_tokens: int = 4096,
        json_mode: bool = False,
        response_schema: Optional[dict] = None,
    ) -> str:
        if self.require_accounting and current_usage_scope() is None:
            raise GenerationError("AI operation requires an owned usage scope")
        genai = self._ensure_configured()
        model = genai.GenerativeModel(
            self._model_name, system_instruction=system_instruction
        )
        config = genai.types.GenerationConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json" if json_mode or response_schema is not None else None,
            response_schema=_sdk_response_schema(response_schema) if response_schema is not None else None,
        )

        started = time.monotonic()
        for attempt in range(settings.GEMINI_MAX_RETRIES_V1 + 1):
            retry = False
            with provider_attempt("generation", self._model_name, retry_index=attempt) as usage:
                try:
                    response = model.generate_content(prompt, generation_config=config, request_options={
                        "timeout": usage.timeout or settings.GEMINI_GENERATION_TIMEOUT_SECONDS_V1,
                        "retry": None,
                    })
                    usage.record_usage(getattr(response, "usage_metadata", None))
                    text = getattr(response, "text", None)
                    if not text:
                        usage.error_category = "RESPONSE_INVALID"
                        raise GenerationError("Gemini response carried no text")
                except GenerationError:
                    raise
                except Exception as exc:
                    usage.error_category = "RATE_LIMITED" if _is_rate_limit_error(exc) else "PROVIDER_ERROR"
                    if _is_rate_limit_error(exc) and attempt < settings.GEMINI_MAX_RETRIES_V1:
                        retry = True
                    else:
                        raise GenerationError(f"Gemini generation call failed: {type(exc).__name__}") from exc
                if retry:
                    # Record the failed transport attempt before releasing its
                    # shared slot and sleeping outside the request capacity.
                    usage.error_category = "RATE_LIMITED"
            if not retry:
                break
            backoff = settings.GEMINI_RETRY_BACKOFF_SECONDS_V1 * (2 ** attempt)
            logger.info("Gemini generation rate-limited; model=%s retry=%d backoff_seconds=%.1f",
                        self._model_name, attempt + 1, backoff,
                        extra={"model_id": self._model_name, "backoff_seconds": backoff})
            time.sleep(backoff)
        elapsed = time.monotonic() - started
        logger.info(
            "Gemini generation completed; model=%s attempts=%d elapsed_seconds=%.3f",
            self._model_name, attempt + 1, elapsed,
            extra={"model_id": self._model_name, "provider_attempts": attempt + 1, "elapsed_seconds": elapsed},
        )
        return text
