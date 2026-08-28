"""
Two-tier citation validation.

Tier 1 (structural) runs on every claim, always, and is a real DB lookup
scoped by course_id AND owner_id inside the query -- a chunk that exists but
belongs to a different course (even one owned by the same caller) fails
exactly like a wholly invented chunk_id. This is the same "ownership filter
lives inside the query" property the retrieval module already enforces
(retrieval/service.py), reapplied here for citation checking.

Tier 2 (semantic) is sampled, not exhaustive -- full entailment checking on
every claim would blow the tutor's latency budget (mandate step 6). Sampling
is deterministic (every Nth claim by position, always including the first),
not random, matching this codebase's general preference for determinism
where either would do.
"""
from dataclasses import dataclass
from typing import Callable, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.documents.chunk_models import Chunk

TIER2_SAMPLE_EVERY = 2  # named, unvalidated default: validate every 2nd claim, always including the first


class ValidationStatus:
    PASSED = "passed"
    FAILED = "failed"
    UNSAMPLED = "unsampled"  # tier 2 was not run on this claim this round
    # Phase 8, B3 ablation only: citation validation was deliberately
    # disabled for this call (TutorService.ask(citation_validation_enabled=
    # False)) to isolate its contribution. Never returned by the real
    # production pipeline -- never confuse this with PASSED.
    DISABLED = "disabled"


@dataclass(frozen=True)
class Claim:
    text: str
    chunk_id: str


@dataclass
class ValidatedClaim:
    claim: Claim
    tier1_passed: bool
    tier2_status: str  # ValidationStatus


def tier1_validate(db: Session, claim: Claim, course_id: UUID, owner_id: int) -> bool:
    """The cited chunk_id must resolve to a real chunk belonging to a
    document in THIS course, owned by THIS user. Fabricated or
    wrong-document chunk_ids fail identically."""
    try:
        chunk_uuid = UUID(claim.chunk_id)
    except (ValueError, TypeError, AttributeError):
        return False
    exists = (
        db.query(Chunk)
        .filter(Chunk.id == chunk_uuid, Chunk.course_id == course_id, Chunk.owner_id == owner_id)
        .first()
        is not None
    )
    return exists


EntailmentChecker = Callable[[str, str], bool]  # (claim_text, chunk_text) -> is_supported


def tier2_validate(claim: Claim, chunk_text: str, checker: EntailmentChecker) -> bool:
    """Does the cited chunk's content actually support the claim? Uses
    whatever (cheaper) checker the caller supplies -- see service.py for the
    production wiring."""
    return checker(claim.text, chunk_text)


def validate_claims(
    db: Session,
    claims: List[Claim],
    course_id: UUID,
    owner_id: int,
    chunk_text_by_id: dict,
    entailment_checker: EntailmentChecker,
    sample_every: int = TIER2_SAMPLE_EVERY,
) -> List[ValidatedClaim]:
    results = []
    for index, claim in enumerate(claims):
        tier1_ok = tier1_validate(db, claim, course_id, owner_id)
        if not tier1_ok:
            results.append(ValidatedClaim(claim=claim, tier1_passed=False, tier2_status=ValidationStatus.UNSAMPLED))
            continue

        if index % sample_every == 0:
            chunk_text = chunk_text_by_id.get(claim.chunk_id, "")
            tier2_ok = tier2_validate(claim, chunk_text, entailment_checker)
            status = ValidationStatus.PASSED if tier2_ok else ValidationStatus.FAILED
        else:
            status = ValidationStatus.UNSAMPLED
        results.append(ValidatedClaim(claim=claim, tier1_passed=True, tier2_status=status))
    return results
