"""
Versioned scoring policy: named weights, nothing hard-coded unlabeled.

CONFLICT, DECLARED AND RESOLVED BY REUSING EXISTING PRECEDENT: this phase's
pack asks for a live `urgency` (w4) and `time_fit` (w5) term (deadline
pacing, session-length fit). docs/adaptation-spec.md S7 (conflict "C-1")
already resolved the identical conflict for the paper's own r/tau terms:
frozen-scope.md excludes "time-based planning, calendars, target dates,
session fitting, forgetting curves, and spaced review" entirely, and no
deadline or session-length field exists anywhere in the schema (Course has
neither). Per AGENTS.md's authority order this phase reapplies C-1's exact
resolution rather than re-deriving a new one: w4 and w5 are pinned to 0.0,
and their combined published weight is redistributed proportionally across
the surviving positive terms, using C-1's own redistribution ratios (which
summed the same four terms: expected_gain, readiness, goal_relevance,
presentation_fit).

`urgency` and `time_fit` are still implemented as pure, callable, testable
functions in scoring.py -- computed and recorded in every decision's
`features` for transparency, just not weighted into the composite score.
Re-enabling either is then a policy-version bump, not a rewrite (the same
reasoning C-1 itself gives).

"Spaced review" as a candidate TYPE is dropped for the same reason (a
forgetting-curve/calendar mechanism); its intent -- resurfacing a concept
whose evidence shows it needs reinforcement -- is covered by
TARGETED_PRACTICE, which is triggered by mastery/uncertainty state, not by
elapsed time.
"""
from dataclasses import dataclass

POLICY_VERSION = "adaptive-policy-v1"

# Redistributed per C-1: original published 0.30/0.20/0.15/0.10 for
# g/R/rho/eta, with r=0.15 and tau=0.10 disabled and their 0.25 spread
# proportionally across the four survivors.
W_EXPECTED_GAIN = 0.400
W_READINESS = 0.267
W_GOAL_RELEVANCE = 0.200
W_URGENCY = 0.0  # disabled -- C-1
W_TIME_FIT = 0.0  # disabled -- C-1
W_PRESENTATION_FIT = 0.133

# Penalty terms are not part of the C-1 redistribution (Eq.4 subtracts them
# separately in both source documents). Unvalidated defaults.
W_DIFFICULTY_MISMATCH = 0.15
W_REPETITION_PENALTY = 0.25

# expected_gain's per-concept uncertainty bonus: a concept with no evidence
# yet (uncertainty=1) counts fully; one with strong evidence (uncertainty=0)
# counts at half -- there's still something to gain from practicing it, just
# less urgently than an unknown. Named, unvalidated default.
UNCERTAINTY_BONUS_FLOOR = 0.5
UNCERTAINTY_BONUS_SPAN = 0.5


@dataclass(frozen=True)
class PolicyWeights:
    version: str = POLICY_VERSION
    w_expected_gain: float = W_EXPECTED_GAIN
    w_readiness: float = W_READINESS
    w_goal_relevance: float = W_GOAL_RELEVANCE
    w_urgency: float = W_URGENCY
    w_time_fit: float = W_TIME_FIT
    w_presentation_fit: float = W_PRESENTATION_FIT
    w_difficulty_mismatch: float = W_DIFFICULTY_MISMATCH
    w_repetition_penalty: float = W_REPETITION_PENALTY


DEFAULT_POLICY = PolicyWeights()

# Fixed tie-break order (frozen-scope.md; not part of the score itself).
TIE_BREAK_ORDER = [
    "PREREQUISITE_REMEDIATION",
    "RESUME_INTERRUPTED",
    "TARGETED_PRACTICE",
    "NEW_LESSON",
    "CHALLENGE",
]
