import json
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.courses.models import Course
from app.modules.documents.chunk_models import Chunk
from app.modules.documents.models import Document
from app.modules.curriculum.models import CourseVersion
from app.modules.learning.models import AssessmentSession, LearningActivity
from app.modules.tutor.models import TutorMessage
from tests.conftest import auth_headers


@pytest.fixture()
def course_with_chunk(db_session, owner):
    course = Course(owner_id=owner.id, title="OS Course")
    db_session.add(course)
    db_session.commit()
    db_session.refresh(course)

    doc = Document(
        course_id=course.id, owner_id=owner.id, filename="notes.txt",
        storage_path="/dev/null", checksum_sha256="a" * 64,
    )
    db_session.add(doc)
    db_session.commit()

    chunk = Chunk(
        id=uuid.uuid4(), document_id=doc.id, course_id=course.id, owner_id=owner.id,
        text="A deadlock is a circular wait condition among processes holding resources.",
    )
    db_session.add(chunk)
    db_session.commit()
    return course, chunk


def parse_sse_events(body: str):
    events = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        lines = block.strip().split("\n")
        event_type = lines[0].removeprefix("event: ")
        events.append(event_type)
    return events


def create_open_assessment(db_session, owner):
    course = Course(owner_id=owner.id, title="Assessment lockout course")
    db_session.add(course)
    db_session.flush()
    version = CourseVersion(course_id=course.id, owner_id=owner.id, version_number=1, status="READY")
    db_session.add(version)
    db_session.flush()
    activity = LearningActivity(
        owner_id=owner.id,
        course_id=course.id,
        course_version_id=version.id,
        activity_type="DIAGNOSTIC",
        target_concept_ids=[],
        status="IN_PROGRESS",
    )
    db_session.add(activity)
    db_session.flush()
    session = AssessmentSession(
        activity_id=activity.id,
        course_version_id=version.id,
        assessment_type="DIAGNOSTIC",
        status="OPEN",
    )
    db_session.add(session)
    db_session.commit()
    return course, activity, session


class TestSSEContract:
    @pytest.mark.parametrize("coverage,expected_status", [('false', 200), ('"false"', 503)])
    def test_inadequate_or_unavailable_remainder_cannot_reach_the_learner(
        self, client, owner, db_session, fake_generation, course_with_chunk, coverage, expected_status
    ):
        course, chunk = course_with_chunk
        fake_generation.when_prompt_contains("ANSWER COVERAGE:", '{"answers_question": ' + coverage + '}').when_prompt_contains(
            "BATCH CHECKS:", '{"results": [{"id": 0, "supported": true}]}'
        ).set_default(json.dumps({"insufficient_evidence": False,
            "answer_markdown": "A deadlock is a circular wait. Resolve it with magic.",
            "claims": [{"text": "A deadlock is a circular wait.", "chunk_id": str(chunk.id)}]}))
        response = client.post(f"/api/v1/courses/{course.id}/tutor", json={"question": "How do I resolve a deadlock?"},
                               headers=auth_headers(owner.email))
        assert response.status_code == expected_status
        assert "magic" not in response.text
        if expected_status == 200:
            assert parse_sse_events(response.text) == ["retrieval", "insufficient"]
            saved = db_session.query(TutorMessage).one()
            assert saved.grounding_mode == "insufficient"
            assert saved.citations == []
        else:
            assert response.json()["error_category"] == "VALIDATION_UNAVAILABLE"
            assert db_session.query(TutorMessage).count() == 0

    def test_only_validated_blocks_reach_sse_and_saved_history(
        self, client, owner, db_session, fake_generation, course_with_chunk
    ):
        course, chunk = course_with_chunk
        supported = "A deadlock is a circular wait."
        unsupported = "Deadlocks are caused by ghosts."
        fake_generation.when_prompt_contains("ANSWER COVERAGE:", '{"answers_question": true}').when_prompt_contains("BATCH CHECKS:", json.dumps({
            "results": [{"id": 0, "supported": True}, {"id": 1, "supported": False}],
        })).set_default(json.dumps({
            "insufficient_evidence": False,
            "answer_markdown": f"{supported} {unsupported} UNLISTED-FICTION",
            "claims": [{"text": text, "chunk_id": str(chunk.id)} for text in [supported, unsupported]],
        }))
        conversation = str(uuid.uuid4())
        route = f"/api/v1/courses/{course.id}/tutor"
        response = client.post(route, json={"question": "What is a deadlock?", "conversation_id": conversation},
                               headers=auth_headers(owner.email))
        assert response.status_code == 200
        assert "UNLISTED-FICTION" not in response.text
        assert unsupported not in response.text
        assert supported in response.text
        assert '"unsampled"' not in response.text
        history = client.get(f"{route}/history?conversation_id={conversation}", headers=auth_headers(owner.email)).json()
        turn = history["turns"][0]
        assert turn["answer_markdown"] == supported
        assert [citation["validation_status"] for citation in turn["citations"]] == ["passed"]
        assert db_session.query(TutorMessage).one().prompt_version == "tutor-prompt-v3"
        assert len(fake_generation.calls) == 4  # answer, complete batch, bounded answer retry, remainder coverage

    def test_incomplete_validation_batch_is_unavailable_without_saving_a_turn(
        self, client, owner, db_session, fake_generation, course_with_chunk
    ):
        course, chunk = course_with_chunk
        fake_generation.when_prompt_contains("BATCH CHECKS:", '{"results": [{"id": 0, "supported": true}]}').set_default(json.dumps({
            "insufficient_evidence": False, "answer_markdown": "First. Second.",
            "claims": [{"text": text, "chunk_id": str(chunk.id)} for text in ["First.", "Second."]],
        }))
        response = client.post(f"/api/v1/courses/{course.id}/tutor", json={"question": "What is a deadlock?"},
                               headers=auth_headers(owner.email))
        assert response.status_code == 503
        assert response.json()["error_category"] == "VALIDATION_UNAVAILABLE"
        assert db_session.query(TutorMessage).count() == 0

    def test_assessment_started_during_generation_blocks_answer_delivery(
        self, client, owner, db_session, course_with_chunk, monkeypatch
    ):
        from app.main import app
        from app.modules.tutor.router import _service
        from app.modules.tutor.service import TutorService
        from app.services.generation.fake import FakeGenerationGateway
        from app.services.embedding.fake import FakeEmbeddingGateway
        from app.services.vectorstore.fake import FakeVectorStore
        course, _chunk = course_with_chunk

        class StartAssessmentGateway(FakeGenerationGateway):
            def generate(self, prompt, **kwargs):
                create_open_assessment(db_session, owner)
                return '{"insufficient_evidence": true, "answer_markdown": "No supported answer.", "claims": []}'

        service = TutorService(db_session, StartAssessmentGateway(), FakeEmbeddingGateway(), FakeVectorStore())
        monkeypatch.setitem(app.dependency_overrides, _service, lambda: service)
        response = client.post(f"/api/v1/courses/{course.id}/tutor", json={"question": "What is a deadlock?"}, headers=auth_headers(owner.email))
        assert response.status_code == 409
        assert "token" not in response.text

    def test_history_is_owner_course_conversation_and_context_scoped(
        self, client, owner, other_user, db_session, course_with_chunk
    ):
        course, _chunk = course_with_chunk
        other_course = Course(owner_id=owner.id, title="Other owned course")
        db_session.add(other_course); db_session.flush()
        conversation_id = uuid.uuid4()
        base = dict(owner_id=owner.id, course_id=course.id, conversation_id=conversation_id,
                    question="Owned question", answer_markdown="Owned answer", retrieved_chunk_ids=[],
                    citations=[], grounding_mode="source_only", model_id="fixture", prompt_version="fixture")
        own = TutorMessage(**base)
        db_session.add_all([
            own,
            TutorMessage(**{**base, "owner_id": other_user.id, "question": "FOREIGN_OWNER"}),
            TutorMessage(**{**base, "course_id": other_course.id, "question": "OTHER_COURSE"}),
            TutorMessage(**{**base, "context_lesson_id": uuid.uuid4(), "question": "OTHER_LESSON"}),
            TutorMessage(**{**base, "conversation_id": uuid.uuid4(), "question": "OTHER_CONVERSATION"}),
        ])
        db_session.commit()
        route = f"/api/v1/courses/{course.id}/tutor/history?conversation_id={conversation_id}"
        response = client.get(route, headers=auth_headers(owner.email))
        assert [turn["id"] for turn in response.json()["turns"]] == [str(own.id)]
        assert response.headers["cache-control"] == "no-store"
        assert client.get(route, headers=auth_headers(other_user.email)).status_code == 404

    def test_history_paginates_in_saved_order_and_rejects_foreign_cursor(
        self, client, owner, db_session, course_with_chunk, monkeypatch
    ):
        from app.core.config import settings
        course, _chunk = course_with_chunk
        monkeypatch.setattr(settings, "TUTOR_HISTORY_PAGE_SIZE_V1", 2)
        conversation = uuid.uuid4()
        rows = []
        for index in range(6):
            row = TutorMessage(owner_id=owner.id, course_id=course.id, conversation_id=conversation,
                question=f"question {index}", answer_markdown="saved answer", retrieved_chunk_ids=[], citations=[],
                grounding_mode="source_only", model_id="fixture", prompt_version="fixture",
                created_at=datetime(2026, 10, 8, tzinfo=timezone.utc) + timedelta(seconds=index))
            rows.append(row); db_session.add(row)
        db_session.commit()
        route = f"/api/v1/courses/{course.id}/tutor/history?conversation_id={conversation}"
        page = client.get(route, headers=auth_headers(owner.email)).json()
        assert [turn["question"] for turn in page["turns"]] == ["question 4", "question 5"]
        assert page["has_more"] is True
        older = client.get(f"{route}&before={rows[4].id}", headers=auth_headers(owner.email)).json()
        assert [turn["question"] for turn in older["turns"]] == ["question 2", "question 3"]
        oldest = client.get(f"{route}&before={rows[2].id}", headers=auth_headers(owner.email)).json()
        assert [turn["question"] for turn in oldest["turns"]] == ["question 0", "question 1"]
        assert oldest["has_more"] is False
        assert client.get(f"{route}&before={uuid.uuid4()}", headers=auth_headers(owner.email)).status_code == 404

    def test_follow_up_retrieves_topic_and_restores_saved_history(
        self, client, owner, fake_generation, course_with_chunk
    ):
        course, chunk = course_with_chunk
        conversation_id = str(uuid.uuid4())
        fake_generation.when_prompt_contains("BATCH CHECKS:", '{"results": [{"id": 0, "supported": true}]}').set_default(
            '{"insufficient_evidence": false, "answer_markdown": "A deadlock is a circular wait.", '
            f'"claims": [{{"text": "A deadlock is a circular wait.", "chunk_id": "{chunk.id}"}}]}}'
        )
        route = f"/api/v1/courses/{course.id}/tutor"
        first = client.post(route, json={"question": "What is a deadlock?", "conversation_id": conversation_id}, headers=auth_headers(owner.email))
        assert first.status_code == 200
        second = client.post(route, json={"question": "Can you explain that more simply?", "conversation_id": conversation_id}, headers=auth_headers(owner.email))
        assert "token" in parse_sse_events(second.text)
        assert any("PREVIOUS CONVERSATION" in prompt and "What is a deadlock?" in prompt for prompt in fake_generation.calls)
        restored = client.get(f"{route}/history?conversation_id={conversation_id}", headers=auth_headers(owner.email))
        assert restored.status_code == 200
        assert restored.json()["available"] is True
        assert [turn["question"] for turn in restored.json()["turns"]] == ["What is a deadlock?", "Can you explain that more simply?"]

    def test_history_reports_account_wide_assessment_lock_without_ai_calls(
        self, client, owner, db_session, fake_generation, course_with_chunk
    ):
        course, _chunk = course_with_chunk
        _other_course, _activity, session = create_open_assessment(db_session, owner)
        route = f"/api/v1/courses/{course.id}/tutor/history?conversation_id={uuid.uuid4()}"
        response = client.get(route, headers=auth_headers(owner.email))
        assert response.status_code == 200
        assert response.json()["available"] is False
        assert response.json()["turns"] == []
        session.status = "SUBMITTED"
        db_session.commit()
        assert client.get(route, headers=auth_headers(owner.email)).json()["available"] is True
        assert fake_generation.calls == []

    @pytest.mark.parametrize("response", [None, "not JSON"])
    def test_provider_or_parse_failure_is_unavailable_not_source_insufficiency(
        self, client, owner, db_session, fake_generation, course_with_chunk, response
    ):
        course, _chunk = course_with_chunk
        if response is not None:
            fake_generation.set_default(response)
        else:
            fake_generation._default = None
        reply = client.post(f"/api/v1/courses/{course.id}/tutor", json={"question": "What is a deadlock?"}, headers=auth_headers(owner.email))
        assert reply.status_code == 503
        assert reply.json()["error_category"] in {"PROVIDER_UNAVAILABLE", "RESPONSE_INVALID"}
        assert db_session.query(TutorMessage).count() == 0

    def test_validation_outage_is_unavailable_not_source_insufficiency(
        self, client, owner, db_session, fake_generation, course_with_chunk
    ):
        course, chunk = course_with_chunk
        fake_generation.when_prompt_contains("BATCH CHECKS:", "not JSON").set_default(
            '{"insufficient_evidence": false, "answer_markdown": "A deadlock is a circular wait.", '
            f'"claims": [{{"text": "A deadlock is a circular wait.", "chunk_id": "{chunk.id}"}}]}}'
        )
        reply = client.post(f"/api/v1/courses/{course.id}/tutor", json={"question": "What is a deadlock?"}, headers=auth_headers(owner.email))
        assert reply.status_code == 503
        assert reply.json()["error_category"] == "VALIDATION_UNAVAILABLE"
        assert db_session.query(TutorMessage).count() == 0

    def test_open_assessment_blocks_chat_before_generation(self, client, owner, db_session, fake_generation):
        course, _, _ = create_open_assessment(db_session, owner)
        calls_before = len(fake_generation.calls)

        response = client.post(
            f"/api/v1/courses/{course.id}/tutor",
            json={"question": "Explain this topic"},
            headers=auth_headers(owner.email),
        )

        assert response.status_code == 409
        assert "unavailable during an active assessment" in response.json()["detail"]
        assert len(fake_generation.calls) == calls_before

    def test_open_assessment_blocks_legacy_lesson_content_route(self, client, owner, db_session, fake_generation):
        course, _, _ = create_open_assessment(db_session, owner)
        calls_before = len(fake_generation.calls)

        response = client.get(
            f"/api/v1/courses/{course.id}/lessons/{uuid.uuid4()}/content",
            headers=auth_headers(owner.email),
        )

        assert response.status_code == 409
        assert "unavailable during an active assessment" in response.json()["detail"]
        assert len(fake_generation.calls) == calls_before

    def test_submitted_assessment_allows_results_clarification(self, client, owner, db_session, fake_generation):
        course, _, session = create_open_assessment(db_session, owner)
        session.status = "SUBMITTED"
        db_session.commit()

        response = client.post(
            f"/api/v1/courses/{course.id}/tutor",
            json={"question": "Explain this topic"},
            headers=auth_headers(owner.email),
        )

        assert response.status_code == 200
        assert parse_sse_events(response.text) == ["retrieval", "insufficient"]

    def test_grounded_answer_produces_the_documented_event_sequence(
        self, client, owner, fake_generation, course_with_chunk
    ):
        course, chunk = course_with_chunk
        fake_generation.when_prompt_contains("BATCH CHECKS:", '{"results": [{"id": 0, "supported": true}]}').set_default(
            '{"insufficient_evidence": false, "answer_markdown": "A deadlock is a circular wait.", '
            f'"claims": [{{"text": "A deadlock is a circular wait.", "chunk_id": "{chunk.id}"}}]}}'
        )
        resp = client.post(
            f"/api/v1/courses/{course.id}/tutor",
            json={"question": "What is a deadlock?"},
            headers=auth_headers(owner.email),
        )
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)
        assert events[0] == "retrieval"
        assert events[-1] == "done"
        assert "citation" in events
        assert "token" in events
        assert "insufficient" not in events

    def test_insufficiency_produces_insufficient_event_only(self, client, owner, db_session):
        course = Course(owner_id=owner.id, title="Empty Course")
        db_session.add(course)
        db_session.commit()
        resp = client.post(
            f"/api/v1/courses/{course.id}/tutor",
            json={"question": "What is quantum entanglement?"},
            headers=auth_headers(owner.email),
        )
        events = parse_sse_events(resp.text)
        assert events == ["retrieval", "insufficient"]
        assert "token" not in events and "citation" not in events and "done" not in events

    def test_unknown_course_is_404(self, client, owner):
        resp = client.post(
            f"/api/v1/courses/{uuid.uuid4()}/tutor", json={"question": "q"}, headers=auth_headers(owner.email)
        )
        assert resp.status_code == 404


class TestEndToEnd:
    def test_covered_then_uncovered_question_in_the_same_flow(
        self, client, owner, db_session, fake_generation, course_with_chunk
    ):
        course, chunk = course_with_chunk
        fake_generation.when_prompt_contains("BATCH CHECKS:", '{"results": [{"id": 0, "supported": true}]}').when_prompt_contains(
            "deadlock",
            '{"insufficient_evidence": false, "answer_markdown": "A deadlock is a circular wait.", '
            f'"claims": [{{"text": "A deadlock is a circular wait.", "chunk_id": "{chunk.id}"}}]}}',
        )

        covered = client.post(
            f"/api/v1/courses/{course.id}/tutor",
            json={"question": "What is a deadlock?"},
            headers=auth_headers(owner.email),
        )
        covered_events = parse_sse_events(covered.text)
        assert "citation" in covered_events

        uncovered = client.post(
            f"/api/v1/courses/{course.id}/tutor",
            # No shared vocabulary at all with the chunk's text (even a common
            # word like "is" would register as lexical overlap under the
            # SQLite fallback scorer in retrieval/lexical.py).
            json={"question": "Explain photosynthesis chlorophyll sunlight"},
            headers=auth_headers(owner.email),
        )
        uncovered_events = parse_sse_events(uncovered.text)
        assert uncovered_events == ["retrieval", "insufficient"]


class TestLessonContentEndpoint:
    def test_generates_real_grounded_content_not_a_placeholder(
        self, client, owner, db_session, fake_generation, course_with_chunk
    ):
        from app.modules.curriculum.models import (
            Concept, ConceptSource, CourseVersion, CourseVersionStatus, Lesson, LessonConcept, Module,
        )

        course, chunk = course_with_chunk
        version = CourseVersion(
            course_id=course.id, owner_id=owner.id, version_number=1, status=CourseVersionStatus.READY.value,
        )
        db_session.add(version)
        db_session.flush()
        concept = Concept(
            course_id=course.id, course_version_id=version.id, owner_id=owner.id,
            canonical_key="deadlock", name="Deadlock", definition="def", importance=0.9,
        )
        db_session.add(concept)
        db_session.flush()
        db_session.add(ConceptSource(concept_id=concept.id, chunk_id=chunk.id, course_id=course.id, owner_id=owner.id))
        module = Module(course_version_id=version.id, position=0, title="M1")
        db_session.add(module)
        db_session.flush()
        lesson = Lesson(module_id=module.id, position=0, title="Deadlocks", objective="Understand it.")
        db_session.add(lesson)
        db_session.flush()
        db_session.add(LessonConcept(lesson_id=lesson.id, concept_id=concept.id))
        db_session.commit()

        fake_generation.when_prompt_contains("BATCH CHECKS:", '{"results": [{"id": 0, "supported": true}]}').set_default(
            '{"insufficient_evidence": false, "answer_markdown": "Real generated content.", '
            f'"claims": [{{"text": "Real generated content.", "chunk_id": "{chunk.id}"}}]}}'
        )

        resp = client.get(
            f"/api/v1/courses/{course.id}/lessons/{lesson.id}/content?format=detailed",
            headers=auth_headers(owner.email),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["content_markdown"] == "Real generated content."
        assert len(body["citations"]) == 1

    def test_invalid_format_is_rejected(self, client, owner, course_with_chunk):
        course, _ = course_with_chunk
        resp = client.get(
            f"/api/v1/courses/{course.id}/lessons/{uuid.uuid4()}/content?format=nonsense",
            headers=auth_headers(owner.email),
        )
        assert resp.status_code == 422

    def test_unknown_lesson_is_404(self, client, owner, course_with_chunk):
        course, _ = course_with_chunk
        resp = client.get(
            f"/api/v1/courses/{course.id}/lessons/{uuid.uuid4()}/content?format=detailed",
            headers=auth_headers(owner.email),
        )
        assert resp.status_code == 404
