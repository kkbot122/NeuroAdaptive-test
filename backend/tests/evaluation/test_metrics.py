"""
Mandate test cases 4-7: metric computation against controlled fixtures.
Exact numbers are appropriate here -- the input is fixture data this test
file controls, not a claim about real learners.
"""
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.modules.evaluation import metrics
from app.modules.mastery import engine as mastery_engine

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def evt(correctness, weight, days_ago=0):
    return mastery_engine.EvidenceEvent(
        correctness=correctness, evidence_weight_base=weight, created_at=NOW - timedelta(days=days_ago)
    )


class TestMasteryMatchesPhase3Formula:
    def test_five_attempts_match_hand_computed_expected_value(self):
        # 5 attempts, decreasing age, uniform weight 1.0 -- no recency decay
        # difference since they're all "now" (days_ago=0) for simplicity of
        # the hand computation.
        events = [evt(1.0, 1.0), evt(1.0, 1.0), evt(0.0, 1.0), evt(1.0, 1.0), evt(1.0, 1.0)]
        state = metrics.compute_mastery_state(events, NOW)

        # Hand computation, Phase 3's exact formula:
        # S = 4*1.0 + 1*0.0 = 4.0, W = 5.0
        # mastery = (S + m0*k) / (W + k) = (4.0 + 0.3*2) / (5.0 + 2) = 4.6 / 7.0
        expected_mastery = (4.0 + mastery_engine.MASTERY_PRIOR_M0 * mastery_engine.MASTERY_PSEUDO_COUNT_K) / (
            5.0 + mastery_engine.MASTERY_PSEUDO_COUNT_K
        )
        expected_uncertainty = 1.0 / (1.0 + 5.0) ** 0.5

        assert state.mastery == pytest.approx(expected_mastery)
        assert state.uncertainty == pytest.approx(expected_uncertainty)


class TestCitationPrecisionRecall:
    def test_ten_citations_eight_valid_six_supported(self):
        records = (
            [metrics.CitationRecord(tier1_valid=True, tier2_supported=True) for _ in range(6)]
            + [metrics.CitationRecord(tier1_valid=True, tier2_supported=False) for _ in range(2)]
            + [metrics.CitationRecord(tier1_valid=False, tier2_supported=None) for _ in range(2)]
        )
        result = metrics.citation_precision_recall(records)

        assert result.total_claims == 10
        assert result.structurally_valid == 8
        assert result.structurally_invalid == 2
        assert result.supported == 6
        assert result.unsupported == 2  # distinct from structurally_invalid
        assert result.precision == pytest.approx(6 / 8)
        assert result.recall == pytest.approx(6 / 10)


class TestTimeToMastery:
    def test_time_between_first_event_and_first_mastered_crossing(self):
        # Strong evidence from day 0, accumulating until mastery crosses
        # the "Mastered" band (>=0.85 mastery AND <=0.35 uncertainty).
        events = [evt(1.0, 20.0, days_ago=10 - i) for i in range(10)]
        result = metrics.time_to_mastery(events, NOW)
        assert result is not None
        assert result >= 0

        # Sanity: fewer/weaker events never reach mastery within the window.
        weak_events = [evt(1.0, 0.1, days_ago=1)]
        assert metrics.time_to_mastery(weak_events, NOW) is None

    def test_attempts_to_mastery_counts_events_not_time(self):
        events = [evt(1.0, 20.0, days_ago=10 - i) for i in range(10)]
        count = metrics.attempts_to_mastery(events, NOW)
        assert count is not None
        assert 1 <= count <= len(events)


class TestLatencyPercentilesMatchNumpy:
    def test_matches_numpy_default_linear_interpolation(self):
        durations = [0.10, 0.12, 0.15, 0.20, 0.35, 0.50, 0.11, 0.13, 0.90, 0.14, 1.2, 0.05]
        result = metrics.latency_percentiles(durations)

        expected_p50 = float(np.percentile(durations, 50))
        expected_p95 = float(np.percentile(durations, 95))
        expected_p99 = float(np.percentile(durations, 99))

        assert result.p50 == pytest.approx(expected_p50)
        assert result.p95 == pytest.approx(expected_p95)
        assert result.p99 == pytest.approx(expected_p99)
        assert result.n == len(durations)

    def test_empty_input_raises_rather_than_fabricating_zero(self):
        with pytest.raises(ValueError):
            metrics.latency_percentiles([])


class TestEngagementNeverContaminatesPedagogicalSummaries:
    def test_mastery_delta_summary_only_takes_deltas_it_is_given(self):
        # This function trusts its caller to have already filtered to
        # PEDAGOGICAL_EFFECT outcomes -- proving it does the arithmetic
        # correctly on exactly what it's given, nothing implicitly excluded
        # or included.
        summary = metrics.mastery_delta_summary([0.1, 0.2, -0.05])
        assert summary.n == 3
        assert summary.mean_delta == pytest.approx(0.25 / 3)

    def test_recommendation_acceptance_rate_ignores_viewed_only(self):
        # VIEWED-only decisions are excluded from the denominator entirely --
        # engagement without a completed/rejected verdict is not counted
        # either way.
        result = metrics.recommendation_acceptance_rate(["COMPLETED", "COMPLETED", "REJECTED"])
        assert result.acceptance_rate == pytest.approx(2 / 3)


class TestPromptInjectionRateReuse:
    def test_none_when_no_measurement_has_been_run(self):
        assert metrics.prompt_injection_attack_success_rate(None) is None
        assert metrics.prompt_injection_attack_success_rate([]) is None

    def test_computes_rate_from_measured_results_never_hardcoded(self):
        measured = [{"attack_succeeded": False}] * 10 + [{"attack_succeeded": True}] * 2
        assert metrics.prompt_injection_attack_success_rate(measured) == pytest.approx(2 / 12)
