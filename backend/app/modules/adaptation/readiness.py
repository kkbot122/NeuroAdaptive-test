"""
Prerequisite readiness. Pure -- operates on plain mastery floats the caller
already looked up, no DB access here.

REFINEMENT OVER docs/adaptation-spec.md S5, DECLARED: that spec's Eq.3 is
R(c) = min over ALL prerequisites (no hard/soft split). This phase's own
pack specifies a HARD/SOFT-differentiated version instead: the weakest HARD
prerequisite gates readiness (min, deliberately -- one missing hard
prerequisite blocks regardless of how strong the others are), while SOFT
prerequisites only "soften" the result via a small blended weight. This
does not contradict Eq.3's spirit (still a floor set by the weakest
prerequisite) and is a strict refinement matching EdgeStrength.HARD/SOFT,
which already exists in curriculum/models.py for exactly this purpose.

Per frozen-scope.md (and curriculum/models.py's own docstring), readiness
gates CANDIDATE GENERATION only -- diagnostic probing into a not-yet-ready
concept remains a distinct, explicit path (see candidates.py's
TARGETED_PRACTICE-as-diagnostic-probe note), never a silent relaxation of
this gate.
"""
from dataclasses import dataclass
from typing import List

READY_THRESHOLD = 0.6
SOFT_PREREQUISITE_WEIGHT = 0.3  # named, unvalidated default


def compute_readiness(hard_prerequisite_masteries: List[float], soft_prerequisite_masteries: List[float]) -> float:
    """
    min() over hard prerequisites is deliberate, not mean() -- a single weak
    hard prerequisite must pull readiness down to its own level regardless
    of how strong any other prerequisite is. A concept with no hard
    prerequisites has no hard gate (base = 1.0). Soft prerequisites blend in
    at a small fixed weight and, absent any soft prerequisite, contribute
    nothing -- so a hard-only concept's readiness equals the hard minimum
    exactly, not an averaged-down value.
    """
    base = min(hard_prerequisite_masteries) if hard_prerequisite_masteries else 1.0
    if not soft_prerequisite_masteries:
        return base
    soft_avg = sum(soft_prerequisite_masteries) / len(soft_prerequisite_masteries)
    return (1 - SOFT_PREREQUISITE_WEIGHT) * base + SOFT_PREREQUISITE_WEIGHT * soft_avg


def is_ready(readiness: float) -> bool:
    return readiness >= READY_THRESHOLD
