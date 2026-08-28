"""
CurriculumService tests: activation gating, and the full generate_version
pipeline exercised end to end against fake gateways (no network).
"""
from unittest.mock import patch

import pytest

from app.modules.courses.models import Course
from app.modules.curriculum.models import CourseVersion, CourseVersionStatus
from app.modules.curriculum.service import (
    CurriculumNotFound,
    CurriculumService,
    VersionNotReady,
)
from app.modules.documents.chunk_models import Chunk
from app.modules.documents.models import Document
from app.services.embedding.fake import FakeEmbeddingGateway
from app.services.generation.fake import FakeGenerationGateway


@pytest.fixture()
def course(db_session, owner):
    c = Course(owner_id=owner.id, title="OS Course")
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    return c


def add_chunks(db_session, course, owner, sections):
    """sections: list of (heading_path, [texts])."""
    doc = Document(
        course_id=course.id, owner_id=owner.id, filename="notes.txt",
        storage_path="/dev/null", checksum_sha256="c" * 64,
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    import uuid as _uuid

    position = 0
    chunks = []
    for heading, texts in sections:
        for text in texts:
            chunk = Chunk(
                id=_uuid.uuid4(), document_id=doc.id, course_id=course.id, owner_id=owner.id,
                position=position, heading_path=heading, text=text, token_count=len(text.split()),
            )
            db_session.add(chunk)
            chunks.append(chunk)
            position += 1
    db_session.commit()
    return chunks


def one_concept_gateway(name="Deadlock", importance=0.9):
    return FakeGenerationGateway().set_default(
        f'{{"concepts": [{{"name": "{name}", "definition": "def", '
        f'"importance": {importance}, "bloom_level": "understand"}}]}}'
    )


class TestGenerateVersionHappyPath:
    def test_produces_a_ready_version_with_a_concept_lesson_and_blueprint(
        self, db_session, owner, course
    ):
        add_chunks(db_session, course, owner, [("Intro > Basics", ["Some text about deadlocks."])])
        service = CurriculumService(db_session, one_concept_gateway(), FakeEmbeddingGateway())

        version = service.generate_version(course.id, owner.id)

        assert version.status == CourseVersionStatus.READY.value
        assert version.validation_errors == []

    def test_does_not_touch_course_active_version(self, db_session, owner, course):
        add_chunks(db_session, course, owner, [("Intro", ["text"])])
        service = CurriculumService(db_session, one_concept_gateway(), FakeEmbeddingGateway())

        service.generate_version(course.id, owner.id)
        db_session.refresh(course)

        assert course.active_version_id is None

    def test_other_user_cannot_generate_for_your_course(self, db_session, owner, other_user, course):
        add_chunks(db_session, course, owner, [("Intro", ["text"])])
        service = CurriculumService(db_session, one_concept_gateway(), FakeEmbeddingGateway())

        with pytest.raises(CurriculumNotFound):
            service.generate_version(course.id, other_user.id)


class TestActivationGating:
    def test_cannot_activate_a_failed_version(self, db_session, owner, course):
        """A version with an important, unblueprinted concept fails
        validation; activation must refuse it."""
        add_chunks(db_session, course, owner, [("Intro", ["text about something important"])])
        service = CurriculumService(db_session, one_concept_gateway(importance=0.9), FakeEmbeddingGateway())
        version = service.generate_version(course.id, owner.id)
        # Force it into a failed state deterministically, independent of
        # whether the real pipeline happened to produce a failure this run.
        version.status = CourseVersionStatus.FAILED.value
        version.validation_errors = ["forced failure for this test"]
        db_session.commit()

        with pytest.raises(VersionNotReady):
            service.activate_version(course.id, owner.id, version.id)

        db_session.refresh(course)
        assert course.active_version_id is None

    def test_activating_a_ready_version_sets_the_pointer(self, db_session, owner, course):
        add_chunks(db_session, course, owner, [("Intro", ["text"])])
        service = CurriculumService(db_session, one_concept_gateway(), FakeEmbeddingGateway())
        version = service.generate_version(course.id, owner.id)
        assert version.status == CourseVersionStatus.READY.value

        service.activate_version(course.id, owner.id, version.id)
        db_session.refresh(course)

        assert course.active_version_id == version.id

    def test_generating_alone_never_activates(self, db_session, owner, course):
        """The explicit-confirmation requirement: generate_version and
        activate_version are two separate calls."""
        add_chunks(db_session, course, owner, [("Intro", ["text"])])
        service = CurriculumService(db_session, one_concept_gateway(), FakeEmbeddingGateway())

        service.generate_version(course.id, owner.id)
        db_session.refresh(course)

        assert course.active_version_id is None

    def test_failed_commit_leaves_the_previous_version_active(self, db_session, owner, course):
        """Simulates a failure immediately before the pointer swap commits;
        the previously active version must remain active afterward."""
        add_chunks(db_session, course, owner, [("Intro", ["text"])])
        service = CurriculumService(db_session, one_concept_gateway(), FakeEmbeddingGateway())

        v1 = service.generate_version(course.id, owner.id)
        service.activate_version(course.id, owner.id, v1.id)
        db_session.refresh(course)
        assert course.active_version_id == v1.id

        v2 = service.generate_version(course.id, owner.id)
        assert v2.status == CourseVersionStatus.READY.value

        with patch.object(db_session, "commit", side_effect=RuntimeError("simulated failure")):
            with pytest.raises(RuntimeError):
                service.activate_version(course.id, owner.id, v2.id)

        db_session.rollback()
        refreshed = db_session.query(Course).filter(Course.id == course.id).first()
        assert refreshed.active_version_id == v1.id

    def test_nonexistent_version_id_is_not_found(self, db_session, owner, course):
        import uuid

        service = CurriculumService(db_session, one_concept_gateway(), FakeEmbeddingGateway())
        with pytest.raises(CurriculumNotFound):
            service.activate_version(course.id, owner.id, uuid.uuid4())

    def test_other_user_cannot_activate_your_version(self, db_session, owner, other_user, course):
        add_chunks(db_session, course, owner, [("Intro", ["text"])])
        service = CurriculumService(db_session, one_concept_gateway(), FakeEmbeddingGateway())
        version = service.generate_version(course.id, owner.id)

        with pytest.raises(CurriculumNotFound):
            service.activate_version(course.id, other_user.id, version.id)


class TestRegeneration:
    def test_regenerating_creates_a_new_version_row(self, db_session, owner, course):
        add_chunks(db_session, course, owner, [("Intro", ["text"])])
        service = CurriculumService(db_session, one_concept_gateway(), FakeEmbeddingGateway())

        v1 = service.generate_version(course.id, owner.id)
        v2 = service.generate_version(course.id, owner.id)

        assert v1.id != v2.id
        assert v2.version_number == v1.version_number + 1

    def test_the_original_version_row_is_untouched_by_regeneration(self, db_session, owner, course):
        add_chunks(db_session, course, owner, [("Intro", ["text"])])
        service = CurriculumService(db_session, one_concept_gateway(), FakeEmbeddingGateway())

        v1 = service.generate_version(course.id, owner.id)
        v1_status_before = v1.status
        v1_errors_before = list(v1.validation_errors)

        service.generate_version(course.id, owner.id)
        db_session.refresh(v1)

        assert v1.status == v1_status_before
        assert v1.validation_errors == v1_errors_before

    def test_carryover_map_records_a_carried_concept_by_canonical_key(self, db_session, owner, course):
        add_chunks(db_session, course, owner, [("Intro", ["text"])])
        service = CurriculumService(db_session, one_concept_gateway("Deadlock"), FakeEmbeddingGateway())

        v1 = service.generate_version(course.id, owner.id)
        service.activate_version(course.id, owner.id, v1.id)

        # Same concept name -> same canonical_key -> carried.
        v2 = service.generate_version(course.id, owner.id)

        assert v2.concept_carryover_map is not None
        statuses = [entry["status"] for entry in v2.concept_carryover_map.values()]
        assert "carried" in statuses

    def test_carryover_map_marks_a_genuinely_new_concept_as_new_not_matched(
        self, db_session, owner, course
    ):
        add_chunks(db_session, course, owner, [("Intro", ["text"])])
        service_v1 = CurriculumService(db_session, one_concept_gateway("Deadlock"), FakeEmbeddingGateway())
        v1 = service_v1.generate_version(course.id, owner.id)
        service_v1.activate_version(course.id, owner.id, v1.id)

        # A different concept name/definition/embedding -> should not match v1's.
        different_gateway = FakeGenerationGateway().set_default(
            '{"concepts": [{"name": "Garbage Collection", "definition": "unrelated", "importance": 0.9}]}'
        )
        different_embeddings = FakeEmbeddingGateway()
        service_v2 = CurriculumService(db_session, different_gateway, different_embeddings)
        v2 = service_v2.generate_version(course.id, owner.id)

        statuses = [entry["status"] for entry in v2.concept_carryover_map.values()]
        assert "new" in statuses

    def test_first_version_has_no_carryover_map(self, db_session, owner, course):
        add_chunks(db_session, course, owner, [("Intro", ["text"])])
        service = CurriculumService(db_session, one_concept_gateway(), FakeEmbeddingGateway())

        v1 = service.generate_version(course.id, owner.id)

        assert v1.concept_carryover_map is None
