from app.modules.adaptation.models import PresentationFormat
from app.modules.adaptation.presentation import (
    AffinityState,
    MANUAL_SWITCH_ALPHA,
    OUTCOME_EMA_ALPHA,
    apply_manual_switch,
    apply_outcome,
    select_format,
)


class TestOutcomeUpdate:
    def test_positive_outcome_increases_affinity_by_the_documented_step(self):
        before = AffinityState(effectiveness=0.5)
        after = apply_outcome(before, success=True)
        expected = 0.5 + OUTCOME_EMA_ALPHA * (1.0 - 0.5)
        assert after.effectiveness == expected

    def test_only_the_touched_formats_state_changes(self):
        worked_example_before = AffinityState(effectiveness=0.5)
        diagram_before = AffinityState(effectiveness=0.5)
        worked_example_after = apply_outcome(worked_example_before, success=True)
        assert diagram_before.effectiveness == 0.5  # untouched, immutable dataclass
        assert worked_example_after.effectiveness != diagram_before.effectiveness


class TestManualSwitchIsWeakerEvidence:
    def test_switching_away_nudges_down_by_the_smaller_step(self):
        before = AffinityState(effectiveness=0.5)
        after = apply_manual_switch(before, switched_toward=False)
        expected = 0.5 + MANUAL_SWITCH_ALPHA * (0.0 - 0.5)
        assert after.effectiveness == expected

    def test_manual_switch_step_is_smaller_than_outcome_step(self):
        assert MANUAL_SWITCH_ALPHA < OUTCOME_EMA_ALPHA


class TestSelection:
    def test_best_supported_format_is_selected_by_default(self):
        affinities = {
            PresentationFormat.CONCISE.value: AffinityState(effectiveness=0.9),
            PresentationFormat.DIAGRAM.value: AffinityState(effectiveness=0.2),
        }
        assert select_format(affinities, exposure_index=1, is_struggling=False) == PresentationFormat.CONCISE.value

    def test_struggling_learner_never_gets_the_periodic_alternative(self):
        affinities = {
            PresentationFormat.CONCISE.value: AffinityState(effectiveness=0.9),
            PresentationFormat.DIAGRAM.value: AffinityState(effectiveness=0.2),
        }
        # exposure_index=0 would normally trigger the periodic alternative.
        selected = select_format(affinities, exposure_index=0, is_struggling=True)
        assert selected == PresentationFormat.CONCISE.value

    def test_near_deadline_also_suppresses_exploration(self):
        affinities = {
            PresentationFormat.CONCISE.value: AffinityState(effectiveness=0.9),
            PresentationFormat.DIAGRAM.value: AffinityState(effectiveness=0.2),
        }
        selected = select_format(affinities, exposure_index=0, is_struggling=False, near_deadline=True)
        assert selected == PresentationFormat.CONCISE.value

    def test_no_output_is_ever_a_fixed_style_label(self):
        """Guardrail at the unit level: every possible return value is a
        PresentationFormat enum value, never free text."""
        affinities = {}
        for exposure_index in range(10):
            selected = select_format(affinities, exposure_index=exposure_index, is_struggling=False)
            assert selected in {f.value for f in PresentationFormat}
