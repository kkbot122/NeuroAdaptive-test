"""
Phase 7: AdaptationOutcome linkage, the engagement-vs-pedagogical-effect
guardrail, and the adaptation-history analytics view.
"""
import uuid

import pytest

from app.modules.adaptation.models import (
    AdaptationDecision,
    AdaptationOutcome,
    OutcomeType,
    SignalCategory,
)
from app.modules.adaptation.outcome_service import (
    AdaptationOutcomeNotFound,
    AdaptationOutcomeService,
    InvalidOutcome,
)
from app.modules.courses.models import Course
from app.modules.curriculum.models import Concept, CourseVersion, CourseVersionStatus
from app.modules.mastery.models import MasteryEvent, Question, QuestionAttempt
from app.modules.mastery.service import MasteryService
from app.services.embedding.fake import FakeEmbeddingGateway
from app.services.generation.fake import FakeGenerationGateway


def make_service(db_session):
    return AdaptationOutcomeService(
        db_session, MasteryService(db_session, FakeGenerationGateway(), FakeEmbeddingGateway())
    )


@pytest.fixture()
def course_with_decision(db_session, owner):
    course = Course(owner_id=owner.id, title="OS Course")
    db_session.add(course)
    db_session.commit()

    version = CourseVersion(
        course_id=course.id, owner_id=owner.id, version_number=1, status=CourseVersionStatus.READY.value,
    )
    db_session.add(version)
    db_session.flush()
    concept = Concept(
        course_id=course.id, course_version_id=version.id, owner_id=owner.id,
        canonical_key="deadlock", name="Deadlock", definition="def", importance=0.8,
    )
    db_session.add(concept)
    db_session.commit()

    decision = AdaptationDecision(
        owner_id=owner.id, course_id=course.id,
        selected_activity_type="PREREQUISITE_REMEDIATION", selected_concept_id=concept.id,
        reason_text="Reviewing Deadlock because a recent check showed it's weak.",
        candidates_considered=[{"activity_type": "PREREQUISITE_REMEDIATION", "concept_ids": [str(concept.id)], "score": 0.9, "selected": True}],
        policy_version="adaptive-policy-v1",
        input_snapshot={"concept_mastery": {str(concept.id): 0.4}, "concept_uncertainty": {str(concept.id): 0.5}},
    )
    db_session.add(decision)
    course.active_version_id = version.id
    db_session.commit()

    return course, concept, decision, version


def make_question_attempt(db_session, owner, course, concept, correctness=1.0, hints_used=0, time_taken=10.0):
    question = Question(
        course_id=course.id, course_version_id=course.active_version_id, owner_id=owner.id,
        question_type="MCQ", prompt="?", options=["a", "b"], correct_answer="a",
        model_id="fake-generation-gateway", prompt_version="test-v1",
    )
    db_session.add(question)
    db_session.flush()
    attempt = QuestionAttempt(
        question_id=question.id, question_version=1, owner_id=owner.id, course_id=course.id,
        given_answer="a", correctness=correctness, hints_used=hints_used, time_taken_seconds=time_taken,
    )
    db_session.add(attempt)
    db_session.commit()
    return attempt


class TestDecisionLinkageRequiresAValidOwnedDecision:
    def test_nonexistent_decision_id_is_rejected(self, db_session, owner):
        service = make_service(db_session)
        with pytest.raises(AdaptationOutcomeNotFound):
            service.record_engagement(uuid.uuid4(), owner.id, "VIEWED")

    def test_another_users_decision_id_is_rejected(self, db_session, owner, other_user, course_with_decision):
        _, _, decision, _ = course_with_decision
        service = make_service(db_session)
        with pytest.raises(AdaptationOutcomeNotFound):
            service.record_engagement(decision.id, other_user.id, "VIEWED")


class TestMultipleOutcomesPerDecision:
    def test_a_decision_can_have_several_outcomes_over_time_all_linked(
        self, db_session, owner, course_with_decision
    ):
        course, concept, decision, version = course_with_decision
        service = make_service(db_session)
        service.record_engagement(decision.id, owner.id, "COMPLETED")
        attempt = make_question_attempt(db_session, owner, course, concept)
        service.record_assessment_outcome(decision.id, owner.id, attempt.id)

        rows = db_session.query(AdaptationOutcome).filter(AdaptationOutcome.decision_id == decision.id).all()
        assert len(rows) == 2
        assert all(r.decision_id == decision.id for r in rows)
        assert {r.outcome_type for r in rows} == {"COMPLETED", "ASSESSED"}


class TestNoOutcomeIsDistinctFromAbandoned:
    def test_a_decision_with_no_outcome_differs_from_one_marked_abandoned(
        self, db_session, owner, course_with_decision
    ):
        course, concept, decision_a, version = course_with_decision
        # A second, independent decision with no outcome at all.
        decision_b = AdaptationDecision(
            owner_id=owner.id, course_id=course.id,
            selected_activity_type="NEW_LESSON", selected_concept_id=concept.id,
            reason_text="r", candidates_considered=[], policy_version="v1",
            input_snapshot={"concept_mastery": {}, "concept_uncertainty": {}},
        )
        db_session.add(decision_b)
        db_session.commit()

        service = make_service(db_session)
        service.record_engagement(decision_a.id, owner.id, "ABANDONED")

        outcomes_a = db_session.query(AdaptationOutcome).filter(AdaptationOutcome.decision_id == decision_a.id).all()
        outcomes_b = db_session.query(AdaptationOutcome).filter(AdaptationOutcome.decision_id == decision_b.id).all()
        assert len(outcomes_a) == 1 and outcomes_a[0].outcome_type == "ABANDONED"
        assert len(outcomes_b) == 0  # genuinely no outcome, not a fabricated "abandoned"


class TestEngagementIsNotPedagogicalEvidence:
    def test_viewing_alone_is_never_reported_as_a_successful_outcome(
        self, db_session, owner, course_with_decision
    ):
        course, concept, decision, version = course_with_decision
        service = make_service(db_session)
        outcome = service.record_engagement(decision.id, owner.id, "COMPLETED")

        assert outcome.signal_category == SignalCategory.ENGAGEMENT.value
        assert outcome.mastery_delta is None
        assert outcome.transfer_success is None

    def test_engagement_and_pedagogical_fields_are_structurally_distinct(
        self, db_session, owner, course_with_decision
    ):
        course, concept, decision, version = course_with_decision
        service = make_service(db_session)
        service.record_engagement(decision.id, owner.id, "COMPLETED")
        attempt = make_question_attempt(db_session, owner, course, concept)
        service.record_assessment_outcome(decision.id, owner.id, attempt.id)

        engagement = db_session.query(AdaptationOutcome).filter(
            AdaptationOutcome.decision_id == decision.id, AdaptationOutcome.outcome_type == "COMPLETED"
        ).first()
        pedagogical = db_session.query(AdaptationOutcome).filter(
            AdaptationOutcome.decision_id == decision.id, AdaptationOutcome.outcome_type == "ASSESSED"
        ).first()
        assert engagement.signal_category == "ENGAGEMENT"
        assert pedagogical.signal_category == "PEDAGOGICAL_EFFECT"
        assert engagement.mastery_delta is None
        assert pedagogical.mastery_delta is not None

    def test_rejecting_an_invalid_engagement_type(self, db_session, owner, course_with_decision):
        _, _, decision, _ = course_with_decision
        service = make_service(db_session)
        with pytest.raises(InvalidOutcome):
            service.record_engagement(decision.id, owner.id, "ASSESSED")  # not an engagement type


class TestReproducibilityMetadata:
    def test_a_newly_created_question_has_model_id_and_prompt_version(
        self, db_session, owner, course_with_decision
    ):
        course, concept, decision, version = course_with_decision
        mastery_service = MasteryService(db_session, FakeGenerationGateway(), FakeEmbeddingGateway())
        question = mastery_service.create_question(
            course.id, version.id, owner.id, "MCQ", "?", concept_weights={concept.id: 1.0},
            options=["a", "b"], correct_answer="a",
        )
        assert question.model_id
        assert question.prompt_version

    def test_a_question_generated_from_a_decision_records_that_decision_id(
        self, db_session, owner, course_with_decision
    ):
        course, concept, decision, version = course_with_decision
        mastery_service = MasteryService(db_session, FakeGenerationGateway(), FakeEmbeddingGateway())
        question = mastery_service.create_question(
            course.id, version.id, owner.id, "MCQ", "?", concept_weights={concept.id: 1.0},
            options=["a", "b"], correct_answer="a", decision_id=decision.id,
        )
        assert question.decision_id == decision.id


class TestAssessmentOutcomeComputesMasteryDelta:
    def test_mastery_delta_against_the_correct_concept(self, db_session, owner, course_with_decision):
        course, concept, decision, version = course_with_decision
        # Snapshot mastery frozen in the decision was 0.4. Push real mastery
        # up with a strong evidence event, so the delta is genuinely positive.
        db_session.add(MasteryEvent(
            owner_id=owner.id, concept_id=concept.id, course_id=course.id, course_version_id=version.id,
            correctness=1.0, evidence_weight_base=50.0,
        ))
        db_session.commit()

        attempt = make_question_attempt(db_session, owner, course, concept)
        service = make_service(db_session)
        outcome = service.record_assessment_outcome(decision.id, owner.id, attempt.id)

        assert outcome.concept_id == concept.id
        assert outcome.mastery_delta is not None
        assert outcome.mastery_delta > 0  # mastery moved up from the 0.4 snapshot


class TestTransferQuestionAndDeltas:
    def test_transfer_success_and_hint_time_deltas(self, db_session, owner, course_with_decision):
        course, concept, decision, version = course_with_decision
        baseline = make_question_attempt(db_session, owner, course, concept, hints_used=3, time_taken=60.0)
        followup = make_question_attempt(db_session, owner, course, concept, hints_used=0, time_taken=20.0)

        service = make_service(db_session)
        outcome = service.record_assessment_outcome(
            decision.id, owner.id, followup.id,
            is_transfer_question=True, baseline_question_attempt_id=baseline.id,
        )
        assert outcome.outcome_type == "TRANSFER_SUCCESS"
        assert bool(outcome.transfer_success) is True  # stored bool-as-int, matches Question.is_diagnostic's precedent
        assert outcome.hint_usage_delta == -3
        assert outcome.time_to_correct_delta == -40.0


class TestHelpfulnessFeedback:
    def test_helpfulness_feedback_is_self_reported_not_pedagogical(
        self, db_session, owner, course_with_decision
    ):
        _, _, decision, _ = course_with_decision
        service = make_service(db_session)
        outcome = service.record_helpfulness_feedback(decision.id, owner.id, 1)
        assert outcome.signal_category == SignalCategory.SELF_REPORTED.value
        assert outcome.mastery_delta is None


class TestAdaptationHistoryOwnership:
    def test_another_users_course_is_not_found(self, db_session, owner, other_user, course_with_decision):
        course, _, _, _ = course_with_decision
        service = make_service(db_session)
        with pytest.raises(AdaptationOutcomeNotFound):
            service.get_adaptation_history(course.id, other_user.id)

    def test_history_includes_reason_and_linked_outcomes_chronologically(
        self, db_session, owner, course_with_decision
    ):
        course, concept, decision, version = course_with_decision
        service = make_service(db_session)
        service.record_engagement(decision.id, owner.id, "COMPLETED")
        attempt = make_question_attempt(db_session, owner, course, concept)
        service.record_assessment_outcome(decision.id, owner.id, attempt.id)

        history = service.get_adaptation_history(course.id, owner.id)
        assert len(history) == 1
        entry = history[0]
        assert entry["decision_id"] == str(decision.id)
        assert entry["reason"] == decision.reason_text
        assert entry["recommended"]["activity_type"] == "PREREQUISITE_REMEDIATION"
        assert len(entry["outcomes"]) == 2
        assert entry["outcomes"][0]["outcome_type"] == "COMPLETED"
        assert entry["outcomes"][1]["outcome_type"] == "ASSESSED"
        assert entry["outcomes"][1]["mastery_delta"] is not None
