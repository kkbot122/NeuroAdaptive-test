"""
Concept carryover across course versions: when a course is regenerated,
which concepts in the new version correspond to which concepts in the old
one, so mastery data collected against the old version still means
something.

Match by canonical_key first (exact, cheap, and correct whenever a concept's
name didn't change), embedding similarity second (for a concept that was
renamed but is substantively the same), otherwise mark it new. Never
silently match something unrelated: below the similarity floor, "new" is the
honest answer, not a guess.
"""
from dataclasses import dataclass
from typing import List, Optional
from uuid import UUID

from app.modules.curriculum.normalization import canonical_key, cosine_similarity

# Unvalidated default: how similar an old and new concept's embeddings must
# be to count as "the same concept, renamed" once canonical_key doesn't match
# directly. Deliberately higher than normalization's ADJUDICATE floor --
# carryover has no adjudication step to fall back on, so it must be more
# conservative about what it calls a match.
CARRYOVER_SIMILARITY_THRESHOLD = 0.90


@dataclass
class CarryoverCandidate:
    id: UUID
    canonical_key: str
    embedding: Optional[List[float]]


def compute_carryover(
    old_concepts: List[CarryoverCandidate], new_concepts: List[CarryoverCandidate]
) -> dict:
    """
    Returns {str(new_concept_id): {"from": str(old_id) | None, "status": "carried" | "new"}}.
    """
    by_key = {c.canonical_key: c for c in old_concepts}
    used_old_ids = set()
    result = {}

    # Pass 1: exact canonical_key match.
    unmatched_new = []
    for new_concept in new_concepts:
        old_match = by_key.get(new_concept.canonical_key)
        if old_match is not None and old_match.id not in used_old_ids:
            result[str(new_concept.id)] = {"from": str(old_match.id), "status": "carried"}
            used_old_ids.add(old_match.id)
        else:
            unmatched_new.append(new_concept)

    # Pass 2: embedding similarity, for concepts whose key changed.
    available_old = [c for c in old_concepts if c.id not in used_old_ids]
    for new_concept in unmatched_new:
        best_match = None
        best_similarity = -1.0
        if new_concept.embedding:
            for old_concept in available_old:
                if old_concept.id in used_old_ids or not old_concept.embedding:
                    continue
                similarity = cosine_similarity(new_concept.embedding, old_concept.embedding)
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = old_concept

        if best_match is not None and best_similarity >= CARRYOVER_SIMILARITY_THRESHOLD:
            result[str(new_concept.id)] = {"from": str(best_match.id), "status": "carried"}
            used_old_ids.add(best_match.id)
        else:
            result[str(new_concept.id)] = {"from": None, "status": "new"}

    return result
