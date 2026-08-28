"""
Phase 7 API contract: GET /courses/{id}/adaptation-history and the
outcome-recording endpoints, exercised through the real HTTP layer.
"""
import uuid

from app.modules.courses.models import Course
from app.modules.curriculum.models import Concept, CourseVersion, CourseVersionStatus
from app.modules.mastery.models import Question, QuestionAttempt
from tests.conftest import auth_headers


def make_course_with_decision(client, db_session, owner):
    from app.modules.adaptation.models import AdaptationDecision

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
    course.active_version_id = version.id
    db_session.commit()

    decision = AdaptationDecision(
        owner_id=owner.id, course_id=course.id,
        selected_activity_type="TARGETED_PRACTICE", selected_concept_id=concept.id,
        reason_text="Extra practice on Deadlock to strengthen a developing area.",
        candidates_considered=[{"activity_type": "TARGETED_PRACTICE", "concept_ids": [str(concept.id)], "score": 0.7, "selected": True}],
        policy_version="adaptive-policy-v1",
        input_snapshot={"concept_mastery": {str(concept.id): 0.5}, "concept_uncertainty": {str(concept.id): 0.4}},
    )
    db_session.add(decision)
    db_session.commit()
    return course, concept, decision, version


class TestAdaptationHistoryOwnership:
    def test_another_users_course_is_404(self, client, owner, other_user, db_session):
        course, *_ = make_course_with_decision(client, db_session, owner)
        resp = client.get(
            f"/api/v1/courses/{course.id}/adaptation-history", headers=auth_headers(other_user.email)
        )
        assert resp.status_code == 404

    def test_unknown_course_is_404(self, client, owner):
        resp = client.get(
            f"/api/v1/courses/{uuid.uuid4()}/adaptation-history", headers=auth_headers(owner.email)
        )
        assert resp.status_code == 404


class TestAdaptationHistoryShape:
    def test_returns_chronological_entries_with_reason_and_outcomes(self, client, owner, db_session):
        course, concept, decision, version = make_course_with_decision(client, db_session, owner)

        engagement = client.post(
            f"/api/v1/courses/{course.id}/adaptation-decisions/{decision.id}/outcomes/engagement",
            json={"outcome_type": "COMPLETED"},
            headers=auth_headers(owner.email),
        )
        assert engagement.status_code == 200

        question = Question(
            course_id=course.id, course_version_id=version.id, owner_id=owner.id,
            question_type="MCQ", prompt="?", options=["a", "b"], correct_answer="a",
            model_id="fake", prompt_version="v1",
        )
        db_session.add(question)
        db_session.flush()
        attempt = QuestionAttempt(
            question_id=question.id, question_version=1, owner_id=owner.id, course_id=course.id,
            given_answer="a", correctness=1.0,
        )
        db_session.add(attempt)
        db_session.commit()

        assessed = client.post(
            f"/api/v1/courses/{course.id}/adaptation-decisions/{decision.id}/outcomes/assessment",
            json={"question_attempt_id": str(attempt.id)},
            headers=auth_headers(owner.email),
        )
        assert assessed.status_code == 200
        assert assessed.json()["outcome_type"] == "ASSESSED"

        history_resp = client.get(
            f"/api/v1/courses/{course.id}/adaptation-history", headers=auth_headers(owner.email)
        )
        assert history_resp.status_code == 200
        history = history_resp.json()
        assert len(history) == 1
        entry = history[0]
        assert entry["decision_id"] == str(decision.id)
        assert entry["reason"] == decision.reason_text
        assert "input_snapshot" in entry
        assert len(entry["outcomes"]) == 2
        assert entry["outcomes"][0]["outcome_type"] == "COMPLETED"
        assert entry["outcomes"][0]["signal_category"] == "ENGAGEMENT"
        assert entry["outcomes"][1]["outcome_type"] == "ASSESSED"
        assert entry["outcomes"][1]["signal_category"] == "PEDAGOGICAL_EFFECT"

    def test_helpfulness_feedback_endpoint(self, client, owner, db_session):
        course, concept, decision, version = make_course_with_decision(client, db_session, owner)
        resp = client.post(
            f"/api/v1/courses/{course.id}/adaptation-decisions/{decision.id}/outcomes/helpfulness",
            json={"rating": 1},
            headers=auth_headers(owner.email),
        )
        assert resp.status_code == 200
        assert resp.json()["helpfulness_rating"] == 1

    def test_outcome_for_unknown_decision_is_404(self, client, owner, db_session):
        course, *_ = make_course_with_decision(client, db_session, owner)
        resp = client.post(
            f"/api/v1/courses/{course.id}/adaptation-decisions/{uuid.uuid4()}/outcomes/engagement",
            json={"outcome_type": "VIEWED"},
            headers=auth_headers(owner.email),
        )
        assert resp.status_code == 404


class TestFullEndToEndLoop:
    def test_recommendation_to_completion_to_assessment_to_mastery_delta_to_history(
        self, client, owner, db_session
    ):
        """
        Full loop, mandate test 11: a REAL recommendation is served through
        Phase 4's actual GET .../next-activity (not a hand-built
        AdaptationDecision row), the learner completes it, takes a
        follow-up assessment on the same concept, a mastery delta is
        computed and linked, and the history view shows the whole story.
        """
        from app.modules.mastery.models import MasteryEvent

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
        db_session.flush()

        from app.modules.curriculum.models import Lesson, LessonConcept, Module

        module = Module(course_version_id=version.id, position=0, title="Concurrency")
        db_session.add(module)
        db_session.flush()
        lesson = Lesson(module_id=module.id, position=0, title="Deadlocks", objective="Understand it.")
        db_session.add(lesson)
        db_session.flush()
        db_session.add(LessonConcept(lesson_id=lesson.id, concept_id=concept.id))
        db_session.commit()
        course.active_version_id = version.id
        db_session.commit()

        # 1. A real recommendation is served (Phase 4) -- with no mastery
        # evidence yet, the only eligible candidate is NEW_LESSON, which is
        # fine: what matters here is the decision -> outcome chain, not
        # which candidate wins.
        rec_resp = client.get(f"/api/v1/courses/{course.id}/next-activity", headers=auth_headers(owner.email))
        assert rec_resp.status_code == 200
        decision_id = rec_resp.json()["decision_id"]

        # 2. The learner completes the recommended activity.
        complete_resp = client.post(
            f"/api/v1/courses/{course.id}/adaptation-decisions/{decision_id}/outcomes/engagement",
            json={"outcome_type": "COMPLETED"},
            headers=auth_headers(owner.email),
        )
        assert complete_resp.status_code == 200

        # New evidence appears (a follow-up assessment moves real mastery).
        db_session.add(MasteryEvent(
            owner_id=owner.id, concept_id=concept.id, course_id=course.id, course_version_id=version.id,
            correctness=1.0, evidence_weight_base=50.0,
        ))
        db_session.commit()
        question = Question(
            course_id=course.id, course_version_id=version.id, owner_id=owner.id,
            question_type="MCQ", prompt="?", options=["a", "b"], correct_answer="a",
            model_id="fake", prompt_version="v1",
        )
        db_session.add(question)
        db_session.flush()
        attempt = QuestionAttempt(
            question_id=question.id, question_version=1, owner_id=owner.id, course_id=course.id,
            given_answer="a", correctness=1.0,
        )
        db_session.add(attempt)
        db_session.commit()

        # 3. The follow-up assessment outcome is recorded and linked.
        assess_resp = client.post(
            f"/api/v1/courses/{course.id}/adaptation-decisions/{decision_id}/outcomes/assessment",
            json={"question_attempt_id": str(attempt.id)},
            headers=auth_headers(owner.email),
        )
        assert assess_resp.status_code == 200
        assert assess_resp.json()["outcome_type"] == "ASSESSED"

        # 4. The history view shows the complete decision -> outcome story.
        history = client.get(
            f"/api/v1/courses/{course.id}/adaptation-history", headers=auth_headers(owner.email)
        ).json()
        assert len(history) == 1
        entry = history[0]
        assert entry["decision_id"] == decision_id
        assert entry["reason"]
        outcome_types = [o["outcome_type"] for o in entry["outcomes"]]
        assert "COMPLETED" in outcome_types
        assert "ASSESSED" in outcome_types
        assessed_entry = next(o for o in entry["outcomes"] if o["outcome_type"] == "ASSESSED")
        assert assessed_entry["mastery_delta"] is not None
        assert assessed_entry["signal_category"] == "PEDAGOGICAL_EFFECT"
