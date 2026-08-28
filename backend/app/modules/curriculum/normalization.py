"""
Concept normalization: merge duplicates and near-duplicates across
documents, so "virtual memory" in one source and "memory virtualization" in
another become one well-explained concept instead of two half-explained
ones.

Three-band decision on embedding cosine similarity between two candidate
concepts' definitions, per the mandate:
  - >= HIGH_CONFIDENCE_THRESHOLD: auto-merge, no LLM call.
  - [LOW_CONFIDENCE_THRESHOLD, HIGH_CONFIDENCE_THRESHOLD): LLM adjudication --
    "same concept, yes/no, plus a merged definition."
  - < LOW_CONFIDENCE_THRESHOLD: kept distinct.

Both thresholds are named, configurable, unvalidated defaults (AGENTS.md
§1) -- chosen for a plausible separation, not tuned against any dataset.
"""
import enum
import json
import re
from dataclasses import dataclass, field
from typing import List, Optional
from uuid import UUID

from app.services.generation.gateway import GenerationError, GenerationGateway

HIGH_CONFIDENCE_THRESHOLD = 0.92
LOW_CONFIDENCE_THRESHOLD = 0.75

_WHITESPACE = re.compile(r"\s+")


def canonical_key(name: str) -> str:
    """
    Deterministic, case/whitespace-insensitive slug.

    This is also what regeneration uses to carry mastery forward across
    course versions (module docstring), so it must be stable: the same
    concept name always produces the same key, independent of surrounding
    whitespace or capitalization.
    """
    return _WHITESPACE.sub(" ", name.strip().lower())


def cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class MergeDecision(str, enum.Enum):
    AUTO_MERGE = "AUTO_MERGE"
    ADJUDICATE = "ADJUDICATE"
    KEEP_DISTINCT = "KEEP_DISTINCT"


def classify_similarity(similarity: float) -> MergeDecision:
    if similarity >= HIGH_CONFIDENCE_THRESHOLD:
        return MergeDecision.AUTO_MERGE
    if similarity >= LOW_CONFIDENCE_THRESHOLD:
        return MergeDecision.ADJUDICATE
    return MergeDecision.KEEP_DISTINCT


@dataclass
class CandidateConcept:
    """One concept as proposed by extraction, before normalization."""

    name: str
    definition: str
    source_chunk_ids: List[UUID]
    embedding: List[float]
    importance: float = 0.5
    bloom_level: Optional[str] = None


@dataclass
class NormalizedConcept:
    canonical_key: str
    name: str
    definition: str
    aliases: List[str] = field(default_factory=list)
    source_chunk_ids: List[UUID] = field(default_factory=list)
    importance: float = 0.5
    bloom_level: Optional[str] = None
    embedding: List[float] = field(default_factory=list)


class AdjudicationError(Exception):
    """The adjudication call returned something that could not be parsed."""


def _adjudicate(
    a: CandidateConcept, b: NormalizedConcept, generation: GenerationGateway
) -> Optional[str]:
    """
    Asks whether two mid-confidence-similarity concepts are really the same.

    Returns a merged definition (str) if the LLM says yes, or None if it says
    no. Raises AdjudicationError on an unparseable response -- callers treat
    that as "keep distinct" (below), since a corrupted adjudication is not
    evidence they should be merged.
    """
    prompt = (
        "Two candidate concepts from a course's source material may be the "
        "same underlying concept described differently.\n\n"
        f'Concept A: "{a.name}" -- {a.definition}\n'
        f'Concept B: "{b.name}" -- {b.definition}\n\n'
        "Are these the same concept? Respond with ONLY this JSON shape:\n"
        '{"same_concept": true or false, "merged_definition": "..." or null}\n'
        "merged_definition should combine both descriptions clearly if "
        "same_concept is true, and be null otherwise."
    )
    try:
        raw = generation.generate(prompt, temperature=0.0, max_output_tokens=500)
        payload = json.loads(_strip_code_fence(raw))
    except (GenerationError, json.JSONDecodeError, ValueError) as exc:
        raise AdjudicationError(str(exc)) from exc

    if not isinstance(payload.get("same_concept"), bool):
        raise AdjudicationError("Response missing a boolean 'same_concept' field.")

    if payload["same_concept"]:
        merged = payload.get("merged_definition")
        return merged if isinstance(merged, str) and merged.strip() else b.definition
    return None


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)
    return stripped.strip()


def normalize_concepts(
    candidates: List[CandidateConcept], generation: GenerationGateway
) -> List[NormalizedConcept]:
    """
    Greedy streaming merge: each candidate is compared against the
    canonical concepts accepted so far (not every pair), which keeps this
    O(n * m) rather than O(n^2) blowing up on a large course and matches how
    concepts naturally arrive (per-section, in document order).
    """
    canonical: List[NormalizedConcept] = []

    for candidate in candidates:
        best_match: Optional[NormalizedConcept] = None
        best_similarity = -1.0
        for existing in canonical:
            similarity = cosine_similarity(candidate.embedding, existing.embedding)
            if similarity > best_similarity:
                best_similarity = similarity
                best_match = existing

        decision = (
            classify_similarity(best_similarity)
            if best_match is not None
            else MergeDecision.KEEP_DISTINCT
        )

        if decision == MergeDecision.AUTO_MERGE:
            _merge_into(best_match, candidate)
            continue

        if decision == MergeDecision.ADJUDICATE:
            try:
                merged_definition = _adjudicate(candidate, best_match, generation)
            except AdjudicationError:
                merged_definition = None  # corrupted response -> keep distinct

            if merged_definition is not None:
                _merge_into(best_match, candidate, definition_override=merged_definition)
                continue
            # else fall through to KEEP_DISTINCT below

        canonical.append(
            NormalizedConcept(
                canonical_key=canonical_key(candidate.name),
                name=candidate.name,
                definition=candidate.definition,
                aliases=[],
                source_chunk_ids=list(candidate.source_chunk_ids),
                importance=candidate.importance,
                bloom_level=candidate.bloom_level,
                embedding=candidate.embedding,
            )
        )

    return canonical


def _merge_into(
    target: NormalizedConcept, candidate: CandidateConcept, definition_override: Optional[str] = None
) -> None:
    if candidate.name.lower() != target.name.lower() and candidate.name not in target.aliases:
        target.aliases.append(candidate.name)
    for chunk_id in candidate.source_chunk_ids:
        if chunk_id not in target.source_chunk_ids:
            target.source_chunk_ids.append(chunk_id)
    if definition_override is not None:
        target.definition = definition_override
    target.importance = max(target.importance, candidate.importance)
