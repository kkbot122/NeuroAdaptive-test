"""
Presentation-affinity update and selection. Pure functions over a plain
{format: (exposure_count, success_count, effectiveness)} snapshot -- no DB
access, so the EMA math and selection policy are unit-testable in isolation
and the persistence wrapper (service.py) is the only thing that touches a
session.

GUARDRAIL: nothing here may resolve to, store, or return a fixed
learning-style label. `select_format` always returns one of
PresentationFormat's values (a per-user statistic-in-progress, not an
identity), and the caller can always override it -- an override is itself
just fed back in as evidence via `apply_manual_switch`, never treated as a
contradiction to correct.
"""
from dataclasses import dataclass, replace
from typing import Dict, List, Optional

from app.modules.adaptation.models import PresentationFormat

PRIOR_EFFECTIVENESS = 0.5
OUTCOME_EMA_ALPHA = 0.1
MANUAL_SWITCH_ALPHA = 0.03

# Deterministic, not random: every Nth exposure to the current best format,
# show the runner-up instead, to keep its estimate from going stale.
EXPLORATION_PERIOD = 5


@dataclass(frozen=True)
class AffinityState:
    exposure_count: int = 0
    success_count: int = 0
    effectiveness: float = PRIOR_EFFECTIVENESS


def apply_outcome(state: AffinityState, success: bool) -> AffinityState:
    """After a block is viewed in format F, the next checkpoint/quiz outcome
    on that block's concept updates affinity(F) -- small-step EMA, alpha~0.1."""
    target = 1.0 if success else 0.0
    new_effectiveness = state.effectiveness + OUTCOME_EMA_ALPHA * (target - state.effectiveness)
    return AffinityState(
        exposure_count=state.exposure_count + 1,
        success_count=state.success_count + (1 if success else 0),
        effectiveness=new_effectiveness,
    )


def apply_manual_switch(state: AffinityState, switched_toward: bool) -> AffinityState:
    """A manual format switch is weaker evidence than an outcome: switching
    away nudges this format's affinity down a little; switching toward
    nudges it up a little. Same (smaller) alpha either direction."""
    target = 1.0 if switched_toward else 0.0
    new_effectiveness = state.effectiveness + MANUAL_SWITCH_ALPHA * (target - state.effectiveness)
    return replace(state, effectiveness=new_effectiveness)


def select_format(
    affinities: Dict[str, AffinityState],
    exposure_index: int,
    is_struggling: bool,
    near_deadline: bool = False,
) -> str:
    """
    Deterministic, not exploratory (this phase; a contextual bandit is
    future work -- see the module docstring's Phase 14 note). Mostly picks
    the best-supported format; periodically shows the runner-up to keep its
    estimate current; never explores while the learner is struggling or
    near a deadline.
    """
    ranked = sorted(
        PresentationFormat, key=lambda f: affinities.get(f.value, AffinityState()).effectiveness, reverse=True
    )
    best = ranked[0].value
    if is_struggling or near_deadline:
        return best
    if len(ranked) > 1 and exposure_index % EXPLORATION_PERIOD == 0:
        return ranked[1].value
    return best
