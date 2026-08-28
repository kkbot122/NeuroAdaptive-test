"""
Pure mastery-update math. No I/O -- no DB session, no network client, no
clock reads other than the `now` argument the caller supplies. This is what
AGENTS.md means by "keep the mastery engine pure and deterministic": the
whole formula is a function of its inputs and can be unit-tested with plain
fixtures, and swapped for a BKT/IRT implementation later without touching any
caller (frozen-scope.md; Phase 3 mandate).

CONFLICT, DECLARED: two governing sources give two different mastery-update
formulas with two different constant sets:
  - docs/adaptation-spec.md Eq.2: m_c(t+1) = (m*w_prior + e_a*w_evidence) /
    (w_prior + w_evidence), w_prior=3.0, w_evidence=1.0.
  - SYSTEM_ARCHITECTURE.md S10 / this phase's own pack ("SDD Deep Dive 4"):
    mastery = (S + m0*k) / (W + k), m0=0.3, k=2, uncertainty = 1/sqrt(1+W).
This phase's own prompt pack is explicit that the SDD's numbers are "what
this phase should implement" and instructs flagging any future attempt to
mix the two constant sets. That is a direct, in-pack resolution of a conflict
between two governing docs, so per AGENTS.md's authority order this
implements the SDD formula (S/W weighted-evidence form) below. The
adaptation-spec.md Eq.2 form is not used anywhere in this module.

Evidence weight itself (w_base * concept_weight * difficulty * hint_penalty *
retry_penalty) is applied at write time and stored per-event
(MasteryEvent.evidence_weight_base); recency decay is applied at READ time
against each event's age, exactly as the pack specifies ("applied AT READ
TIME, not stored") -- so raw evidence history is permanent and auditable,
and current mastery still reflects current (decayed) evidence.
"""
import math
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

MASTERY_POLICY_VERSION = "mastery-v1"

# Shrinkage-toward-prior constants (SDD Deep Dive 4). Named, versioned,
# explicitly NOT calibrated -- "stated defaults, not fitted values."
MASTERY_PRIOR_M0 = 0.3
MASTERY_PSEUDO_COUNT_K = 2.0

# Evidence-weight factors. w_base and the recency half-life are left as
# configurable, versioned parameters per the pack -- illustrative defaults,
# not precise/calibrated values.
EVIDENCE_W_BASE = 1.0
HINT_PENALTY = 0.6
RETRY_PENALTY = 0.5
RECENCY_HALF_LIFE_DAYS = 21.0

MASTERED_THRESHOLD = 0.85
MASTERED_MAX_UNCERTAINTY = 0.35

NOT_ASSESSED = "Not assessed"
NEEDS_ATTENTION = "Needs attention"
DEVELOPING = "Developing"
PROFICIENT = "Proficient"
MASTERED = "Mastered"


def difficulty_factor(difficulty: float) -> float:
    """0.5-1.5: a harder question carries more evidentiary weight."""
    return 0.5 + difficulty


def hint_factor(hints_used: int) -> float:
    return HINT_PENALTY ** hints_used


def retry_factor(retry_index: int) -> float:
    return RETRY_PENALTY ** retry_index


def evidence_weight_base(
    concept_weight: float,
    difficulty: float,
    hints_used: int,
    retry_index: int,
    w_base: float = EVIDENCE_W_BASE,
) -> float:
    """
    The w_i in the S/W sums, excluding recency (recency is applied later, at
    read time, against this stored value).
    """
    return (
        w_base
        * concept_weight
        * difficulty_factor(difficulty)
        * hint_factor(hints_used)
        * retry_factor(retry_index)
    )


def recency_factor(age_days: float, half_life_days: float = RECENCY_HALF_LIFE_DAYS) -> float:
    """Exponential decay: weight halves every `half_life_days`. age_days < 0
    (clock skew) is clamped to 0 -- no evidence gets a decay bonus."""
    age_days = max(age_days, 0.0)
    return 0.5 ** (age_days / half_life_days)


@dataclass(frozen=True)
class EvidenceEvent:
    """One stored, immutable piece of mastery evidence."""

    correctness: float  # o_i in [0, 1]
    evidence_weight_base: float  # w_i excluding recency
    created_at: datetime


@dataclass(frozen=True)
class MasteryState:
    mastery: float
    uncertainty: float
    evidence_weight_total: float  # W, after recency decay -- 0.0 means no evidence


def compute_mastery(events: List[EvidenceEvent], now: datetime) -> MasteryState:
    """
    Pure aggregation over an evidence history. W=0 (no events, i.e. a skipped
    diagnostic) is the explicit "not assessed" case: mastery equals the raw
    prior exactly, uncertainty is exactly 1.0 -- an honest unknown, not a
    fabricated baseline.
    """
    if not events:
        return MasteryState(mastery=MASTERY_PRIOR_M0, uncertainty=1.0, evidence_weight_total=0.0)

    s_total = 0.0
    w_total = 0.0
    for event in events:
        age_days = (now - event.created_at).total_seconds() / 86400.0
        effective_weight = event.evidence_weight_base * recency_factor(age_days)
        s_total += effective_weight * event.correctness
        w_total += effective_weight

    mastery = (s_total + MASTERY_PRIOR_M0 * MASTERY_PSEUDO_COUNT_K) / (w_total + MASTERY_PSEUDO_COUNT_K)
    uncertainty = 1.0 / math.sqrt(1.0 + w_total)
    return MasteryState(mastery=mastery, uncertainty=uncertainty, evidence_weight_total=w_total)


def classify_band(state: MasteryState) -> str:
    """
    The ONLY way to ask "what band is this" -- Mastered is a single gated
    check (mastery AND uncertainty), not two independent predicates a caller
    could apply separately and get wrong (pack requirement #10).
    """
    if state.evidence_weight_total <= 0.0:
        return NOT_ASSESSED
    if state.mastery >= MASTERED_THRESHOLD and state.uncertainty <= MASTERED_MAX_UNCERTAINTY:
        return MASTERED
    if state.mastery >= 0.70:
        return PROFICIENT
    if state.mastery >= 0.40:
        return DEVELOPING
    return NEEDS_ATTENTION


def is_mastered(state: MasteryState) -> bool:
    return classify_band(state) == MASTERED
