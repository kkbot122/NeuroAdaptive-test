"""
Two-tier citation validation.

Tier 1 (structural) runs on every claim, always, and is a real DB lookup
scoped by course_id AND owner_id inside the query -- a chunk that exists but
belongs to a different course (even one owned by the same caller) fails
exactly like a wholly invented chunk_id. This is the same "ownership filter
lives inside the query" property the retrieval module already enforces
(retrieval/service.py), reapplied here for citation checking.

Tier 2 (semantic) checks every claim by default. Production callers can batch
checks against identical source text without sampling. Explicit sampling is
retained for evaluation callers, never the ordinary tutor path.
"""
from dataclasses import dataclass
from typing import Callable, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.documents.chunk_models import Chunk
from app.modules.tutor.entailment import check_support

TIER2_SAMPLE_EVERY = 1


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
    source_chunk_ids: tuple[str, ...] = ()


def _source_ids(claim: Claim) -> tuple[str, ...]:
    """Return every cited chunk while preserving compatibility with one-source claims."""
    return tuple(dict.fromkeys((claim.chunk_id, *claim.source_chunk_ids)))


@dataclass
class ValidatedClaim:
    claim: Claim
    tier1_passed: bool
    tier2_status: str  # ValidationStatus


def tier1_validate(db: Session, claim: Claim, course_id: UUID, owner_id: int) -> bool:
    """Every cited chunk must resolve to a real chunk in THIS owned course.

    Fabricated or wrong-course citations fail identically, including when a
    claim uses several passages as combined evidence.
    """
    source_ids = _source_ids(claim)
    if not source_ids:
        return False
    for source_id in source_ids:
        try:
            chunk_uuid = UUID(source_id)
        except (ValueError, TypeError, AttributeError):
            return False
        exists = (
            db.query(Chunk)
            .filter(Chunk.id == chunk_uuid, Chunk.course_id == course_id, Chunk.owner_id == owner_id)
            .first()
            is not None
        )
        if not exists:
            return False
    return True


EntailmentChecker = Callable[[str, str], bool]  # (claim_text, chunk_text) -> is_supported


def tier2_validate(claim: Claim, chunk_text: str, checker: EntailmentChecker) -> bool:
    """Do the cited passages together support the claim? Uses
    whatever (cheaper) checker the caller supplies -- see service.py for the
    production wiring. Multiple cited passages are checked together because
    their combined evidence may support one complete statement."""
    return checker(claim.text, chunk_text)


def validate_claims(
    db: Session,
    claims: List[Claim],
    course_id: UUID,
    owner_id: int,
    chunk_text_by_id: dict,
    entailment_checker: EntailmentChecker,
    sample_every: int = TIER2_SAMPLE_EVERY,
    batch_checks: bool = False,
) -> List[ValidatedClaim]:
    results = []
    pending = []
    for index, claim in enumerate(claims):
        tier1_ok = tier1_validate(db, claim, course_id, owner_id)
        if not tier1_ok:
            results.append(ValidatedClaim(claim=claim, tier1_passed=False, tier2_status=ValidationStatus.UNSAMPLED))
            continue

        if index % sample_every == 0:
            source_texts = [chunk_text_by_id.get(source_id) for source_id in _source_ids(claim)]
            if any(source_text is None for source_text in source_texts):
                results.append(
                    ValidatedClaim(claim=claim, tier1_passed=True, tier2_status=ValidationStatus.FAILED)
                )
                continue
            chunk_text = "\n\n".join(source_text for source_text in source_texts if source_text is not None)
            if batch_checks:
                pending.append((len(results), claim.text, chunk_text))
                results.append(ValidatedClaim(claim=claim, tier1_passed=True, tier2_status=ValidationStatus.UNSAMPLED))
                continue
            tier2_ok = tier2_validate(claim, chunk_text, entailment_checker)
            status = ValidationStatus.PASSED if tier2_ok else ValidationStatus.FAILED
        else:
            status = ValidationStatus.UNSAMPLED
        results.append(ValidatedClaim(claim=claim, tier1_passed=True, tier2_status=status))
    if pending:
        supported = check_support(entailment_checker, [(claim, source) for _, claim, source in pending])
        for (index, _, _), is_supported in zip(pending, supported):
            results[index].tier2_status = ValidationStatus.PASSED if is_supported else ValidationStatus.FAILED
    return results
