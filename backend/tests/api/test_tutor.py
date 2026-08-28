import re
import uuid

import pytest

from app.modules.courses.models import Course
from app.modules.documents.chunk_models import Chunk
from app.modules.documents.models import Document
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


class TestSSEContract:
    def test_grounded_answer_produces_the_documented_event_sequence(
        self, client, owner, fake_generation, course_with_chunk
    ):
        course, chunk = course_with_chunk
        fake_generation.when_prompt_contains("SOURCE TEXT", '{"supported": true}').set_default(
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
        fake_generation.when_prompt_contains("SOURCE TEXT", '{"supported": true}').when_prompt_contains(
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

        fake_generation.when_prompt_contains("SOURCE TEXT", '{"supported": true}').set_default(
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
