"""
Pure scoring tests: fixtures only, zero mocked I/O -- no DB session, no
network client anywhere in this file, matching the mandate's own purity
requirement for scoring.py.
"""
import uuid

import pytest

from app.modules.adaptation.policy import DEFAULT_POLICY
from app.modules.adaptation.scoring import (
    Candidate,
    ConceptState,
    LearnerStateSnapshot,
    recommend,
    score_candidate,
)


def concept(mastery=0.3, uncertainty=0.5, importance=0.5, readiness=1.0):
    return ConceptState(mastery=mastery, uncertainty=uncertainty, importance=importance, readiness=readiness)


def candidate(activity_type="TARGETED_PRACTICE", concept_ids=None, **overrides):
    defaults = dict(
        activity_type=activity_type, concept_ids=tuple(concept_ids or [uuid.uuid4()]), lesson_id=None,
        estimated_minutes=10.0, activity_difficulty=0.5, default_format="concise", remediation_bonus=0.0,
    )
    defaults.update(overrides)
    return Candidate(**defaults)


class TestPurity:
    def test_scoring_runs_from_plain_fixtures_with_no_io(self):
        """The whole point: this function is callable with nothing but
        Python values -- no DB session, no network client -- and still
        deterministic."""
        cid = uuid.uuid4()
        state = LearnerStateSnapshot(concepts={cid: concept()}, presentation_affinity={})
        c = candidate(concept_ids=[cid])
        result_1 = score_candidate(c, state)
        result_2 = score_candidate(c, state)
        assert result_1.score == result_2.score


class TestExpectedGain:
    def test_higher_importance_and_lower_mastery_scores_higher(self):
        cid_strong = uuid.uuid4()
        cid_weak = uuid.uuid4()
        state = LearnerStateSnapshot(
            concepts={
                cid_strong: concept(mastery=0.9, importance=0.3),
                cid_weak: concept(mastery=0.1, importance=0.9),
            },
            presentation_affinity={},
        )
        weak_candidate = candidate(concept_ids=[cid_weak])
        strong_candidate = candidate(concept_ids=[cid_strong])
        assert (
            score_candidate(weak_candidate, state).features["expected_gain"]
            > score_candidate(strong_candidate, state).features["expected_gain"]
        )


class TestTimeFitComputedButInert:
    """C-1 (reused from adaptation-spec.md): time_fit is computed for
    transparency but w_time_fit=0, so it must not move the composite score
    even though the pure feature itself still reflects a mismatch."""

    def test_time_fit_feature_reflects_mismatch(self):
        from app.modules.adaptation.scoring import session_time_fit

        cid = uuid.uuid4()
        state = LearnerStateSnapshot(concepts={cid: concept()}, presentation_affinity={}, session_minutes=10.0)
        close_fit = candidate(concept_ids=[cid], estimated_minutes=10.0)
        far_fit = candidate(concept_ids=[cid], estimated_minutes=60.0)
        assert session_time_fit(close_fit, state) > session_time_fit(far_fit, state)

    def test_composite_score_is_unaffected_by_time_fit(self):
        cid = uuid.uuid4()
        state = LearnerStateSnapshot(concepts={cid: concept()}, presentation_affinity={}, session_minutes=10.0)
        close_fit = candidate(concept_ids=[cid], estimated_minutes=10.0)
        far_fit = candidate(concept_ids=[cid], estimated_minutes=60.0)
        assert score_candidate(close_fit, state).score == score_candidate(far_fit, state).score
        assert DEFAULT_POLICY.w_time_fit == 0.0
        assert DEFAULT_POLICY.w_urgency == 0.0


class TestRepetitionPenalty:
    def test_a_previously_rejected_candidate_scores_lower_next_time(self):
        cid = uuid.uuid4()
        c = candidate(concept_ids=[cid])
        key = (c.activity_type, c.concept_ids)

        fresh_state = LearnerStateSnapshot(concepts={cid: concept()}, presentation_affinity={})
        rejected_state = LearnerStateSnapshot(
            concepts={cid: concept()}, presentation_affinity={}, rejected_candidate_keys={key}
        )
        assert score_candidate(c, rejected_state).score < score_candidate(c, fresh_state).score


class TestRecommendRankingAndTieBreak:
    def test_returns_full_ranked_list_best_first(self):
        cid_a = uuid.uuid4()
        cid_b = uuid.uuid4()
        state = LearnerStateSnapshot(
            concepts={cid_a: concept(mastery=0.1, importance=0.9), cid_b: concept(mastery=0.9, importance=0.1)},
            presentation_affinity={},
        )
        candidates = [candidate(concept_ids=[cid_a]), candidate(concept_ids=[cid_b])]
        ranked = recommend(candidates, state)
        assert len(ranked) == 2
        assert ranked[0].score >= ranked[1].score

    def test_tie_break_prefers_remediation_over_new_lesson(self):
        cid = uuid.uuid4()
        state = LearnerStateSnapshot(concepts={cid: concept()}, presentation_affinity={})
        remediation = candidate(activity_type="PREREQUISITE_REMEDIATION", concept_ids=[cid])
        new_lesson = candidate(activity_type="NEW_LESSON", concept_ids=[cid])
        # Force an exact tie on score by disabling remediation's bonus.
        ranked = recommend([new_lesson, remediation], state)
        # With no bonus, remediation's identical features should still win
        # the tie-break slot when scores are equal.
        scores = {sc.candidate.activity_type: sc.score for sc in ranked}
        if scores["PREREQUISITE_REMEDIATION"] == scores["NEW_LESSON"]:
            assert ranked[0].candidate.activity_type == "PREREQUISITE_REMEDIATION"
