"""Gemini implementation of GenerationGateway."""
import time
from typing import Optional

from app.core.config import settings
from app.services.generation.gateway import GenerationError, GenerationGateway

# Same reasoning as the embedding gateway: this is ordinary resilience
# against an expected, transient free-tier limit, not the "automatic
# provider fallback" frozen-scope.md forbids -- the same provider, retried
# after backing off.
_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = (10, 20, 40)


def _is_rate_limit_error(exc: Exception) -> bool:
    return type(exc).__name__ == "ResourceExhausted"


class GeminiGenerationGateway(GenerationGateway):
    def __init__(self, api_key: str = None, model: str = None):
        self._api_key = api_key or settings.GEMINI_API_KEY
        self._model_name = model or settings.GEMINI_GENERATION_MODEL
        self._genai = None  # lazy: importing/constructing must not need a key

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
    ) -> str:
        genai = self._ensure_configured()
        model = genai.GenerativeModel(
            self._model_name, system_instruction=system_instruction
        )
        config = genai.types.GenerationConfig(
            temperature=temperature, max_output_tokens=max_output_tokens
        )

        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = model.generate_content(prompt, generation_config=config)
                break
            except Exception as exc:
                is_last_attempt = attempt == _MAX_RETRIES
                if _is_rate_limit_error(exc) and not is_last_attempt:
                    time.sleep(_RETRY_BACKOFF_SECONDS[attempt])
                    continue
                raise GenerationError(
                    f"Gemini generation call failed: {type(exc).__name__}"
                ) from exc

        text = getattr(response, "text", None)
        if not text:
            raise GenerationError("Gemini response carried no text.")
        return text
