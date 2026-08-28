"""
Repository/service-layer tests for validate_course_version(): hand-built DB
rows exercising each check directly, rather than relying on the full
generation pipeline to happen to produce a failing scenario.
"""
import uuid

import pytest

from app.modules.courses.models import Course
from app.modules.curriculum.models import (
    AssessmentBlueprint,
    Concept,
    ConceptPrerequisite,
    ConceptSource,
    CourseVersion,
    Lesson,
    LessonConcept,
    Module,
)
from app.modules.curriculum.validation import validate_course_version
from app.modules.documents.chunk_models import Chunk
from app.modules.documents.models import Document


@pytest.fixture()
def course_and_version(db_session, owner):
    course = Course(owner_id=owner.id, title="Test Course")
    db_session.add(course)
    db_session.commit()
    db_session.refresh(course)

    version = CourseVersion(course_id=course.id, owner_id=owner.id, version_number=1, validation_errors=[])
    db_session.add(version)
    db_session.commit()
    db_session.refresh(version)
    return course, version


def make_concept(db_session, course, version, owner, importance=0.5, **kwargs):
    concept = Concept(
        course_id=course.id,
        course_version_id=version.id,
        owner_id=owner.id,
        canonical_key=kwargs.get("name", "concept").lower(),
        name=kwargs.get("name", "Concept"),
        definition=kwargs.get("definition", "A definition."),
        aliases=[],
        importance=importance,
    )
    db_session.add(concept)
    db_session.commit()
    db_session.refresh(concept)
    return concept


def make_lesson(db_session, version):
    module = Module(course_version_id=version.id, position=0, title="Module 1")
    db_session.add(module)
    db_session.commit()
    db_session.refresh(module)
    lesson = Lesson(module_id=module.id, position=0, title="Lesson 1")
    db_session.add(lesson)
    db_session.commit()
    db_session.refresh(lesson)
    return lesson


class TestConceptLessonCoverage:
    def test_concept_with_no_lesson_fails_validation(self, db_session, owner, course_and_version):
        course, version = course_and_version
        make_concept(db_session, course, version, owner)  # no LessonConcept row at all

        result = validate_course_version(db_session, version)

        assert not result.is_valid
        assert any("does not belong to any lesson" in e for e in result.errors)

    def test_concept_with_a_lesson_passes_that_check(self, db_session, owner, course_and_version):
        course, version = course_and_version
        concept = make_concept(db_session, course, version, owner)
        lesson = make_lesson(db_session, version)
        db_session.add(LessonConcept(lesson_id=lesson.id, concept_id=concept.id, role="INTRODUCES", weight=0.5))
        db_session.commit()

        result = validate_course_version(db_session, version)

        assert not any("does not belong to any lesson" in e for e in result.errors)

    def test_no_concepts_at_all_is_not_a_coverage_failure(self, db_session, course_and_version):
        _, version = course_and_version
        result = validate_course_version(db_session, version)
        assert not any("does not belong to any lesson" in e for e in result.errors)


class TestCitationResolution:
    def test_source_pointing_to_a_nonexistent_chunk_fails_validation(
        self, db_session, owner, course_and_version
    ):
        course, version = course_and_version
        concept = make_concept(db_session, course, version, owner)
        db_session.add(
            ConceptSource(
                concept_id=concept.id,
                chunk_id=uuid.uuid4(),  # does not exist
                course_id=course.id,
                owner_id=owner.id,
            )
        )
        db_session.commit()

        result = validate_course_version(db_session, version)

        assert not result.is_valid
        assert any("does not exist or does not belong" in e for e in result.errors)

    def test_source_pointing_to_another_courses_chunk_fails_validation(
        self, db_session, owner, course_and_version
    ):
        course, version = course_and_version
        other_course = Course(owner_id=owner.id, title="Other Course")
        db_session.add(other_course)
        db_session.commit()
        db_session.refresh(other_course)

        other_doc = Document(
            course_id=other_course.id, owner_id=owner.id, filename="x.txt",
            storage_path="/dev/null", checksum_sha256="a" * 64,
        )
        db_session.add(other_doc)
        db_session.commit()
        db_session.refresh(other_doc)

        foreign_chunk = Chunk(
            id=uuid.uuid4(), document_id=other_doc.id, course_id=other_course.id, owner_id=owner.id,
            position=0, text="foreign text",
        )
        db_session.add(foreign_chunk)
        db_session.commit()
        db_session.refresh(foreign_chunk)

        concept = make_concept(db_session, course, version, owner)
        db_session.add(
            ConceptSource(
                concept_id=concept.id, chunk_id=foreign_chunk.id,
                course_id=course.id, owner_id=owner.id,
            )
        )
        db_session.commit()

        result = validate_course_version(db_session, version)

        assert not result.is_valid

    def test_source_pointing_to_a_real_owned_chunk_passes(self, db_session, owner, course_and_version):
        course, version = course_and_version
        doc = Document(
            course_id=course.id, owner_id=owner.id, filename="x.txt",
            storage_path="/dev/null", checksum_sha256="b" * 64,
        )
        db_session.add(doc)
        db_session.commit()
        db_session.refresh(doc)

        chunk = Chunk(id=uuid.uuid4(), document_id=doc.id, course_id=course.id, owner_id=owner.id, position=0, text="real text")
        db_session.add(chunk)
        db_session.commit()
        db_session.refresh(chunk)

        concept = make_concept(db_session, course, version, owner)
        db_session.add(
            ConceptSource(concept_id=concept.id, chunk_id=chunk.id, course_id=course.id, owner_id=owner.id)
        )
        db_session.commit()

        result = validate_course_version(db_session, version)

        assert not any("does not exist" in e for e in result.errors)


class TestAcyclicity:
    def test_cyclic_prerequisite_graph_fails_validation(self, db_session, owner, course_and_version):
        course, version = course_and_version
        a = make_concept(db_session, course, version, owner, name="A")
        b = make_concept(db_session, course, version, owner, name="B")
        db_session.add_all([
            ConceptPrerequisite(course_id=course.id, course_version_id=version.id,
                                 prerequisite_concept_id=a.id, dependent_concept_id=b.id,
                                 strength="SOFT", confidence=0.5),
            ConceptPrerequisite(course_id=course.id, course_version_id=version.id,
                                 prerequisite_concept_id=b.id, dependent_concept_id=a.id,
                                 strength="SOFT", confidence=0.5),
        ])
        db_session.commit()

        result = validate_course_version(db_session, version)

        assert not result.is_valid
        assert any("cycle" in e for e in result.errors)


class TestAssessmentCoverage:
    def test_important_concept_with_no_blueprint_fails_validation(
        self, db_session, owner, course_and_version
    ):
        course, version = course_and_version
        make_concept(db_session, course, version, owner, importance=0.9, name="Important")

        result = validate_course_version(db_session, version)

        assert not result.is_valid
        assert any("no assessment coverage" in e for e in result.errors)

    def test_unimportant_concept_needs_no_blueprint(self, db_session, owner, course_and_version):
        course, version = course_and_version
        make_concept(db_session, course, version, owner, importance=0.2, name="Minor")

        result = validate_course_version(db_session, version)

        assert not any("no assessment coverage" in e for e in result.errors)

    def test_important_concept_with_a_blueprint_passes(self, db_session, owner, course_and_version):
        course, version = course_and_version
        concept = make_concept(db_session, course, version, owner, importance=0.9, name="Important")
        db_session.add(
            AssessmentBlueprint(
                course_version_id=version.id, concept_id=concept.id,
                question_type="MCQ", difficulty="medium", target_count=1,
            )
        )
        db_session.commit()

        result = validate_course_version(db_session, version)

        assert not any("no assessment coverage" in e for e in result.errors)


class TestVersionIsolation:
    def test_another_versions_concepts_do_not_affect_this_versions_validation(
        self, db_session, owner, course_and_version
    ):
        """A stale or failed prior version's orphan concept must not leak
        into this version's coverage check."""
        course, version = course_and_version
        other_version = CourseVersion(
            course_id=course.id, owner_id=owner.id, version_number=2, validation_errors=[]
        )
        db_session.add(other_version)
        db_session.commit()
        db_session.refresh(other_version)
        make_concept(db_session, course, other_version, owner)  # orphan, but in a DIFFERENT version

        result = validate_course_version(db_session, version)

        assert result.is_valid
