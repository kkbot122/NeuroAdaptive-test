from pydantic import BaseModel, Field, StrictBool, StrictStr, ValidationError
from dataclasses import dataclass
from typing import List

from app.modules.tutor.validation import Claim


class TutorParseError(Exception):
    """The generation response could not be parsed at all."""


@dataclass(frozen=True)
class ParsedAnswer:
    insufficient_evidence: bool
    answer_markdown: str
    claims: List[Claim]


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


class ClaimPayload(BaseModel):
    text: StrictStr = Field(min_length=1)
    chunk_id: StrictStr = Field(min_length=1)


class AnswerPayload(BaseModel):
    insufficient_evidence: StrictBool = False
    answer_markdown: StrictStr = ""
    claims: List[ClaimPayload] = Field(default_factory=list)


def parse_tutor_response(raw: str) -> ParsedAnswer:
    try:
        payload = AnswerPayload.model_validate_json(_strip_code_fence(raw))
        if not payload.insufficient_evidence and payload.answer_markdown.strip() and not payload.claims:
            raise ValueError("An answer requires claims")
        if any(not claim.text.strip() or not claim.chunk_id.strip() for claim in payload.claims):
            raise ValueError("Claim text and citation must not be blank")
    except (ValidationError, ValueError) as exc:
        raise TutorParseError("Tutor response failed schema validation") from exc
    return ParsedAnswer(payload.insufficient_evidence, payload.answer_markdown,
        [Claim(text=c.text, chunk_id=c.chunk_id) for c in payload.claims])
