"""
Text-generation provider abstraction.

Every LLM call this phase makes -- concept extraction, normalization
adjudication, prerequisite-edge proposal, lesson planning, assessment
blueprinting, default content generation -- goes through this interface, not
a vendor SDK directly (AGENTS.md: "keep model/provider calls behind a single
abstraction").

The interface returns raw text; callers are responsible for parsing and
validating structured output against their own schema. Per the mandate's
explicit instruction, the LLM proposes structure and deterministic code
validates it -- the gateway does not attempt to enforce a schema itself,
because doing so would let a provider's own confidence stand in for
validation the mandate says must be separate.
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
    ) -> str:
        """
        One completion. Raises GenerationError on provider failure.

        Low default temperature: every call site in this phase wants
        structured, low-variance output (concept lists, edge proposals, JSON
        plans), not creative prose.
        """
