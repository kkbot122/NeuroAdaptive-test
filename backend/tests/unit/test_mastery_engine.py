"""
Pure mastery-engine tests: no DB, no network, fixture inputs only.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.mastery import engine

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_event(correctness, weight, days_ago=0):
    return engine.EvidenceEvent(
        correctness=correctness, evidence_weight_base=weight, created_at=NOW - timedelta(days=days_ago)
    )


class TestNoEvidence:
    def test_no_events_is_the_prior_exactly(self):
        state = engine.compute_mastery([], NOW)
        assert state.mastery == 0.3
        assert state.uncertainty == 1.0
        assert state.evidence_weight_total == 0.0

    def test_no_evidence_bands_as_not_assessed(self):
        state = engine.compute_mastery([], NOW)
        assert engine.classify_band(state) == engine.NOT_ASSESSED


class TestUncertaintyDecreasesWithEvidence:
    def test_strictly_decreasing_across_a_sequence_of_attempts(self):
        events = []
        uncertainties = []
        for _ in range(6):
            events.append(make_event(1.0, 1.0))
            uncertainties.append(engine.compute_mastery(events, NOW).uncertainty)
        for earlier, later in zip(uncertainties, uncertainties[1:]):
            assert later < earlier


class TestHintAndRetryPenalties:
    def test_a_hint_reduces_evidence_weight(self):
        no_hint = engine.evidence_weight_base(1.0, 0.5, hints_used=0, retry_index=0)
        one_hint = engine.evidence_weight_base(1.0, 0.5, hints_used=1, retry_index=0)
        assert one_hint < no_hint

    def test_a_later_retry_reduces_evidence_weight(self):
        first_try = engine.evidence_weight_base(1.0, 0.5, hints_used=0, retry_index=0)
        third_try = engine.evidence_weight_base(1.0, 0.5, hints_used=0, retry_index=2)
        assert third_try < first_try

    def test_harder_questions_carry_more_weight(self):
        easy = engine.evidence_weight_base(1.0, difficulty=0.1, hints_used=0, retry_index=0)
        hard = engine.evidence_weight_base(1.0, difficulty=0.9, hints_used=0, retry_index=0)
        assert hard > easy


class TestMultiConceptWeightSplit:
    def test_higher_concept_weight_gets_more_evidence_from_the_same_attempt(self):
        major = engine.evidence_weight_base(concept_weight=0.7, difficulty=0.5, hints_used=0, retry_index=0)
        minor = engine.evidence_weight_base(concept_weight=0.3, difficulty=0.5, hints_used=0, retry_index=0)
        assert major > minor
        assert major == pytest.approx(minor * (0.7 / 0.3))


class TestShrinkageRegressionGuard:
    def test_one_bad_answer_does_not_sharply_drop_strong_prior_mastery(self):
        """Large accumulated W, mastery near 0.85; one new incorrect attempt
        must move it far less than a naive unweighted running average would.
        This must fail if the formula is ever 'simplified' to a plain mean."""
        strong_events = [make_event(1.0, 5.0) for _ in range(20)]
        before = engine.compute_mastery(strong_events, NOW)
        assert before.mastery > 0.8

        after_bad_answer = engine.compute_mastery(
            strong_events + [make_event(0.0, 1.0)], NOW
        )
        shrinkage_drop = before.mastery - after_bad_answer.mastery

        # Naive running average over the same raw (correctness, weight) pairs.
        all_events = strong_events + [make_event(0.0, 1.0)]
        naive_before = sum(e.correctness * e.evidence_weight_base for e in strong_events) / sum(
            e.evidence_weight_base for e in strong_events
        )
        naive_after = sum(e.correctness * e.evidence_weight_base for e in all_events) / sum(
            e.evidence_weight_base for e in all_events
        )
        naive_drop = naive_before - naive_after

        assert shrinkage_drop < naive_drop


class TestMasteredIsGated:
    def test_high_mastery_but_thin_evidence_is_not_mastered(self):
        state = engine.MasteryState(mastery=0.85, uncertainty=0.5, evidence_weight_total=1.0)
        assert engine.classify_band(state) != engine.MASTERED
        assert not engine.is_mastered(state)

    def test_high_mastery_and_low_uncertainty_is_mastered(self):
        state = engine.MasteryState(mastery=0.9, uncertainty=0.1, evidence_weight_total=20.0)
        assert engine.classify_band(state) == engine.MASTERED
        assert engine.is_mastered(state)


class TestBandBoundaries:
    @pytest.mark.parametrize(
        "mastery,expected",
        [
            (0.39, engine.NEEDS_ATTENTION),
            (0.40, engine.DEVELOPING),
            (0.69, engine.DEVELOPING),
            (0.70, engine.PROFICIENT),
            (0.84, engine.PROFICIENT),
        ],
    )
    def test_boundary_values_map_to_the_documented_band(self, mastery, expected):
        state = engine.MasteryState(mastery=mastery, uncertainty=0.1, evidence_weight_total=10.0)
        assert engine.classify_band(state) == expected


class TestRecencyDecay:
    def test_older_evidence_contributes_less_at_read_time(self):
        recent = engine.compute_mastery([make_event(1.0, 1.0, days_ago=0)], NOW)
        old = engine.compute_mastery([make_event(1.0, 1.0, days_ago=200)], NOW)
        # Same raw correctness/weight, but the old event has decayed toward
        # negligible influence, so its resulting mastery sits closer to the
        # untouched prior (0.3) than the recent event's does.
        assert abs(old.mastery - engine.MASTERY_PRIOR_M0) < abs(recent.mastery - engine.MASTERY_PRIOR_M0)
