"""
Unit tests for the adaptation engine — pure math, no network.

These constants are hand-chosen and unvalidated (SYSTEM_ARCHITECTURE.md §10).
The tests below pin behaviour and invariants, not correctness against any
learning outcome, and must not be read as evidence that the weights are right.
"""
import pytest

from app.services.adaptation import (
    CALIBRATION_QUESTIONS,
    RAW_DIRECTIVES,
    apply_signals_to_scores,
    archetype_to_scores,
    build_fslsm_system_prompt,
    compute_format_effectiveness,
    generate_system_prompt,
    infer_signals_from_prompt,
    normalize_profile,
    process_calibration_answers,
    reinforce_scores_from_effectiveness,
    select_directives,
    update_scores_from_feedback,
    _norm,
    _soft,
)

DIMS = ("visual", "structural", "active", "logic")
NEUTRAL = {d: 50.0 for d in DIMS}


class TestNormalisation:
    @pytest.mark.parametrize("raw,expected", [(0.0, -1.0), (50.0, 0.0), (100.0, 1.0)])
    def test_norm_maps_percentage_to_signed_unit(self, raw, expected):
        assert _norm(raw) == pytest.approx(expected)

    def test_norm_clamps_out_of_range_input(self):
        assert _norm(-50.0) == -1.0
        assert _norm(150.0) == 1.0

    def test_soft_preserves_sign_and_zero(self):
        assert _soft(0.0) == 0.0
        assert _soft(0.5) > 0
        assert _soft(-0.5) < 0

    def test_soft_is_bounded(self):
        assert abs(_soft(100.0)) < 1.0000001

    def test_soft_is_monotonic(self):
        values = [_soft(_norm(x)) for x in range(0, 101, 10)]
        assert values == sorted(values)

    def test_normalize_profile_covers_all_dimensions(self):
        assert set(normalize_profile({})) == set(DIMS)

    def test_missing_dimension_defaults_to_neutral(self):
        assert normalize_profile({})["visual"] == pytest.approx(0.0)


class TestDirectiveSelection:
    def test_neutral_profile_gets_the_fallback(self):
        """No dimension clears the gate, so a usable default must still return."""
        selected = select_directives(normalize_profile(NEUTRAL), RAW_DIRECTIVES)
        assert selected == ["Balance structure with depth; mix text with clarity."]

    def test_never_returns_empty(self):
        for profile in ({}, NEUTRAL, {d: 0.0 for d in DIMS}):
            assert select_directives(normalize_profile(profile), RAW_DIRECTIVES)

    def test_respects_max_n(self):
        strong = normalize_profile({d: 100.0 for d in DIMS})
        assert len(select_directives(strong, RAW_DIRECTIVES, max_n=2)) <= 2

    def test_high_visual_selects_a_visual_directive(self):
        profile = normalize_profile({**NEUTRAL, "visual": 100.0})
        assert any("diagram" in d.lower() for d in select_directives(profile, RAW_DIRECTIVES))

    def test_opposite_poles_select_opposite_directives(self):
        high = select_directives(normalize_profile({**NEUTRAL, "visual": 100.0}), RAW_DIRECTIVES)
        low = select_directives(normalize_profile({**NEUTRAL, "visual": 0.0}), RAW_DIRECTIVES)
        assert high != low


class TestSystemPrompts:
    def test_includes_learner_style_and_format_rules(self):
        prompt = generate_system_prompt(NEUTRAL)
        assert "Learner style:" in prompt
        assert "Rules:" in prompt

    def test_requires_a_mapping_not_a_label(self):
        """
        Regression for K-9: a str reached this and raised AttributeError,
        making /content/articles/{id} a guaranteed 500.
        """
        with pytest.raises(AttributeError):
            generate_system_prompt("THE_PIONEER")

    def test_fslsm_prompt_accepts_signed_vectors(self):
        prompt = build_fslsm_system_prompt(
            {"processing": 0.8, "perception": -0.3, "reception": -0.9, "understanding": 0.2}
        )
        assert "FSLSM-calibrated" in prompt


class TestSignalInference:
    @pytest.mark.parametrize(
        "prompt,signal",
        [("can you draw a diagram", "requested_diagram"),
         ("give me a tldr", "requested_summary"),
         ("walk me through it step by step", "requested_steps"),
         ("why does that happen", "prompt_why")],
    )
    def test_keywords_map_to_signals(self, prompt, signal):
        assert signal in infer_signals_from_prompt(prompt)

    def test_is_case_insensitive(self):
        assert "requested_diagram" in infer_signals_from_prompt("DIAGRAM please")

    def test_unremarkable_prompt_yields_nothing(self):
        assert infer_signals_from_prompt("hello there") == []


class TestScoreUpdates:
    def test_applies_the_mapped_delta(self):
        updated = apply_signals_to_scores(dict(NEUTRAL), ["requested_diagram"])
        assert updated["visual"] == 55.0

    def test_scores_stay_within_bounds(self):
        """The invariant: no sequence of signals may push a score off [0, 100]."""
        scores = dict(NEUTRAL)
        for _ in range(200):
            scores = apply_signals_to_scores(scores, ["requested_diagram"])
        assert scores["visual"] == 100.0

        scores = dict(NEUTRAL)
        for _ in range(200):
            scores = update_scores_from_feedback(scores, "prefer_text")
        assert scores["visual"] == 0.0

    def test_does_not_mutate_input(self):
        original = dict(NEUTRAL)
        apply_signals_to_scores(original, ["requested_diagram"])
        assert original["visual"] == 50.0

    def test_unknown_signal_is_a_no_op(self):
        assert apply_signals_to_scores(dict(NEUTRAL), ["nope"]) == NEUTRAL


class TestCalibration:
    def test_known_answer_produces_its_deltas(self):
        totals = process_calibration_answers({"unfamiliar": "A"})
        assert totals["visual"] == 20.0

    def test_unknown_question_and_option_are_ignored(self):
        assert process_calibration_answers({"nope": "A"}) == {d: 0.0 for d in DIMS}
        assert process_calibration_answers({"unfamiliar": "Z"}) == {d: 0.0 for d in DIMS}

    def test_option_keys_are_case_insensitive(self):
        assert process_calibration_answers({"unfamiliar": "a"})["visual"] == 20.0

    def test_every_question_declares_four_options_with_deltas(self):
        for question in CALIBRATION_QUESTIONS:
            assert set(question["options"]) == {"A", "B", "C", "D"}
            for option in question["options"].values():
                assert option["delta"]
                assert set(option["delta"]) <= set(DIMS)


class TestEffectiveness:
    def test_weights_sum_to_one(self):
        assert compute_format_effectiveness(1.0, 1.0, 1.0, 1.0) == pytest.approx(1.0)
        assert compute_format_effectiveness(0.0, 0.0, 0.0, 0.0) == pytest.approx(0.0)

    def test_accuracy_carries_exactly_half_the_weight(self):
        """
        Accuracy alone equals completion + engagement + feedback combined
        (0.5 vs 0.2 + 0.2 + 0.1). Pinned because it is a deliberate-looking
        balance that a future weight change would silently break.
        """
        accuracy_only = compute_format_effectiveness(1.0, 0.0, 0.0, 0.0)
        everything_else = compute_format_effectiveness(0.0, 1.0, 1.0, 1.0)
        assert accuracy_only == pytest.approx(0.5)
        assert accuracy_only == pytest.approx(everything_else)

    def test_accuracy_is_the_largest_single_weight(self):
        singles = [
            compute_format_effectiveness(1.0, 0.0, 0.0, 0.0),
            compute_format_effectiveness(0.0, 1.0, 0.0, 0.0),
            compute_format_effectiveness(0.0, 0.0, 1.0, 0.0),
            compute_format_effectiveness(0.0, 0.0, 0.0, 1.0),
        ]
        assert max(singles) == singles[0]

    def test_effectiveness_above_half_reinforces(self):
        updated = reinforce_scores_from_effectiveness(dict(NEUTRAL), 1.0, ["visual"])
        assert updated["visual"] > 50.0

    def test_effectiveness_below_half_attenuates(self):
        updated = reinforce_scores_from_effectiveness(dict(NEUTRAL), 0.0, ["visual"])
        assert updated["visual"] < 50.0

    def test_neutral_effectiveness_changes_nothing(self):
        updated = reinforce_scores_from_effectiveness(dict(NEUTRAL), 0.5, ["visual"])
        assert updated["visual"] == pytest.approx(50.0)

    def test_only_active_dimensions_move(self):
        updated = reinforce_scores_from_effectiveness(dict(NEUTRAL), 1.0, ["visual"])
        assert updated["logic"] == 50.0

    def test_reinforcement_respects_bounds(self):
        scores = dict(NEUTRAL)
        for _ in range(500):
            scores = reinforce_scores_from_effectiveness(scores, 1.0, ["visual"])
        assert scores["visual"] == 100.0


class TestArchetypeBackCompat:
    def test_every_known_label_maps_to_all_four_dimensions(self):
        for label in ("THE_PIONEER", "THE_VISUALIZER", "THE_ARCHITECT",
                      "THE_SPRINTER", "THE_DEBUGGER", "THE_DEEP_SCHOLAR"):
            assert set(archetype_to_scores(label)) == set(DIMS)

    def test_unknown_label_falls_back_to_neutral_pioneer(self):
        assert archetype_to_scores("NOT_A_LABEL") == archetype_to_scores("THE_PIONEER")

    def test_output_is_a_mapping_the_engine_accepts(self):
        """The bridge must emit what generate_system_prompt requires."""
        assert "Learner style:" in generate_system_prompt(archetype_to_scores("THE_VISUALIZER"))
