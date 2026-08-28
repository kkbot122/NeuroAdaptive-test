import uuid

import pytest

from app.modules.courses.models import Course
from app.modules.curriculum.models import (
    Concept,
    ConceptSource,
    CourseVersion,
    CourseVersionStatus,
    Lesson,
    LessonConcept,
    Module,
)
from app.modules.documents.chunk_models import Chunk
from app.modules.documents.models import Document
from app.modules.tutor.models import GroundingMode, TutorMessage
from app.modules.tutor.service import TutorNotFound, TutorService
from app.services.embedding.fake import FakeEmbeddingGateway
from app.services.generation.fake import FakeGenerationGateway
from app.services.vectorstore.fake import FakeVectorStore


def make_service(db_session, generation=None):
    return TutorService(db_session, generation or FakeGenerationGateway(), FakeEmbeddingGateway(), FakeVectorStore())


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


class TestOwnership:
    def test_another_users_course_raises_not_found(self, db_session, other_user, course_with_chunk):
        course, _ = course_with_chunk
        service = make_service(db_session)
        with pytest.raises(TutorNotFound):
            service.ask(course.id, other_user.id, "What is a deadlock?")


class TestCitationValidation:
    def test_fabricated_chunk_id_is_stripped_before_reaching_the_learner(
        self, db_session, owner, course_with_chunk
    ):
        course, chunk = course_with_chunk
        gen = FakeGenerationGateway().set_default(
            '{"insufficient_evidence": false, "answer_markdown": "A deadlock is a circular wait.", '
            f'"claims": [{{"text": "A deadlock is a circular wait.", "chunk_id": "{uuid.uuid4()}"}}]}}'
        )
        service = make_service(db_session, gen)
        result = service.ask(course.id, owner.id, "What is a deadlock?")
        assert result.citations == []
        assert "A deadlock is a circular wait." not in result.answer_markdown
        assert result.grounding_mode == GroundingMode.INSUFFICIENT.value

    def test_wrong_course_chunk_id_is_rejected(self, db_session, owner, course_with_chunk):
        course, chunk = course_with_chunk
        other_course = Course(owner_id=owner.id, title="Other Course")
        db_session.add(other_course)
        db_session.commit()
        other_doc = Document(
            course_id=other_course.id, owner_id=owner.id, filename="x.txt",
            storage_path="/dev/null", checksum_sha256="b" * 64,
        )
        db_session.add(other_doc)
        db_session.commit()
        foreign_chunk = Chunk(
            id=uuid.uuid4(), document_id=other_doc.id, course_id=other_course.id, owner_id=owner.id,
            text="Unrelated content.",
        )
        db_session.add(foreign_chunk)
        db_session.commit()

        gen = FakeGenerationGateway().set_default(
            '{"insufficient_evidence": false, "answer_markdown": "A deadlock is bad.", '
            f'"claims": [{{"text": "A deadlock is bad.", "chunk_id": "{foreign_chunk.id}"}}]}}'
        )
        service = make_service(db_session, gen)
        result = service.ask(course.id, owner.id, "What is a deadlock?")
        assert result.citations == []

    def test_correctly_scoped_citation_passes_and_is_returned(self, db_session, owner, course_with_chunk):
        course, chunk = course_with_chunk
        gen = (
            FakeGenerationGateway()
            .when_prompt_contains("SOURCE TEXT", '{"supported": true}')
            .set_default(
                '{"insufficient_evidence": false, "answer_markdown": "A deadlock is a circular wait.", '
                f'"claims": [{{"text": "A deadlock is a circular wait.", "chunk_id": "{chunk.id}"}}]}}'
            )
        )
        service = make_service(db_session, gen)
        result = service.ask(course.id, owner.id, "What is a deadlock?")
        assert len(result.citations) == 1
        assert result.citations[0].chunk_id == str(chunk.id)
        assert result.grounding_mode == GroundingMode.SOURCE_ONLY.value

    def test_tier2_failure_triggers_a_documented_fallback_path(self, db_session, owner, course_with_chunk):
        course, chunk = course_with_chunk
        # Same response every call -- the retry will produce an identical
        # (still-failing) claim, so the service must fall back to stripping
        # rather than looping or crashing.
        gen = FakeGenerationGateway().set_default(
            '{"insufficient_evidence": false, "answer_markdown": "Deadlocks are caused by ghosts.", '
            f'"claims": [{{"text": "Deadlocks are caused by ghosts.", "chunk_id": "{chunk.id}"}}]}}'
        )
        service = make_service(db_session, gen)
        service.entailment_checker = lambda claim, source: False  # deterministic fake: always unsupported
        result = service.ask(course.id, owner.id, "What is a deadlock?")
        assert result.fallback_path in {"stripped", "retried", "insufficiency"}
        assert "Deadlocks are caused by ghosts." not in result.answer_markdown


class TestInsufficiency:
    def test_no_retrieval_hits_produces_insufficient_grounding(self, db_session, owner):
        course = Course(owner_id=owner.id, title="Empty Course")
        db_session.add(course)
        db_session.commit()
        service = make_service(db_session)
        result = service.ask(course.id, owner.id, "What is quantum entanglement?")
        assert result.grounding_mode == GroundingMode.INSUFFICIENT.value
        assert result.citations == []

    def test_model_declared_insufficient_evidence_is_honored(self, db_session, owner, course_with_chunk):
        course, chunk = course_with_chunk
        gen = FakeGenerationGateway().set_default(
            '{"insufficient_evidence": true, "answer_markdown": "", "claims": []}'
        )
        service = make_service(db_session, gen)
        result = service.ask(course.id, owner.id, "What is a deadlock?")
        assert result.grounding_mode == GroundingMode.INSUFFICIENT.value


class TestRetrievalIsolation:
    def test_query_from_one_user_never_returns_another_users_chunks(self, db_session, owner, other_user):
        course_a = Course(owner_id=owner.id, title="Course A")
        course_b = Course(owner_id=other_user.id, title="Course B")
        db_session.add_all([course_a, course_b])
        db_session.commit()

        doc_a = Document(course_id=course_a.id, owner_id=owner.id, filename="a.txt", storage_path="/dev/null", checksum_sha256="c" * 64)
        doc_b = Document(course_id=course_b.id, owner_id=other_user.id, filename="b.txt", storage_path="/dev/null", checksum_sha256="d" * 64)
        db_session.add_all([doc_a, doc_b])
        db_session.commit()

        chunk_a = Chunk(id=uuid.uuid4(), document_id=doc_a.id, course_id=course_a.id, owner_id=owner.id, text="A deadlock is bad for scheduling.")
        chunk_b = Chunk(id=uuid.uuid4(), document_id=doc_b.id, course_id=course_b.id, owner_id=other_user.id, text="A deadlock is bad for scheduling too.")
        db_session.add_all([chunk_a, chunk_b])
        db_session.commit()

        service = make_service(db_session)
        result = service.ask(course_a.id, owner.id, "What is a deadlock?")
        assert str(chunk_a.id) in result.retrieved_chunk_ids
        assert str(chunk_b.id) not in result.retrieved_chunk_ids


class TestPersistence:
    def test_every_call_persists_a_tutor_message_with_required_fields(self, db_session, owner, course_with_chunk):
        course, chunk = course_with_chunk
        service = make_service(db_session)
        before = db_session.query(TutorMessage).count()
        service.ask(course.id, owner.id, "What is a deadlock?")
        after = db_session.query(TutorMessage).count()
        assert after == before + 1

        message = db_session.query(TutorMessage).order_by(TutorMessage.created_at.desc()).first()
        assert message.retrieved_chunk_ids is not None
        assert message.model_id is not None
        assert message.grounding_mode is not None


@pytest.fixture()
def course_with_lesson(db_session, owner):
    """course -> version -> module -> lesson -> concept (grounded in a real
    chunk via ConceptSource) -- everything generate_lesson_content needs."""
    course = Course(owner_id=owner.id, title="OS Course")
    db_session.add(course)
    db_session.commit()

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

    version = CourseVersion(
        course_id=course.id, owner_id=owner.id, version_number=1, status=CourseVersionStatus.READY.value,
    )
    db_session.add(version)
    db_session.flush()

    concept = Concept(
        course_id=course.id, course_version_id=version.id, owner_id=owner.id,
        canonical_key="deadlock", name="Deadlock", definition="A circular wait.", importance=0.9,
    )
    db_session.add(concept)
    db_session.flush()
    db_session.add(
        ConceptSource(concept_id=concept.id, chunk_id=chunk.id, course_id=course.id, owner_id=owner.id)
    )

    module = Module(course_version_id=version.id, position=0, title="Concurrency")
    db_session.add(module)
    db_session.flush()
    lesson = Lesson(module_id=module.id, position=0, title="Deadlocks", objective="Understand deadlocks.")
    db_session.add(lesson)
    db_session.flush()
    db_session.add(LessonConcept(lesson_id=lesson.id, concept_id=concept.id))
    db_session.commit()

    return course, lesson, chunk


class TestGenerateLessonContent:
    def test_reuses_ask_and_grounds_in_the_lessons_concepts(self, db_session, owner, course_with_lesson):
        course, lesson, chunk = course_with_lesson
        gen = FakeGenerationGateway().when_prompt_contains("SOURCE TEXT", '{"supported": true}').set_default(
            '{"insufficient_evidence": false, "answer_markdown": "Deadlocks are a circular wait.", '
            f'"claims": [{{"text": "Deadlocks are a circular wait.", "chunk_id": "{chunk.id}"}}]}}'
        )
        service = make_service(db_session, gen)
        result = service.generate_lesson_content(course.id, owner.id, lesson.id, "detailed")

        assert result.grounding_mode == GroundingMode.SOURCE_ONLY.value
        assert len(result.citations) == 1
        assert result.citations[0].chunk_id == str(chunk.id)
        # The prompt sent for generation named the lesson's actual concept.
        assert any("Deadlock" in p for p in gen.calls)

    def test_unknown_lesson_is_not_found(self, db_session, owner, course_with_lesson):
        course, _, _ = course_with_lesson
        service = make_service(db_session)
        with pytest.raises(TutorNotFound):
            service.generate_lesson_content(course.id, owner.id, uuid.uuid4(), "detailed")

    def test_a_lesson_from_another_course_is_not_found(self, db_session, owner, course_with_lesson):
        _, lesson, _ = course_with_lesson
        other_course = Course(owner_id=owner.id, title="Unrelated Course")
        db_session.add(other_course)
        db_session.commit()
        service = make_service(db_session)
        with pytest.raises(TutorNotFound):
            service.generate_lesson_content(other_course.id, owner.id, lesson.id, "detailed")
