"""
Phase 8 metric computation. Every function here is a PURE function over
data the caller already fetched (from Phase 7's real logging, or a
synthetic fixture the caller explicitly labels as such) -- nothing in this
module queries a database or calls a network service, and nothing
generates a number that didn't come from its inputs.

Mastery/uncertainty computation is NOT reimplemented here: it imports
Phase 3's real engine.py directly (mastery model wiring, not a parallel
formula that could silently drift from what production actually runs).
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from app.modules.mastery import engine as mastery_engine


# -- mastery-derived metrics (reuse Phase 3's real engine) ---------------------

def compute_mastery_state(events: List[mastery_engine.EvidenceEvent], now: datetime) -> mastery_engine.MasteryState:
    """Thin re-export -- kept here so evaluation code has one import path,
    but this IS mastery_engine.compute_mastery, not a copy of it."""
    return mastery_engine.compute_mastery(events, now)


def time_to_mastery(events: List[mastery_engine.EvidenceEvent], now: datetime) -> Optional[float]:
    """
    Seconds between the first evidence event (diagnostic initialization,
    by convention -- the earliest event in the sorted history) and the
    first point at which cumulative mastery crosses into the "Mastered"
    band. None if mastery was never reached within the given history.
    """
    if not events:
        return None
    ordered = sorted(events, key=lambda e: e.created_at)
    start = ordered[0].created_at
    for i in range(1, len(ordered) + 1):
        state = mastery_engine.compute_mastery(ordered[:i], now)
        if mastery_engine.classify_band(state) == mastery_engine.MASTERED:
            return (ordered[i - 1].created_at - start).total_seconds()
    return None


def attempts_to_mastery(events: List[mastery_engine.EvidenceEvent], now: datetime) -> Optional[int]:
    """Count of evidence events up to and including the one that first
    reached "Mastered". None if never reached."""
    if not events:
        return None
    ordered = sorted(events, key=lambda e: e.created_at)
    for i in range(1, len(ordered) + 1):
        state = mastery_engine.compute_mastery(ordered[:i], now)
        if mastery_engine.classify_band(state) == mastery_engine.MASTERED:
            return i
    return None


# -- citation precision/recall (ALCE-style) ------------------------------------

@dataclass(frozen=True)
class CitationRecord:
    tier1_valid: bool  # structurally real, owned, correctly-scoped chunk
    tier2_supported: Optional[bool]  # semantic entailment result; None if never sampled/checked


@dataclass(frozen=True)
class CitationPrecisionRecall:
    total_claims: int
    structurally_valid: int
    structurally_invalid: int
    supported: int  # structurally valid AND semantically supported
    unsupported: int  # structurally valid but NOT semantically supported
    precision: Optional[float]  # supported / structurally_valid (None if no valid citations to check)
    recall: Optional[float]  # supported / total_claims (None if no claims at all)


def citation_precision_recall(records: List[CitationRecord]) -> CitationPrecisionRecall:
    """
    Precision asks "of the citations that were real, how many actually
    supported their claim" -- distinct from recall, "of every claim made,
    how many ended up with a real, supporting citation." Structurally
    invalid citations (fabricated/wrong-document chunk ids) are counted
    separately from structurally-valid-but-unsupported ones -- these are
    different failure modes (a hallucinated reference vs. a real reference
    that doesn't actually say what the claim says), and collapsing them
    would hide which one is actually happening.
    """
    total = len(records)
    valid = [r for r in records if r.tier1_valid]
    invalid_count = total - len(valid)
    supported = [r for r in valid if r.tier2_supported is True]
    unsupported = [r for r in valid if r.tier2_supported is False]

    precision = (len(supported) / len(valid)) if valid else None
    recall = (len(supported) / total) if total else None

    return CitationPrecisionRecall(
        total_claims=total,
        structurally_valid=len(valid),
        structurally_invalid=invalid_count,
        supported=len(supported),
        unsupported=len(unsupported),
        precision=precision,
        recall=recall,
    )


def unsupported_claim_rate(records: List[CitationRecord]) -> Optional[float]:
    """Fraction of ALL claims (not just structurally-valid ones) that ended
    up unsupported or fabricated -- the inverse-facing view of recall."""
    if not records:
        return None
    result = citation_precision_recall(records)
    return (result.structurally_invalid + result.unsupported) / result.total_claims


# -- latency percentiles -------------------------------------------------------

def _percentile(sorted_values: List[float], p: float) -> float:
    """Linear interpolation between closest ranks -- the same method
    numpy.percentile uses by default (interpolation='linear'), implemented
    without a hard runtime dependency on numpy in production code.
    tests/evaluation cross-checks this against numpy directly."""
    if not sorted_values:
        raise ValueError("Cannot compute a percentile of an empty sequence.")
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (p / 100.0) * (len(sorted_values) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = rank - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * fraction


@dataclass(frozen=True)
class LatencyPercentiles:
    p50: float
    p95: float
    p99: float
    n: int


def latency_percentiles(durations_seconds: List[float]) -> LatencyPercentiles:
    if not durations_seconds:
        raise ValueError("Cannot compute latency percentiles from an empty list.")
    ordered = sorted(durations_seconds)
    return LatencyPercentiles(
        p50=_percentile(ordered, 50), p95=_percentile(ordered, 95), p99=_percentile(ordered, 99),
        n=len(ordered),
    )


# -- recommendation / feedback / mastery-delta summaries -----------------------
# GUARDRAIL: these summaries never mix signal_category=ENGAGEMENT rows into
# a pedagogical-effect average, and never produce one blended "success"
# number -- each function below operates on exactly one signal category.

@dataclass(frozen=True)
class RecommendationAcceptance:
    total_decisions: int
    completed: int
    rejected: int
    acceptance_rate: Optional[float]  # completed / (completed + rejected); None if neither happened


def recommendation_acceptance_rate(outcome_types: List[str]) -> RecommendationAcceptance:
    """`outcome_types` is a flat list of engagement outcome_type strings --
    one per decision that received a COMPLETED or REJECTED engagement
    outcome (a decision with no engagement outcome yet, or only VIEWED, is
    excluded, not counted as either)."""
    completed = sum(1 for t in outcome_types if t == "COMPLETED")
    rejected = sum(1 for t in outcome_types if t == "REJECTED")
    denom = completed + rejected
    return RecommendationAcceptance(
        total_decisions=len(outcome_types), completed=completed, rejected=rejected,
        acceptance_rate=(completed / denom) if denom else None,
    )


@dataclass(frozen=True)
class HelpfulnessSummary:
    n: int
    mean_rating: Optional[float]  # over ratings in {-1, 0, 1}


def helpfulness_summary(ratings: List[int]) -> HelpfulnessSummary:
    if not ratings:
        return HelpfulnessSummary(n=0, mean_rating=None)
    return HelpfulnessSummary(n=len(ratings), mean_rating=sum(ratings) / len(ratings))


@dataclass(frozen=True)
class MasteryDeltaSummary:
    n: int
    mean_delta: Optional[float]


def mastery_delta_summary(deltas: List[float]) -> MasteryDeltaSummary:
    """Input must already be filtered to signal_category=PEDAGOGICAL_EFFECT
    (ASSESSED/TRANSFER_SUCCESS) outcomes with a non-null mastery_delta --
    this function does not know or check that itself, callers must not
    pass engagement-outcome data in here."""
    if not deltas:
        return MasteryDeltaSummary(n=0, mean_delta=None)
    return MasteryDeltaSummary(n=len(deltas), mean_delta=sum(deltas) / len(deltas))


# -- prompt-injection attack success rate (Phase 6 reuse, not recomputation) --

def prompt_injection_attack_success_rate(measured_results: Optional[List[dict]]) -> Optional[float]:
    """
    Reads Phase 6's own measured results (scripts/measure_injection_resistance.py's
    output, loaded by the caller -- this function does not run the live
    measurement itself) rather than recomputing or hardcoding a number.
    Returns None, honestly, if no measurement has been run yet -- never a
    fabricated or assumed 0%.
    """
    if not measured_results:
        return None
    successes = sum(1 for r in measured_results if r.get("attack_succeeded"))
    return successes / len(measured_results)
