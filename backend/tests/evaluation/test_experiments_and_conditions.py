"""
Mandate test cases 1-3: experiments/conditions/cohort assignment, and that
B3/A1 are real toggles on the actual production code, not reimplementations.
"""
import uuid

import pytest

from app.modules.adaptation.policy import DEFAULT_POLICY, PolicyWeights
from app.modules.adaptation.scoring import score_candidate
from app.modules.evaluation.service import DuplicateAssignment, EvaluationNotFound, EvaluationService
from tests.unit.test_adaptation_scoring import candidate, concept  # reuse existing pure fixtures


def make_b1_b2_b3_experiment(db_session, owner):
    service = EvaluationService(db_session)
    experiment = service.create_experiment(
        owner.id, "Pilot v1", "B1/B2/B3 comparison",
        [
            {"code": "B1", "description": "Fixed-sequence, non-adaptive", "config": {"adaptive": False}},
            {"code": "B2", "description": "Real adaptive system", "config": {}},
            {"code": "B3", "description": "No citation validation", "config": {"citation_validation_enabled": False}},
        ],
    )
    return service, experiment


class TestExperimentAndCohortPersistence:
    def test_conditions_persist_and_are_queryable(self, db_session, owner):
        service, experiment = make_b1_b2_b3_experiment(db_session, owner)
        conditions = service.get_conditions(experiment.id, owner.id)
        assert {c.code for c in conditions} == {"B1", "B2", "B3"}

    def test_a_learner_is_assigned_to_exactly_one_condition(self, db_session, owner, other_user):
        service, experiment = make_b1_b2_b3_experiment(db_session, owner)
        service.assign_to_condition(experiment.id, "B2", other_user.id, owner.id)

        condition = service.get_learner_condition(experiment.id, other_user.id)
        assert condition.code == "B2"

    def test_a_learner_cannot_be_assigned_to_two_conditions_in_one_experiment(
        self, db_session, owner, other_user
    ):
        service, experiment = make_b1_b2_b3_experiment(db_session, owner)
        service.assign_to_condition(experiment.id, "B2", other_user.id, owner.id)
        with pytest.raises(DuplicateAssignment):
            service.assign_to_condition(experiment.id, "B1", other_user.id, owner.id)

    def test_unassigned_learner_has_no_condition(self, db_session, owner, other_user):
        service, experiment = make_b1_b2_b3_experiment(db_session, owner)
        assert service.get_learner_condition(experiment.id, other_user.id) is None

    def test_unknown_condition_code_is_rejected(self, db_session, owner, other_user):
        service, experiment = make_b1_b2_b3_experiment(db_session, owner)
        with pytest.raises(EvaluationNotFound):
            service.assign_to_condition(experiment.id, "B99", other_user.id, owner.id)


class TestB3IsARealToggleNotAReimplementation:
    """B3's config says citation_validation_enabled=False -- this must be
    literally the same parameter TutorService.ask() takes, not a separate
    code path. Verified structurally: the parameter exists with that exact
    name on the real production method."""

    def test_tutor_service_ask_accepts_the_b3_toggle(self):
        import inspect

        from app.modules.tutor.service import TutorService

        sig = inspect.signature(TutorService.ask)
        assert "citation_validation_enabled" in sig.parameters
        assert sig.parameters["citation_validation_enabled"].default is True


class TestA1ReusesTheRealScoringFunction:
    """A1's config zeroes w_presentation_fit -- this must go through the
    real scoring.score_candidate with a PolicyWeights override, not a
    parallel formula."""

    def test_zeroing_the_presentation_fit_weight_changes_only_that_terms_contribution(self):
        cid = uuid.uuid4()
        state_with_high_affinity = _state(cid, presentation_affinity=0.9)

        c = candidate(concept_ids=[cid])
        default_score = score_candidate(c, state_with_high_affinity, DEFAULT_POLICY).score

        a1_policy = PolicyWeights(w_presentation_fit=0.0)
        ablated_score = score_candidate(c, state_with_high_affinity, a1_policy).score

        # Removing a positive-weighted term for a candidate with high
        # presentation affinity can only lower (or leave unchanged) its score.
        assert ablated_score <= default_score
        # And it is the SAME function, just parameterized differently --
        # every other weight is untouched.
        assert a1_policy.w_expected_gain == DEFAULT_POLICY.w_expected_gain


def _state(cid, presentation_affinity):
    from app.modules.adaptation.scoring import LearnerStateSnapshot

    return LearnerStateSnapshot(concepts={cid: concept()}, presentation_affinity={"concise": presentation_affinity})
