"""
A deterministic, offline GenerationGateway for tests.

Unlike the embedding fake (which can synthesize a plausible vector from a
hash), a text-generation fake cannot synthesize a plausible *structured JSON
response* for an arbitrary prompt -- the caller must supply what each call
should return. Responses are matched by a substring of the prompt, checked in
registration order, so a test sets up "when the prompt mentions X, return Y"
without needing to match the whole prompt text exactly.
"""
from typing import List, Optional, Tuple

from app.services.generation.gateway import GenerationError, GenerationGateway


class FakeGenerationGateway(GenerationGateway):
    def __init__(self):
        self._responses: List[Tuple[str, str]] = []
        self._default: Optional[str] = None
        self.calls: List[str] = []  # prompts received, for assertions

    @property
    def model_name(self) -> str:
        return "fake-generation-gateway"

    def when_prompt_contains(self, substring: str, response: str) -> "FakeGenerationGateway":
        self._responses.append((substring, response))
        return self

    def set_default(self, response: str) -> "FakeGenerationGateway":
        self._default = response
        return self

    def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.2,
        max_output_tokens: int = 4096,
    ) -> str:
        self.calls.append(prompt)
        for substring, response in self._responses:
            if substring in prompt:
                return response
        if self._default is not None:
            return self._default
        raise GenerationError(
            f"FakeGenerationGateway has no registered response matching this prompt "
            f"(first 200 chars): {prompt[:200]!r}"
        )
