"""
Text-generation provider abstraction.

Every LLM call this phase makes -- concept extraction, normalization
adjudication, prerequisite-edge proposal, lesson planning, assessment
blueprinting, default content generation -- goes through this interface, not
a vendor SDK directly (AGENTS.md: "keep model/provider calls behind a single
abstraction").

The interface returns raw text; callers are responsible for parsing and
validating structured output against their own schema. Callers may request
a provider response shape, which the gateway transports. That shape never
replaces deterministic domain parsing, source validation, or grading policy.
"""
from abc import ABC, abstractmethod
from typing import Optional


class GenerationError(Exception):
    """Raised when the provider cannot produce a completion."""


class GenerationGateway(ABC):
    @property
    @abstractmethod
    def model_name(self) -> str:
        """Recorded on every generated artifact for provenance."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.2,
        max_output_tokens: int = 4096,
        json_mode: bool = False,
        response_schema: Optional[dict] = None,
    ) -> str:
        """
        One completion. Raises GenerationError on provider failure.

        ``json_mode`` asks providers that support it for syntactically valid
        JSON; callers must still validate its domain schema.

        ``response_schema`` constrains a structured response at the provider;
        the domain parser remains authoritative for accepting its values.

        Low default temperature: every call site in this phase wants
        structured, low-variance output (concept lists, edge proposals, JSON
        plans), not creative prose.
        """
