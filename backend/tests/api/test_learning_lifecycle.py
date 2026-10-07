import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.modules.adaptation.models import AdaptationDecision
from app.modules.auth.models import User
from app.modules.courses.models import Course, CourseStatus
from app.modules.curriculum.models import (
    Concept,
    ConceptPrerequisite,
    CourseVersion,
    CourseVersionStatus,
    EdgeStrength,
    Lesson,
    LessonConcept,
    Module,
)
from app.modules.learning.models import (
    AnswerSubmission,
    AssessmentQuestion,
    AssessmentSession,
    LearningActivity,
)
from app.modules.learning.router import _service as learning_service_dependency
from app.modules.learning.service import LearningService
from app.modules.mastery.models import MasteryEvent, Question, QuestionAttempt, QuestionConcept
from app.services.embedding.fake import FakeEmbeddingGateway
from app.services.generation.fake import FakeGenerationGateway
from app.services.generation.gateway import GenerationError
from tests.conftest import auth_headers


@pytest.fixture()
def published_course_with_lessons(db_session, owner):
    course = Course(owner_id=owner.id, title="Lifecycle course", status=CourseStatus.PUBLISHED.value)
    db_session.add(course)
    db_session.flush()
    version = CourseVersion(
        course_id=course.id,
        owner_id=owner.id,
        version_number=1,
        status=CourseVersionStatus.READY.value,
    )
    db_session.add(version)
    db_session.flush()
    concept_a = Concept(
        course_id=course.id,
        course_version_id=version.id,
        owner_id=owner.id,
        canonical_key="concept-a",
        name="Concept A",
        definition="A deterministic test concept.",
        importance=0.9,
    )
    concept_b = Concept(
        course_id=course.id,
        course_version_id=version.id,
        owner_id=owner.id,
        canonical_key="concept-b",
        name="Concept B",
        definition="Another deterministic test concept.",
        importance=0.5,
    )
    db_session.add_all([concept_a, concept_b])
    db_session.flush()
    module = Module(course_version_id=version.id, position=0, title="Module")
    db_session.add(module)
    db_session.flush()
    lesson = Lesson(module_id=module.id, position=0, title="Lesson", objective="Test objective")
    db_session.add(lesson)
    db_session.flush()
    db_session.add_all(
        [
            LessonConcept(lesson_id=lesson.id, concept_id=concept_a.id, weight=0.7),
            LessonConcept(lesson_id=lesson.id, concept_id=concept_b.id, weight=0.3),
        ]
    )
    course.active_version_id = version.id
    db_session.commit()
    return course, version, concept_a, concept_b, lesson


def configure_diagnostic(fake_generation):
    fake_generation.when_prompt_contains(
        "Concept A",
        json.dumps(
            {
                "questions": [
                    {
                        "concept_name": "Concept A",
                        "prompt": "Question A?",
                        "options": ["A", "not A"],
                        "correct_answer": "A",
                        "difficulty": 0.3,
                    },
                    {
                        "concept_name": "Concept B",
                        "prompt": "Question B?",
                        "options": ["B", "not B"],
                        "correct_answer": "B",
                        "difficulty": 0.4,
                    },
                ]
            }
        ),
    )


def start_diagnostic(client, course, owner, max_questions=2):
    return client.post(
        f"/api/v1/courses/{course.id}/diagnostic",
        json={"max_questions": max_questions},
        headers=auth_headers(owner.email),
    )


class TestDiagnosticResumeAndFeedback:
    def test_continue_resumes_saved_diagnostic_as_supported_work(
        self, client, owner, db_session, fake_generation, published_course_with_lessons
    ):
        configure_diagnostic(fake_generation)
        course, *_ = published_course_with_lessons
        session = start_diagnostic(client, course, owner).json()

        continued = client.post(
            f"/api/v1/courses/{course.id}/activities/next",
            headers=auth_headers(owner.email),
        )

        assert continued.status_code == 200
        assert continued.json()["activity_type"] == "DIAGNOSTIC"
        assert continued.json()["experience_availability"] == "SUPPORTED"
        assert continued.json()["assessment_session_id"] == session["id"]

    def test_refresh_returns_the_same_order_and_historical_question_version(
        self, client, owner, db_session, fake_generation, published_course_with_lessons
    ):
        configure_diagnostic(fake_generation)
        course, _, _, _, _ = published_course_with_lessons

        first = start_diagnostic(client, course, owner)
        assert first.status_code == 200
        original = first.json()
        assert [q["prompt"] for q in original["questions"]] == ["Question A?", "Question B?"]
        assert [q["position"] for q in original["questions"]] == [0, 1]
        assert [q["question_version"] for q in original["questions"]] == [1, 1]

        original_question_id = uuid.UUID(original["questions"][0]["question_id"])
        from app.modules.mastery.service import MasteryService

        MasteryService(db_session, fake_generation, FakeEmbeddingGateway()).supersede_question(
            original_question_id, owner.id, prompt="Replacement question?"
        )

        resumed = client.get(
            f"/api/v1/courses/{course.id}/assessment-sessions/{original['id']}",
            headers=auth_headers(owner.email),
        )
        repeated_start = start_diagnostic(client, course, owner)
        assert resumed.status_code == 200
        assert repeated_start.status_code == 200
        assert repeated_start.json()["id"] == original["id"]
        assert [q["question_id"] for q in resumed.json()["questions"]] == [
            q["question_id"] for q in original["questions"]
        ]
        assert [q["question_version"] for q in resumed.json()["questions"]] == [1, 1]
        assert resumed.json()["questions"][0]["prompt"] == "Question A?"
        assert len(fake_generation.calls) == 1

    def test_activity_assessment_uses_server_owned_questions_and_keeps_its_fixed_set(
        self, client, owner, db_session, fake_generation, published_course_with_lessons
    ):
        course, version, concept_a, _, lesson = published_course_with_lessons
        activity = LearningActivity(
            owner_id=owner.id,
            course_id=course.id,
            course_version_id=version.id,
            activity_type="NEW_LESSON",
            target_concept_ids=[str(concept_a.id)],
            lesson_id=lesson.id,
            status="AWAITING_ASSESSMENT",
            presentation_format="detailed",
            reading_completed_at=datetime.now(timezone.utc),
        )
        db_session.add(activity)
        db_session.flush()
        questions = []
        for index in range(2):
            question = Question(
                course_id=course.id,
                course_version_id=version.id,
                owner_id=owner.id,
                question_type="MCQ",
                prompt=f"Saved question {index + 1}?",
                options=["yes", "no"],
                correct_answer="yes",
                rubric=None,
                difficulty=0.4,
                is_diagnostic=0,
                version=1,
                model_id="fixture",
                prompt_version="test-v1",
            )
            db_session.add(question)
            db_session.flush()
            db_session.add(QuestionConcept(question_id=question.id, concept_id=concept_a.id, weight=1.0))
            questions.append(question)
        db_session.commit()
        expected_prompts = {str(question.id): question.prompt for question in questions}

        response = client.post(
            f"/api/v1/courses/{course.id}/activities/{activity.id}/assessment",
            headers=auth_headers(owner.email),
        )
        assert response.status_code == 200
        initial = response.json()
        assert len(initial["questions"]) == 2
        assert [item["question_version"] for item in initial["questions"]] == [1, 1]

        from app.modules.mastery.service import MasteryService

        MasteryService(db_session, fake_generation, FakeEmbeddingGateway()).supersede_question(
            uuid.UUID(initial["questions"][0]["question_id"]), owner.id, prompt="New version?"
        )
        same_activity = client.post(
            f"/api/v1/courses/{course.id}/activities/{activity.id}/assessment",
            headers=auth_headers(owner.email),
        )
        by_session = client.get(
            f"/api/v1/courses/{course.id}/assessment-sessions/{initial['id']}",
            headers=auth_headers(owner.email),
        )
        expected_ids = [item["question_id"] for item in initial["questions"]]
        assert [item["question_id"] for item in same_activity.json()["questions"]] == expected_ids
        assert [item["question_id"] for item in by_session.json()["questions"]] == expected_ids
        assert [item["question_version"] for item in by_session.json()["questions"]] == [1, 1]
        assert by_session.json()["questions"][0]["prompt"] == expected_prompts[expected_ids[0]]

    def test_duplicate_answer_is_idempotent_conflict_is_rejected_and_feedback_waits_for_set_submit(
        self, client, owner, db_session, fake_generation, published_course_with_lessons
    ):
        configure_diagnostic(fake_generation)
        course, _, _, _, _ = published_course_with_lessons
        session = start_diagnostic(client, course, owner).json()
        q_a, q_b = session["questions"]
        answer_url = (
            f"/api/v1/courses/{course.id}/assessment-sessions/{session['id']}"
            f"/questions/{q_a['question_id']}/answer"
        )
        body = {"given_answer": "A"}

        first = client.post(answer_url, json=body, headers=auth_headers(owner.email))
        retry = client.post(answer_url, json=body, headers=auth_headers(owner.email))
        assert first.status_code == retry.status_code == 200
        assert all("result" not in question for question in first.json()["questions"])
        assert "concept_progress" not in first.json()
        assert "expected_answer" not in json.dumps(first.json())
        assert db_session.query(AnswerSubmission).count() == 1
        assert db_session.query(QuestionAttempt).filter(QuestionAttempt.assessment_question_id.isnot(None)).count() == 1
        assert db_session.query(MasteryEvent).filter(MasteryEvent.question_attempt_id.isnot(None)).count() == 1
        concept_id = db_session.query(QuestionConcept.concept_id).filter(
            QuestionConcept.question_id == uuid.UUID(q_a["question_id"])
        ).scalar()
        progress_before_submit = client.get(
            f"/api/v1/courses/{course.id}/learning-state",
            headers=auth_headers(owner.email),
        )
        mastery_before_submit = client.get(
            f"/api/v1/courses/{course.id}/mastery-report",
            headers=auth_headers(owner.email),
        )
        assert progress_before_submit.status_code == mastery_before_submit.status_code == 200
        assert next(
            row for row in progress_before_submit.json()["concept_understanding"]
            if row["concept_id"] == str(concept_id)
        )["band"] == "Not assessed"
        assert next(
            row for row in mastery_before_submit.json()
            if row["concept_id"] == str(concept_id)
        )["band"] == "Not assessed"

        conflict = client.post(
            answer_url,
            json={"given_answer": "not A"},
            headers=auth_headers(owner.email),
        )
        assert conflict.status_code == 409
        assert db_session.query(AnswerSubmission).one().given_answer == "A"

        session_url = f"/api/v1/courses/{course.id}/assessment-sessions/{session['id']}"
        incomplete = client.post(f"{session_url}/submit", headers=auth_headers(owner.email))
        assert incomplete.status_code == 409
        second = client.post(
            f"{session_url}/questions/{q_b['question_id']}/answer",
            json={"given_answer": "B"},
            headers=auth_headers(owner.email),
        )
        assert second.status_code == 200
        assert all("result" not in question for question in second.json()["questions"])
        assert "concept_progress" not in second.json()
        assessed_concept_ids = {
            str(row[0])
            for row in db_session.query(QuestionConcept.concept_id)
            .filter(QuestionConcept.question_id.in_([uuid.UUID(q_a["question_id"]), uuid.UUID(q_b["question_id"])]))
            .all()
        }
        open_progress = client.get(
            f"/api/v1/courses/{course.id}/learning-state",
            headers=auth_headers(owner.email),
        ).json()
        open_understanding = {
            row["concept_id"]: row["band"] for row in open_progress["concept_understanding"]
        }
        assert all(open_understanding[concept] == "Not assessed" for concept in assessed_concept_ids)

        submitted = client.post(f"{session_url}/submit", headers=auth_headers(owner.email))
        assert submitted.status_code == 200
        submitted_body = submitted.json()
        assert [q["result"]["correctness"] for q in submitted_body["questions"]] == [1.0, 1.0]
        assert submitted_body["graded_answer_count"] == 2
        assert submitted_body["unresolved_answer_count"] == 0
        assert submitted_body["concept_progress_reference_at"] == submitted_body["submitted_at"]
        progress_by_id = {row["concept_id"]: row for row in submitted_body["concept_progress"]}
        assert set(progress_by_id) == assessed_concept_ids
        assert progress_by_id[str(concept_id)]["before_band"] == "Not assessed"
        assert progress_by_id[str(concept_id)]["after_band"] == "Developing"
        assert progress_by_id[str(concept_id)]["before_evidence_strength"] == "Not assessed"
        assert progress_by_id[str(concept_id)]["after_evidence_strength"] == "Limited evidence"
        refreshed_results = client.get(session_url, headers=auth_headers(owner.email)).json()
        assert refreshed_results["concept_progress"] == submitted_body["concept_progress"]
        assert refreshed_results["concept_progress_reference_at"] == submitted_body["submitted_at"]
        decisions_before_refresh = db_session.query(AdaptationDecision).count()
        generation_calls_before_refresh = len(fake_generation.calls)
        refreshed_again = client.get(session_url, headers=auth_headers(owner.email))
        assert refreshed_again.status_code == 200
        assert db_session.query(AdaptationDecision).count() == decisions_before_refresh
        assert len(fake_generation.calls) == generation_calls_before_refresh
        mastery_after_submit = client.get(
            f"/api/v1/courses/{course.id}/mastery-report",
            headers=auth_headers(owner.email),
        )
        assert next(
            row for row in mastery_after_submit.json()
            if row["concept_id"] == str(concept_id)
        )["band"] != "Not assessed"
        duplicate_after_submit = client.post(answer_url, json=body, headers=auth_headers(owner.email))
        assert duplicate_after_submit.status_code == 200
        assert db_session.query(AnswerSubmission).count() == 2
        assert db_session.query(QuestionAttempt).filter(QuestionAttempt.assessment_question_id.isnot(None)).count() == 2

    def test_resume_and_retry_reconcile_activity_after_final_grade_commit_gap(
        self, client, owner, db_session, fake_generation, published_course_with_lessons
    ):
        configure_diagnostic(fake_generation)
        course, *_ = published_course_with_lessons
        session = start_diagnostic(client, course, owner).json()
        headers = auth_headers(owner.email)
        session_url = f"/api/v1/courses/{course.id}/assessment-sessions/{session['id']}"

        for question, answer in zip(session["questions"], ("A", "B"), strict=True):
            response = client.post(
                f"{session_url}/questions/{question['question_id']}/answer",
                json={"given_answer": answer},
                headers=headers,
            )
            assert response.status_code == 200

        submitted = client.post(f"{session_url}/submit", headers=headers)
        assert submitted.status_code == 200
        activity = db_session.query(LearningActivity).one()
        activity.status = "AWAITING_GRADING"  # Persisted outcome of submit/grading interleaving.
        db_session.commit()

        resumed_activity = client.post(
            f"/api/v1/courses/{course.id}/activities/next",
            headers=headers,
        )
        assert resumed_activity.status_code == 200
        assert resumed_activity.json()["status"] == "COMPLETED"
        assert resumed_activity.json()["assessment_session_id"] == session["id"]

        activity.status = "AWAITING_GRADING"  # Simulate a process stop before read-side repair.
        db_session.commit()
        resumed_session = client.get(session_url, headers=headers)
        assert resumed_session.status_code == 200
        assert resumed_session.json()["grading_state"] == "COMPLETE"
        db_session.refresh(activity)
        assert activity.status == "COMPLETED"
        progress = client.get(
            f"/api/v1/courses/{course.id}/learning-state",
            headers=headers,
        )
        assert progress.status_code == 200
        assert "active_activity" not in progress.json()

        activity.status = "AWAITING_GRADING"
        db_session.commit()
        retry = client.post(
            f"{session_url}/questions/{session['questions'][-1]['question_id']}/retry-grading",
            headers=headers,
        )

        assert retry.status_code == 200
        db_session.refresh(activity)
        assert activity.status == "COMPLETED"


class TestActivityCoverageAndOwnership:
    def test_lesson_assessment_requires_server_recorded_reading_completion(
        self, client, owner, db_session, published_course_with_lessons
    ):
        course, version, concept_a, _, lesson = published_course_with_lessons
        activity = LearningActivity(
            owner_id=owner.id,
            course_id=course.id,
            course_version_id=version.id,
            activity_type="NEW_LESSON",
            target_concept_ids=[str(concept_a.id)],
            lesson_id=lesson.id,
            status="READY",
        )
        db_session.add(activity)
        db_session.commit()

        response = client.post(
            f"/api/v1/courses/{course.id}/activities/{activity.id}/assessment",
            headers=auth_headers(owner.email),
        )
        assert response.status_code == 409
        assert db_session.query(AssessmentSession).count() == 0

    def test_reading_completion_changes_coverage_but_not_understanding(
        self, client, owner, db_session, published_course_with_lessons
    ):
        course, version, concept_a, _, lesson = published_course_with_lessons
        activity = LearningActivity(
            owner_id=owner.id,
            course_id=course.id,
            course_version_id=version.id,
            activity_type="NEW_LESSON",
            target_concept_ids=[str(concept_a.id)],
            lesson_id=lesson.id,
            status="READY",
            presentation_format="detailed",
        )
        db_session.add(activity)
        db_session.commit()
        headers = auth_headers(owner.email)
        url = f"/api/v1/courses/{course.id}/activities/{activity.id}"

        saved = client.patch(
            url,
            json={"reading_position": 184, "presentation_format": "concise"},
            headers=headers,
        )
        assert saved.status_code == 200
        resumed = client.get(url, headers=headers)
        assert resumed.json()["reading_position"] == 184
        assert resumed.json()["presentation_format"] == "concise"

        completed = client.post(f"{url}/reading-complete", headers=headers)
        assert completed.status_code == 200
        assert completed.json()["status"] == "AWAITING_ASSESSMENT"
        state = client.get(f"/api/v1/courses/{course.id}/learning-state", headers=headers).json()
        assert state["lesson_coverage"]["lessons_covered"] == 1
        assert state["lesson_coverage"]["covered_lesson_ids"] == [str(lesson.id)]
        understanding = next(row for row in state["concept_understanding"] if row["concept_id"] == str(concept_a.id))
        assert understanding["band"] == "Not assessed"
        assert understanding["evidence_strength"] == "Not assessed"
        assert db_session.query(MasteryEvent).count() == 0

    def test_question_only_activity_is_supported_but_waits_for_prepared_questions(
        self, client, owner, db_session, published_course_with_lessons
    ):
        course, version, concept_a, _, _lesson = published_course_with_lessons
        activity = LearningActivity(
            owner_id=owner.id,
            course_id=course.id,
            course_version_id=version.id,
            activity_type="TARGETED_PRACTICE",
            target_concept_ids=[str(concept_a.id)],
            lesson_id=None,
            status="READY",
            presentation_format="concise",
        )
        db_session.add(activity)
        db_session.commit()
        headers = auth_headers(owner.email)

        saved = client.get(
            f"/api/v1/courses/{course.id}/activities/{activity.id}", headers=headers
        )
        assert saved.status_code == 200
        assert saved.json()["experience_availability"] == "SUPPORTED"
        assert saved.json()["question_count"] == 5
        assert saved.json().get("lesson_id") is None
        assessment = client.post(
            f"/api/v1/courses/{course.id}/activities/{activity.id}/assessment", headers=headers
        )
        assert assessment.status_code == 409
        assert "saved questions" in assessment.json()["detail"].lower()
        assert db_session.query(AssessmentSession).count() == 0

    def test_foreign_course_session_and_question_relationships_return_404(
        self, client, owner, other_user, db_session, fake_generation, published_course_with_lessons
    ):
        configure_diagnostic(fake_generation)
        course, *_ = published_course_with_lessons
        session = start_diagnostic(client, course, owner).json()
        other_headers = auth_headers(other_user.email)
        activity = db_session.query(LearningActivity).filter(LearningActivity.owner_id == owner.id).one()

        foreign_owner = client.get(
            f"/api/v1/courses/{course.id}/assessment-sessions/{session['id']}",
            headers=other_headers,
        )
        foreign_diagnostic = start_diagnostic(client, course, other_user)
        foreign_course = client.get(
            f"/api/v1/courses/{uuid.uuid4()}/assessment-sessions/{session['id']}",
            headers=auth_headers(owner.email),
        )
        foreign_question = client.post(
            f"/api/v1/courses/{course.id}/assessment-sessions/{session['id']}/questions/{uuid.uuid4()}/answer",
            json={"given_answer": "A"},
            headers=auth_headers(owner.email),
        )
        foreign_question_answer = client.post(
            f"/api/v1/courses/{course.id}/assessment-sessions/{session['id']}"
            f"/questions/{session['questions'][0]['question_id']}/answer",
            json={"given_answer": "A"},
            headers=other_headers,
        )
        foreign_learning_state = client.get(
            f"/api/v1/courses/{course.id}/learning-state",
            headers=other_headers,
        )
        foreign_activity_selection = client.post(
            f"/api/v1/courses/{course.id}/activities/next",
            headers=other_headers,
        )
        foreign_submit = client.post(
            f"/api/v1/courses/{course.id}/assessment-sessions/{session['id']}/submit",
            headers=other_headers,
        )
        foreign_retry_grading = client.post(
            f"/api/v1/courses/{course.id}/assessment-sessions/{session['id']}"
            f"/questions/{session['questions'][0]['question_id']}/retry-grading",
            headers=other_headers,
        )
        foreign_activity = client.get(
            f"/api/v1/courses/{course.id}/activities/{activity.id}",
            headers=other_headers,
        )
        foreign_progress = client.patch(
            f"/api/v1/courses/{course.id}/activities/{activity.id}",
            json={"reading_position": 20},
            headers=other_headers,
        )
        foreign_reading = client.post(
            f"/api/v1/courses/{course.id}/activities/{activity.id}/reading-complete",
            headers=other_headers,
        )
        foreign_activity_assessment = client.post(
            f"/api/v1/courses/{course.id}/activities/{activity.id}/assessment",
            headers=other_headers,
        )
        assert foreign_owner.status_code == 404
        assert foreign_diagnostic.status_code == 404
        assert foreign_course.status_code == 404
        assert foreign_question.status_code == 404
        assert foreign_question_answer.status_code == 404
        assert foreign_learning_state.status_code == 404
        assert foreign_activity_selection.status_code == 404
        assert foreign_submit.status_code == 404
        assert foreign_retry_grading.status_code == 404
        assert foreign_activity.status_code == 404
        assert foreign_progress.status_code == 404
        assert foreign_reading.status_code == 404
        assert foreign_activity_assessment.status_code == 404

    def test_account_deletion_removes_activity_assessment_and_answer_rows(
        self, client, owner, db_session, fake_generation, published_course_with_lessons
    ):
        configure_diagnostic(fake_generation)
        course, _, _, _, _ = published_course_with_lessons
        session = start_diagnostic(client, course, owner).json()
        q_a, q_b = session["questions"]
        base = f"/api/v1/courses/{course.id}/assessment-sessions/{session['id']}"
        headers = auth_headers(owner.email)
        client.post(
            f"{base}/questions/{q_a['question_id']}/answer",
            json={"given_answer": "A"},
            headers=headers,
        )
        client.post(
            f"{base}/questions/{q_b['question_id']}/answer",
            json={"given_answer": "B"},
            headers=headers,
        )
        assert db_session.query(LearningActivity).count() == 1
        assert db_session.query(AssessmentSession).count() == 1
        assert db_session.query(AssessmentQuestion).count() == 2
        assert db_session.query(AnswerSubmission).count() == 2

        deleted = client.delete("/api/v1/me", headers=headers)
        assert deleted.status_code == 202
        assert db_session.query(LearningActivity).count() == 0
        assert db_session.query(AssessmentSession).count() == 0
        assert db_session.query(AssessmentQuestion).count() == 0
        assert db_session.query(AnswerSubmission).count() == 0
        assert db_session.query(QuestionAttempt).count() == 0
        assert db_session.query(MasteryEvent).count() == 0

    def test_failed_grading_keeps_answer_without_mastery_evidence(
        self,
        client,
        owner,
        db_session,
        fake_embeddings,
        monkeypatch,
        published_course_with_lessons,
    ):
        course, version, concept_a, _, lesson = published_course_with_lessons
        activity = LearningActivity(
            owner_id=owner.id,
            course_id=course.id,
            course_version_id=version.id,
            activity_type="NEW_LESSON",
            target_concept_ids=[str(concept_a.id)],
            lesson_id=lesson.id,
            status="AWAITING_ASSESSMENT",
            presentation_format="detailed",
            reading_completed_at=datetime.now(timezone.utc),
        )
        db_session.add(activity)
        db_session.flush()
        question = Question(
            course_id=course.id,
            course_version_id=version.id,
            owner_id=owner.id,
            question_type="SHORT_TEXT",
            prompt="Explain the concept.",
            options=None,
            correct_answer=None,
            rubric=["identifies the concept"],
            difficulty=0.5,
            is_diagnostic=0,
            version=1,
            model_id="fixture",
            prompt_version="test-v1",
        )
        db_session.add(question)
        db_session.flush()
        db_session.add(QuestionConcept(question_id=question.id, concept_id=concept_a.id, weight=1.0))
        db_session.commit()

        class FailingGeneration(FakeGenerationGateway):
            def generate(self, *args, **kwargs):
                raise GenerationError("fixture grading outage")

        def failing_service():
            return LearningService(db_session, FailingGeneration(), fake_embeddings)

        monkeypatch.setitem(app.dependency_overrides, learning_service_dependency, failing_service)
        headers = auth_headers(owner.email)
        session = client.post(
            f"/api/v1/courses/{course.id}/activities/{activity.id}/assessment",
            headers=headers,
        ).json()
        question_id = session["questions"][0]["question_id"]
        answer_url = f"/api/v1/courses/{course.id}/assessment-sessions/{session['id']}/questions/{question_id}/answer"
        submitted_answer = client.post(answer_url, json={"given_answer": "A supported response"}, headers=headers)
        assert submitted_answer.status_code == 200
        assert submitted_answer.json()["questions"][0]["answer"]["status"] == "GRADING_FAILED"
        assert "result" not in submitted_answer.json()["questions"][0]
        assert db_session.query(AnswerSubmission).count() == 1
        assert db_session.query(QuestionAttempt).filter(QuestionAttempt.assessment_question_id.isnot(None)).count() == 0
        assert db_session.query(MasteryEvent).count() == 0

        completed_set = client.post(
            f"/api/v1/courses/{course.id}/assessment-sessions/{session['id']}/submit",
            headers=headers,
        )
        assert completed_set.status_code == 200
        result = completed_set.json()["questions"][0]["result"]
        assert "correctness" not in result
        assert result["rubric"] == ["identifies the concept"]
        assert completed_set.json()["grading_state"] == "RETRY_REQUIRED"
        assert completed_set.json()["graded_answer_count"] == 0
        assert completed_set.json()["unresolved_answer_count"] == 1
        pending_progress = completed_set.json()["concept_progress"][0]
        assert pending_progress["before_band"] == "Not assessed"
        assert pending_progress["after_band"] == "Not assessed"
        assert pending_progress["after_evidence_strength"] == "Not assessed"
        active_on_continue = client.post(
            f"/api/v1/courses/{course.id}/activities/next", headers=headers
        )
        assert active_on_continue.status_code == 200
        assert active_on_continue.json()["id"] == str(activity.id)
        assert active_on_continue.json()["assessment_session_id"] == session["id"]
        assert db_session.query(AdaptationDecision).count() == 0

        def recovered_service():
            generation = FakeGenerationGateway().set_default('{"criteria_met": [true]}')
            return LearningService(db_session, generation, fake_embeddings)

        monkeypatch.setitem(app.dependency_overrides, learning_service_dependency, recovered_service)
        retry_url = (
            f"/api/v1/courses/{course.id}/assessment-sessions/{session['id']}"
            f"/questions/{question_id}/retry-grading"
        )
        recovered = client.post(retry_url, headers=headers)
        retried_again = client.post(retry_url, headers=headers)
        assert recovered.status_code == retried_again.status_code == 200
        assert recovered.json()["grading_state"] == "COMPLETE"
        assert recovered.json()["questions"][0]["result"]["correctness"] == 1.0
        assert recovered.json()["graded_answer_count"] == 1
        assert recovered.json()["unresolved_answer_count"] == 0
        assert recovered.json()["concept_progress"][0]["after_band"] == "Developing"
        assert recovered.json()["concept_progress"][0]["after_evidence_strength"] == "Limited evidence"
        assert db_session.query(AnswerSubmission).one().given_answer == "A supported response"
        assert db_session.query(QuestionAttempt).filter(QuestionAttempt.assessment_question_id.isnot(None)).count() == 1
        assert db_session.query(MasteryEvent).count() == 1
        db_session.refresh(activity)
        assert activity.status == "COMPLETED"


class TestAssessmentProgressAttribution:
    def test_grade_committed_during_progress_read_stays_after_its_own_session(
        self, owner, db_session, fake_generation, published_course_with_lessons, monkeypatch
    ):
        course, version, concept_a, _, lesson = published_course_with_lessons
        activity = LearningActivity(
            owner_id=owner.id,
            course_id=course.id,
            course_version_id=version.id,
            activity_type="NEW_LESSON",
            target_concept_ids=[str(concept_a.id)],
            lesson_id=lesson.id,
            status="COMPLETED",
        )
        db_session.add(activity)
        db_session.flush()
        question = Question(
            course_id=course.id,
            course_version_id=version.id,
            owner_id=owner.id,
            question_type="MCQ",
            prompt="Question for the progress snapshot?",
            options=["supported", "unsupported"],
            correct_answer="supported",
            rubric=None,
            difficulty=0.5,
            is_diagnostic=0,
            version=1,
            model_id="fixture",
            prompt_version="test-v1",
        )
        db_session.add(question)
        db_session.flush()
        db_session.add(QuestionConcept(question_id=question.id, concept_id=concept_a.id, weight=1.0))
        submitted_at = datetime.now(timezone.utc)
        session = AssessmentSession(
            activity_id=activity.id,
            course_version_id=version.id,
            assessment_type="LESSON",
            status="SUBMITTED",
            submitted_at=submitted_at,
        )
        db_session.add(session)
        db_session.flush()
        assessment_question = AssessmentQuestion(
            session_id=session.id,
            question_id=question.id,
            question_version=question.version,
            position=0,
        )
        db_session.add(assessment_question)
        db_session.commit()

        from app.modules.mastery.service import MasteryService

        original_visible_events = MasteryService.visible_mastery_events
        injected = False

        def commit_grade_before_evidence_read(service, *args, **kwargs):
            nonlocal injected
            if not injected:
                injected = True
                attempt = QuestionAttempt(
                    assessment_question_id=assessment_question.id,
                    question_id=question.id,
                    question_version=question.version,
                    owner_id=owner.id,
                    course_id=course.id,
                    given_answer="supported",
                    correctness=1.0,
                )
                db_session.add(attempt)
                db_session.flush()
                db_session.add(
                    MasteryEvent(
                        owner_id=owner.id,
                        course_id=course.id,
                        course_version_id=version.id,
                        concept_id=concept_a.id,
                        question_attempt_id=attempt.id,
                        correctness=1.0,
                        evidence_weight_base=1.0,
                        created_at=submitted_at - timedelta(seconds=1),
                    )
                )
                db_session.commit()
            return original_visible_events(service, *args, **kwargs)

        monkeypatch.setattr(
            MasteryService, "visible_mastery_events", commit_grade_before_evidence_read
        )
        progress = MasteryService(
            db_session, fake_generation, FakeEmbeddingGateway()
        ).get_assessment_concept_progress(course.id, owner.id, session.id, submitted_at)

        assert injected
        assert len(progress) == 1
        assert progress[0]["before_band"] == "Not assessed"
        assert progress[0]["after_band"] == "Developing"


class TestConcurrentActivitySelection:
    def test_completed_results_continue_to_one_persisted_supported_next_lesson(
        self, client, owner, db_session, fake_generation, published_course_with_lessons
    ):
        from app.modules.mastery.service import MasteryService

        course, version, concept_a, concept_b, first_lesson = published_course_with_lessons
        module = db_session.query(Module).filter_by(course_version_id=version.id).one()
        concept_c = Concept(
            course_id=course.id,
            course_version_id=version.id,
            owner_id=owner.id,
            canonical_key="concept-c",
            name="Concept C",
            definition="A later, unassessed concept.",
            importance=0.8,
        )
        db_session.add(concept_c)
        db_session.flush()
        next_lesson = Lesson(module_id=module.id, position=1, title="Next lesson", objective="Teach Concept C")
        db_session.add(next_lesson)
        db_session.flush()
        db_session.add(LessonConcept(lesson_id=next_lesson.id, concept_id=concept_c.id, weight=1.0))
        db_session.add(
            ConceptPrerequisite(
                course_id=course.id,
                course_version_id=version.id,
                prerequisite_concept_id=concept_a.id,
                dependent_concept_id=concept_c.id,
                strength=EdgeStrength.HARD.value,
                confidence=1.0,
            )
        )

        mastery = MasteryService(db_session, fake_generation, FakeEmbeddingGateway())
        for concept in (concept_a, concept_b):
            question = mastery.create_question(
                course.id,
                version.id,
                owner.id,
                "MCQ",
                f"Prior fixture check for {concept.name}?",
                {concept.id: 1.0},
                options=["yes", "no"],
                correct_answer="yes",
                difficulty=0.5,
                commit=False,
            )
            for _ in range(9):
                mastery.record_graded_attempt(question, owner.id, "yes", 1.0, commit=False)
        db_session.commit()

        configure_diagnostic(fake_generation)
        diagnostic = start_diagnostic(client, course, owner, max_questions=2).json()
        headers = auth_headers(owner.email)
        session_url = f"/api/v1/courses/{course.id}/assessment-sessions/{diagnostic['id']}"
        for question, answer in zip(diagnostic["questions"], ("A", "B"), strict=True):
            saved = client.post(
                f"{session_url}/questions/{question['question_id']}/answer",
                json={"given_answer": answer},
                headers=headers,
            )
            assert saved.status_code == 200
        submitted = client.post(f"{session_url}/submit", headers=headers)
        assert submitted.status_code == 200
        assert submitted.json()["grading_state"] == "COMPLETE"

        selected = client.post(f"/api/v1/courses/{course.id}/activities/next", headers=headers)
        repeated = client.post(f"/api/v1/courses/{course.id}/activities/next", headers=headers)
        assert selected.status_code == repeated.status_code == 200
        activity = selected.json()
        assert activity["id"] == repeated.json()["id"]
        assert activity["decision_id"] == repeated.json()["decision_id"]
        assert activity["experience_availability"] == "SUPPORTED"
        assert activity["activity_type"] == "NEW_LESSON"
        assert activity["course_version_id"] == str(version.id)
        assert activity["lesson_id"] == str(next_lesson.id)
        assert activity["target_concept_ids"] == [str(concept_c.id)]
        assert activity["reason"] == "You're ready for the next lesson, covering Concept C."
        assert activity["presentation_format"]
        assert db_session.query(AdaptationDecision).count() == 1
        assert (
            db_session.query(LearningActivity)
            .filter(
                LearningActivity.course_id == course.id,
                LearningActivity.status != "COMPLETED",
            )
            .count()
            == 1
        )

    def test_parallel_continue_requests_return_one_persisted_activity(
        self, client, tmp_path, monkeypatch
    ):
        database = tmp_path / "concurrent-learning.sqlite"
        engine = create_engine(
            f"sqlite:///{database}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        seed = session_factory()
        user = User(email="parallel@example.com", full_name="Parallel learner", is_active=True)
        seed.add(user)
        seed.flush()
        course = Course(owner_id=user.id, title="Parallel", status=CourseStatus.PUBLISHED.value)
        seed.add(course)
        seed.flush()
        version = CourseVersion(
            course_id=course.id,
            owner_id=user.id,
            version_number=1,
            status=CourseVersionStatus.READY.value,
        )
        seed.add(version)
        seed.flush()
        concept = Concept(
            course_id=course.id,
            course_version_id=version.id,
            owner_id=user.id,
            canonical_key="parallel",
            name="Parallel concept",
            definition="A concurrency fixture.",
            importance=0.8,
        )
        seed.add(concept)
        seed.flush()
        module = Module(course_version_id=version.id, position=0, title="Module")
        seed.add(module)
        seed.flush()
        lesson = Lesson(module_id=module.id, position=0, title="Lesson")
        seed.add(lesson)
        seed.flush()
        seed.add(LessonConcept(lesson_id=lesson.id, concept_id=concept.id, weight=1.0))
        seed.add(
            LearningActivity(
                owner_id=user.id,
                course_id=course.id,
                course_version_id=version.id,
                activity_type="DIAGNOSTIC",
                target_concept_ids=[str(concept.id)],
                status="COMPLETED",
            )
        )
        course.active_version_id = version.id
        seed.commit()
        course_id = str(course.id)
        version_id = str(version.id)
        user_id = user.id
        email = user.email
        seed.close()

        def override_db():
            db = session_factory()
            try:
                yield db
            finally:
                db.close()

        barrier = Barrier(2)
        answer_barrier = Barrier(2)

        class RacingLearningService(LearningService):
            def select_or_resume(self, course_id, owner_id):
                barrier.wait(timeout=5)
                return super().select_or_resume(course_id, owner_id)

            def submit_answer(self, course_id, session_id, question_id, owner_id, given_answer):
                answer_barrier.wait(timeout=5)
                return super().submit_answer(course_id, session_id, question_id, owner_id, given_answer)

        def override_learning_service(db=Depends(get_db)):
            return RacingLearningService(db, FakeGenerationGateway(), FakeEmbeddingGateway())

        monkeypatch.setitem(app.dependency_overrides, get_db, override_db)
        monkeypatch.setitem(app.dependency_overrides, learning_service_dependency, override_learning_service)

        def continue_learning():
            with TestClient(app) as local_client:
                return local_client.post(
                    f"/api/v1/courses/{course_id}/activities/next",
                    headers=auth_headers(email),
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: continue_learning(), range(2)))
        assert [response.status_code for response in responses] == [200, 200]
        bodies = [response.json() for response in responses]
        assert bodies[0]["id"] == bodies[1]["id"]
        assert bodies[0]["decision_id"] == bodies[1]["decision_id"]
        assert bodies[0]["experience_availability"] == "SUPPORTED"

        check = session_factory()
        assert check.query(LearningActivity).filter(LearningActivity.course_id == uuid.UUID(course_id)).count() == 2
        assert check.query(AdaptationDecision).filter(AdaptationDecision.course_id == uuid.UUID(course_id)).count() == 1
        activity = (
            check.query(LearningActivity)
            .filter(
                LearningActivity.course_id == uuid.UUID(course_id),
                LearningActivity.status != "COMPLETED",
            )
            .one()
        )
        selected_concept_id = uuid.UUID(activity.target_concept_ids[0])
        assert activity.lesson_id is not None
        activity.reading_completed_at = datetime.now(timezone.utc)
        question = Question(
            course_id=uuid.UUID(course_id),
            course_version_id=uuid.UUID(version_id),
            owner_id=user_id,
            question_type="MCQ",
            prompt="Concurrent answer?",
            options=["A", "B"],
            correct_answer="A",
            difficulty=0.5,
            is_diagnostic=0,
            version=1,
            model_id="fixture",
            prompt_version="test-v1",
        )
        check.add(question)
        check.flush()
        check.add(QuestionConcept(question_id=question.id, concept_id=selected_concept_id, weight=1.0))
        check.commit()

        with TestClient(app) as local_client:
            session_response = local_client.post(
                f"/api/v1/courses/{course_id}/activities/{activity.id}/assessment",
                headers=auth_headers(email),
            )
        assert session_response.status_code == 200
        assessment = session_response.json()
        item = assessment["questions"][0]
        answer_url = (
            f"/api/v1/courses/{course_id}/assessment-sessions/{assessment['id']}"
            f"/questions/{item['question_id']}/answer"
        )

        def submit_same_answer():
            with TestClient(app) as local_client:
                return local_client.post(
                    answer_url,
                    json={"given_answer": "A"},
                    headers=auth_headers(email),
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            answer_responses = list(pool.map(lambda _: submit_same_answer(), range(2)))
        assert [response.status_code for response in answer_responses] == [200, 200]
        assert check.query(AnswerSubmission).count() == 1
        assert check.query(QuestionAttempt).filter(QuestionAttempt.assessment_question_id.isnot(None)).count() == 1
        assert check.query(MasteryEvent).count() == 1
        check.close()
        engine.dispose()
