"""Unit tests for the FSLSM vector engine — pure math, no I/O."""
import pytest

from app.services.fslsm import (
    DIMENSIONS,
    clamp,
    default_vector,
    describe_vector,
    label_dimension,
    nudge,
    nudge_vector,
    signals_to_deltas,
    validate_vector,
)


class TestClamp:
    def test_passes_through_in_range(self):
        assert clamp(0.5) == 0.5

    @pytest.mark.parametrize("value,expected", [(2.0, 1.0), (-2.0, -1.0)])
    def test_clamps_to_bounds(self, value, expected):
        assert clamp(value) == expected

    def test_bounds_are_inclusive(self):
        assert clamp(1.0) == 1.0
        assert clamp(-1.0) == -1.0


class TestNudge:
    def test_applies_delta(self):
        assert nudge(0.0, 0.3) == pytest.approx(0.3)

    def test_result_cannot_escape_bounds(self):
        """The invariant that matters: repeated nudges must never run away."""
        assert nudge(0.95, 0.5) == 1.0
        assert nudge(-0.95, -0.5) == -1.0

    def test_saturates_rather_than_wrapping(self):
        value = 0.0
        for _ in range(100):
            value = nudge(value, 0.1)
        assert value == 1.0


class TestNudgeVector:
    def test_applies_only_named_dimensions(self):
        result = nudge_vector(default_vector(), {"reception": -0.2})
        assert result["reception"] == pytest.approx(-0.2)
        assert result["processing"] == 0.0

    def test_ignores_unknown_dimensions(self):
        result = nudge_vector(default_vector(), {"nonsense": 0.9})
        assert result == default_vector()

    def test_always_returns_all_dimensions(self):
        assert set(nudge_vector({}, {})) == set(DIMENSIONS)

    def test_does_not_mutate_input(self):
        original = default_vector()
        nudge_vector(original, {"processing": 0.5})
        assert original["processing"] == 0.0


class TestValidateVector:
    def test_rejects_unknown_keys(self):
        with pytest.raises(ValueError, match="Unknown FSLSM dimensions"):
            validate_vector({"bogus": 0.1})

    def test_clamps_out_of_range_values(self):
        assert validate_vector({"processing": 5.0})["processing"] == 1.0

    def test_fills_missing_dimensions_with_zero(self):
        assert validate_vector({"processing": 0.4})["perception"] == 0.0


class TestSignalsToDeltas:
    def test_known_signal_produces_its_delta(self):
        assert signals_to_deltas(["requested_diagram"])["reception"] == pytest.approx(-0.12)

    def test_unknown_signals_are_ignored(self):
        assert signals_to_deltas(["not_a_signal"]) == {d: 0.0 for d in DIMENSIONS}

    def test_repeated_signals_accumulate(self):
        once = signals_to_deltas(["requested_diagram"])["reception"]
        twice = signals_to_deltas(["requested_diagram"] * 2)["reception"]
        assert twice == pytest.approx(once * 2)

    def test_opposing_signals_cancel(self):
        """Visual and verbal evidence in the same batch should not both win."""
        deltas = signals_to_deltas(["requested_diagram", "requested_text_explanation"])
        assert deltas["reception"] == pytest.approx(-0.02)

    def test_compound_signal_touches_multiple_dimensions(self):
        deltas = signals_to_deltas(["quiz_got_logic_question"])
        assert deltas["processing"] == pytest.approx(0.06)
        assert deltas["understanding"] == pytest.approx(-0.06)


class TestLabels:
    @pytest.mark.parametrize(
        "value,expected",
        [(-0.65, "Visual (strong)"), (-0.30, "Visual (moderate)"),
         (0.0, "Balanced"), (0.30, "Verbal (moderate)"), (0.65, "Verbal (strong)")],
    )
    def test_reception_labels(self, value, expected):
        assert label_dimension("reception", value) == expected

    def test_threshold_is_exclusive(self):
        """At exactly the threshold the dimension is still Balanced."""
        assert label_dimension("reception", 0.25) == "Balanced"
        assert label_dimension("reception", -0.25) == "Balanced"

    def test_describe_vector_covers_every_dimension(self):
        assert set(describe_vector(default_vector())) == set(DIMENSIONS)
