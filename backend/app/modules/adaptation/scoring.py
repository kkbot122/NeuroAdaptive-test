"""
Pure candidate scoring. No DB session, no network client, no clock read
anywhere in this module -- every input arrives as a plain value on
LearnerStateSnapshot/Candidate/Context (mandate: "a pure function of
(learner state snapshot, candidate list, context) with no I/O inside it").
This is what makes it unit-testable with fixtures and swappable for a
learned policy later without touching any caller (service.py is the only
caller, and it only does persistence/reads around this function).
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from uuid import UUID

from app.modules.adaptation.policy import (
    DEFAULT_POLICY,
    TIE_BREAK_ORDER,
    UNCERTAINTY_BONUS_FLOOR,
    UNCERTAINTY_BONUS_SPAN,
    PolicyWeights,
)


@dataclass(frozen=True)
class ConceptState:
    mastery: float
    uncertainty: float
    importance: float
    readiness: float  # precomputed via readiness.compute_readiness by the caller


@dataclass(frozen=True)
class Candidate:
    activity_type: str
    concept_ids: Tuple[UUID, ...]
    lesson_id: Optional[UUID]
    estimated_minutes: float
    activity_difficulty: float  # [0, 1]
    default_format: str  # a PresentationFormat value
    remediation_bonus: float = 0.0  # concrete trigger bonus (mandate B)


@dataclass(frozen=True)
class LearnerStateSnapshot:
    concepts: Dict[UUID, ConceptState]
    presentation_affinity: Dict[str, float]  # format -> effectiveness
    goal_text: Optional[str] = None
    session_minutes: Optional[float] = None
    rejected_candidate_keys: Set[Tuple[str, Tuple[UUID, ...]]] = field(default_factory=set)


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: Candidate
    score: float
    features: Dict[str, float]


def expected_gain(candidate: Candidate, state: LearnerStateSnapshot) -> float:
    if not candidate.concept_ids:
        return 0.0
    total = 0.0
    for concept_id in candidate.concept_ids:
        c = state.concepts[concept_id]
        uncertainty_bonus = UNCERTAINTY_BONUS_FLOOR + UNCERTAINTY_BONUS_SPAN * c.uncertainty
        total += (1.0 - c.mastery) * c.importance * uncertainty_bonus
    return total / len(candidate.concept_ids)


def readiness_score(candidate: Candidate, state: LearnerStateSnapshot) -> float:
    if not candidate.concept_ids:
        return 1.0
    return sum(state.concepts[c].readiness for c in candidate.concept_ids) / len(candidate.concept_ids)


def goal_relevance(candidate: Candidate, state: LearnerStateSnapshot) -> float:
    goal = (state.goal_text or "").lower()
    if "exam" in goal:
        return 1.0 if candidate.activity_type in ("TARGETED_PRACTICE", "CHALLENGE") else 0.5
    if "understand" in goal or "deep" in goal:
        return 1.0 if candidate.activity_type == "NEW_LESSON" else 0.5
    return 0.5  # no stated goal, or one that doesn't match a known pattern


def urgency(candidate: Candidate, state: LearnerStateSnapshot) -> float:
    """Computed for transparency; NOT weighted into the composite score --
    see policy.py's module docstring (C-1). No deadline field exists to
    compute this against, so it is a documented, inert placeholder."""
    return 0.0


def session_time_fit(candidate: Candidate, state: LearnerStateSnapshot) -> float:
    """Computed for transparency; NOT weighted into the composite score --
    see policy.py's module docstring (C-1). Still meaningful as a pure
    function of its inputs when a session length happens to be supplied."""
    if state.session_minutes is None:
        return 1.0
    mismatch = abs(candidate.estimated_minutes - state.session_minutes) / max(state.session_minutes, 1.0)
    return max(0.0, 1.0 - mismatch)


def presentation_fit(candidate: Candidate, state: LearnerStateSnapshot) -> float:
    return state.presentation_affinity.get(candidate.default_format, 0.5)


def difficulty_mismatch(candidate: Candidate, state: LearnerStateSnapshot) -> float:
    if not candidate.concept_ids:
        return 0.0
    mean_mastery = sum(state.concepts[c].mastery for c in candidate.concept_ids) / len(candidate.concept_ids)
    target_difficulty = 1.0 - mean_mastery  # a learner with low mastery wants easier material
    return abs(candidate.activity_difficulty - target_difficulty)


def repetition_penalty(candidate: Candidate, state: LearnerStateSnapshot) -> float:
    key = (candidate.activity_type, candidate.concept_ids)
    return 1.0 if key in state.rejected_candidate_keys else 0.0


def score_candidate(
    candidate: Candidate, state: LearnerStateSnapshot, policy: PolicyWeights = DEFAULT_POLICY
) -> ScoredCandidate:
    features = {
        "expected_gain": expected_gain(candidate, state),
        "readiness": readiness_score(candidate, state),
        "goal_relevance": goal_relevance(candidate, state),
        "urgency": urgency(candidate, state),
        "time_fit": session_time_fit(candidate, state),
        "presentation_fit": presentation_fit(candidate, state),
        "difficulty_mismatch": difficulty_mismatch(candidate, state),
        "repetition_penalty": repetition_penalty(candidate, state),
    }
    score = (
        policy.w_expected_gain * features["expected_gain"]
        + policy.w_readiness * features["readiness"]
        + policy.w_goal_relevance * features["goal_relevance"]
        + policy.w_urgency * features["urgency"]
        + policy.w_time_fit * features["time_fit"]
        + policy.w_presentation_fit * features["presentation_fit"]
        - policy.w_difficulty_mismatch * features["difficulty_mismatch"]
        - policy.w_repetition_penalty * features["repetition_penalty"]
        + candidate.remediation_bonus
    )
    return ScoredCandidate(candidate=candidate, score=score, features=features)


def recommend(
    candidates: List[Candidate], state: LearnerStateSnapshot, policy: PolicyWeights = DEFAULT_POLICY
) -> List[ScoredCandidate]:
    """
    Scores and ranks every candidate. Ties broken by the fixed
    frozen-scope.md order (not part of the score itself). Returns the full
    ranked list -- index 0 is the recommendation, the rest are alternatives.
    Pure: no I/O, no persistence. The caller (service.py) writes the
    AdaptationDecision from this result before returning anything.
    """
    scored = [score_candidate(c, state, policy) for c in candidates]

    def sort_key(sc: ScoredCandidate):
        tie_rank = (
            TIE_BREAK_ORDER.index(sc.candidate.activity_type)
            if sc.candidate.activity_type in TIE_BREAK_ORDER
            else len(TIE_BREAK_ORDER)
        )
        return (-sc.score, tie_rank)

    return sorted(scored, key=sort_key)
