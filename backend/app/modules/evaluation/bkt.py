"""
A2 ablation: classic Bayesian Knowledge Tracing, reimplemented from scratch,
run over the SAME logged attempt sequence the weighted-evidence model
(Phase 3, mastery/engine.py) already scores -- infrastructure for a
comparison, not a conclusion about which model is better (mandate). Nothing
here feeds back into the live mastery engine; production mastery is always
Phase 3's weighted-evidence model. This module exists only so the
evaluation harness can compute a second number over the same history.

Standard BKT update (four named, unvalidated-default parameters, exactly
the parameters classic BKT requires -- not tuned against this project's
data):
  P(L0): prior probability the skill is already known before any evidence.
  P(T):  probability of learning the skill between two opportunities.
  P(G):  probability of guessing correctly despite not knowing the skill.
  P(S):  probability of a slip -- answering incorrectly despite knowing it.
"""
from dataclasses import dataclass
from typing import List


P_L0 = 0.3
P_T = 0.1
P_G = 0.2
P_S = 0.1


@dataclass(frozen=True)
class BktParams:
    p_l0: float = P_L0
    p_t: float = P_T
    p_g: float = P_G
    p_s: float = P_S


def _posterior_given_correct(p_l: float, params: BktParams) -> float:
    numerator = p_l * (1 - params.p_s)
    denominator = numerator + (1 - p_l) * params.p_g
    return numerator / denominator if denominator > 0 else p_l


def _posterior_given_incorrect(p_l: float, params: BktParams) -> float:
    numerator = p_l * params.p_s
    denominator = numerator + (1 - p_l) * (1 - params.p_g)
    return numerator / denominator if denominator > 0 else p_l


def compute_bkt_mastery(correctness_sequence: List[float], params: BktParams = BktParams()) -> float:
    """
    Runs the standard BKT update over a sequence of correctness values
    (0/1, or fractional partial credit treated as a soft evidence weight on
    the same update rule) in chronological order, returning the final
    P(known). An empty sequence returns the prior P(L0) unchanged -- the
    same "no evidence yet, honest unknown" shape Phase 3's weighted-evidence
    model uses.
    """
    p_l = params.p_l0
    for correctness in correctness_sequence:
        if correctness >= 0.5:
            p_l = _posterior_given_correct(p_l, params)
        else:
            p_l = _posterior_given_incorrect(p_l, params)
        # Learning can happen between any two opportunities regardless of
        # this one's outcome.
        p_l = p_l + (1 - p_l) * params.p_t
    return p_l
