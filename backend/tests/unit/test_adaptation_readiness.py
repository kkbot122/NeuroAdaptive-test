from app.modules.adaptation.readiness import READY_THRESHOLD, compute_readiness, is_ready


class TestMinOverHardPrerequisites:
    def test_min_not_mean_across_two_hard_prerequisites(self):
        readiness = compute_readiness(hard_prerequisite_masteries=[0.9, 0.3], soft_prerequisite_masteries=[])
        assert readiness == 0.3  # exact min -- an average would land near 0.6

    def test_no_prerequisites_is_fully_ready(self):
        assert compute_readiness([], []) == 1.0


class TestReadyThresholdBoundary:
    def test_below_threshold_is_not_ready(self):
        assert not is_ready(compute_readiness([0.59], []))

    def test_at_threshold_is_ready(self):
        assert is_ready(compute_readiness([0.60], []))

    def test_above_threshold_is_ready(self):
        assert is_ready(compute_readiness([0.61], []))

    def test_threshold_constant_matches_spec(self):
        assert READY_THRESHOLD == 0.6


class TestSoftPrerequisitesSoftenButDoNotBlock:
    def test_soft_only_at_low_mastery_still_passes(self):
        """A soft prerequisite at 0.1 mastery, alone, must not gate the way
        a hard one at 0.1 would."""
        soft_only = compute_readiness(hard_prerequisite_masteries=[], soft_prerequisite_masteries=[0.1])
        hard_equivalent = compute_readiness(hard_prerequisite_masteries=[0.1], soft_prerequisite_masteries=[])
        assert is_ready(soft_only)
        assert not is_ready(hard_equivalent)
        assert soft_only > hard_equivalent

    def test_soft_prerequisites_do_move_the_score_a_little(self):
        no_soft = compute_readiness(hard_prerequisite_masteries=[0.9], soft_prerequisite_masteries=[])
        with_weak_soft = compute_readiness(hard_prerequisite_masteries=[0.9], soft_prerequisite_masteries=[0.1])
        assert with_weak_soft < no_soft
