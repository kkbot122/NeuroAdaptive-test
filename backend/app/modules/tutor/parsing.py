import json
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


def parse_tutor_response(raw: str) -> ParsedAnswer:
    try:
        parsed = json.loads(_strip_code_fence(raw))
        insufficient = bool(parsed.get("insufficient_evidence", False))
        answer = str(parsed.get("answer_markdown", ""))
        raw_claims = parsed.get("claims", [])
        if not isinstance(raw_claims, list):
            raise ValueError("claims must be a list")
    except (json.JSONDecodeError, ValueError, AttributeError) as exc:
        raise TutorParseError(f"Could not parse tutor response: {raw[:200]!r}") from exc

    claims = []
    for entry in raw_claims:
        try:
            claims.append(Claim(text=str(entry["text"]), chunk_id=str(entry["chunk_id"])))
        except (KeyError, TypeError):
            continue  # a malformed individual claim is dropped, not fatal

    return ParsedAnswer(insufficient_evidence=insufficient, answer_markdown=answer, claims=claims)
