import uuid

import pytest

from app.modules.courses.models import Course
from app.modules.curriculum.models import Concept, CourseVersion, CourseVersionStatus
from app.modules.mastery.models import MasteryEvent, QuestionAttempt
from app.modules.mastery.service import InvalidQuestionWeights, MasteryNotFound, MasteryService
from app.services.embedding.fake import FakeEmbeddingGateway
from app.services.generation.fake import FakeGenerationGateway


@pytest.fixture()
def course_with_concepts(db_session, owner):
    course = Course(owner_id=owner.id, title="OS Course")
    db_session.add(course)
    db_session.commit()
    db_session.refresh(course)

    version = CourseVersion(
        course_id=course.id, owner_id=owner.id, version_number=1,
        status=CourseVersionStatus.READY.value,
    )
    db_session.add(version)
    db_session.flush()

    concept_a = Concept(
        course_id=course.id, course_version_id=version.id, owner_id=owner.id,
        canonical_key="concept a", name="Concept A", definition="def a", importance=0.9,
    )
    concept_b = Concept(
        course_id=course.id, course_version_id=version.id, owner_id=owner.id,
        canonical_key="concept b", name="Concept B", definition="def b", importance=0.5,
    )
    db_session.add_all([concept_a, concept_b])
    db_session.commit()

    course.active_version_id = version.id
    db_session.commit()
    db_session.refresh(course)
    return course, version, concept_a, concept_b


def make_service(db_session):
    return MasteryService(db_session, FakeGenerationGateway(), FakeEmbeddingGateway())


class TestCreateQuestion:
    def test_rejects_weights_not_summing_to_one(self, db_session, owner, course_with_concepts):
        course, version, concept_a, concept_b = course_with_concepts
        service = make_service(db_session)
        with pytest.raises(InvalidQuestionWeights):
            service.create_question(
                course.id, version.id, owner.id, "MCQ", "?",
                concept_weights={concept_a.id: 0.7, concept_b.id: 0.1},
                options=["x", "y"], correct_answer="x",
            )

    def test_accepts_weights_summing_to_one_and_persists_links(self, db_session, owner, course_with_concepts):
        course, version, concept_a, concept_b = course_with_concepts
        service = make_service(db_session)
        question = service.create_question(
            course.id, version.id, owner.id, "MCQ", "?",
            concept_weights={concept_a.id: 0.7, concept_b.id: 0.3},
            options=["x", "y"], correct_answer="x",
        )
        assert question.version == 1
        assert question.supersedes_question_id is None


class TestSubmitAttempt:
    def test_creates_mastery_events_split_by_concept_weight(self, db_session, owner, course_with_concepts):
        course, version, concept_a, concept_b = course_with_concepts
        service = make_service(db_session)
        question = service.create_question(
            course.id, version.id, owner.id, "MCQ", "?",
            concept_weights={concept_a.id: 0.7, concept_b.id: 0.3},
            options=["x", "y"], correct_answer="x",
        )
        service.submit_attempt(question.id, owner.id, given_answer="x")

        events = db_session.query(MasteryEvent).filter(MasteryEvent.question_attempt_id.isnot(None)).all()
        assert len(events) == 2
        weight_a = next(e.evidence_weight_base for e in events if e.concept_id == concept_a.id)
        weight_b = next(e.evidence_weight_base for e in events if e.concept_id == concept_b.id)
        assert weight_a > weight_b

    def test_updating_concept_a_mastery_does_not_touch_concept_b(self, db_session, owner, course_with_concepts):
        course, version, concept_a, concept_b = course_with_concepts
        service = make_service(db_session)
        question = service.create_question(
            course.id, version.id, owner.id, "MCQ", "?",
            concept_weights={concept_a.id: 1.0},
            options=["x", "y"], correct_answer="x",
        )
        service.submit_attempt(question.id, owner.id, given_answer="x")

        state_a = service.get_concept_mastery(owner.id, concept_a.id)
        state_b = service.get_concept_mastery(owner.id, concept_b.id)
        assert state_a.evidence_weight_total > 0
        assert state_b.evidence_weight_total == 0  # untouched -- still the honest prior

    def test_unknown_question_raises_not_found(self, db_session, owner):
        service = make_service(db_session)
        with pytest.raises(MasteryNotFound):
            service.submit_attempt(uuid.uuid4(), owner.id, given_answer="x")

    def test_another_users_question_is_not_found(self, db_session, owner, other_user, course_with_concepts):
        course, version, concept_a, _ = course_with_concepts
        service = make_service(db_session)
        question = service.create_question(
            course.id, version.id, owner.id, "MCQ", "?",
            concept_weights={concept_a.id: 1.0}, options=["x", "y"], correct_answer="x",
        )
        with pytest.raises(MasteryNotFound):
            service.submit_attempt(question.id, other_user.id, given_answer="x")


class TestUserIsolation:
    def test_two_users_mastery_for_the_same_concept_are_independent(
        self, db_session, owner, other_user, course_with_concepts
    ):
        course, version, concept_a, _ = course_with_concepts
        service = make_service(db_session)
        question = service.create_question(
            course.id, version.id, owner.id, "MCQ", "?",
            concept_weights={concept_a.id: 1.0}, options=["x", "y"], correct_answer="x",
        )
        service.submit_attempt(question.id, owner.id, given_answer="x")

        owner_state = service.get_concept_mastery(owner.id, concept_a.id)
        other_state = service.get_concept_mastery(other_user.id, concept_a.id)
        assert owner_state.evidence_weight_total > 0
        assert other_state.evidence_weight_total == 0


class TestQuestionVersioning:
    def test_superseding_a_question_does_not_change_a_historical_attempts_version(
        self, db_session, owner, course_with_concepts
    ):
        course, version, concept_a, _ = course_with_concepts
        service = make_service(db_session)
        question = service.create_question(
            course.id, version.id, owner.id, "MCQ", "?",
            concept_weights={concept_a.id: 1.0}, options=["x", "y"], correct_answer="x",
        )
        attempt = service.submit_attempt(question.id, owner.id, given_answer="x")
        assert attempt.question_id == question.id
        assert attempt.question_version == 1

        service.supersede_question(question.id, owner.id, prompt="Improved prompt?")

        db_session.refresh(attempt)
        assert attempt.question_id == question.id
        assert attempt.question_version == 1  # untouched by the supersession

    def test_superseded_question_creates_a_new_row_at_version_plus_one(
        self, db_session, owner, course_with_concepts
    ):
        course, version, concept_a, _ = course_with_concepts
        service = make_service(db_session)
        question = service.create_question(
            course.id, version.id, owner.id, "MCQ", "?",
            concept_weights={concept_a.id: 1.0}, options=["x", "y"], correct_answer="x",
        )
        new_question = service.supersede_question(question.id, owner.id, prompt="Improved prompt?")
        assert new_question.id != question.id
        assert new_question.version == 2
        assert new_question.supersedes_question_id == question.id


class TestMasteryReport:
    def test_report_is_bands_by_default_no_raw_field(self, db_session, owner, course_with_concepts):
        course, version, concept_a, concept_b = course_with_concepts
        service = make_service(db_session)
        report = service.get_mastery_report(course.id, owner.id)
        assert all("raw" not in row for row in report)
        assert all(row["band"] == "Not assessed" for row in report)

    def test_include_raw_nests_raw_values_separately(self, db_session, owner, course_with_concepts):
        course, version, concept_a, concept_b = course_with_concepts
        service = make_service(db_session)
        question = service.create_question(
            course.id, version.id, owner.id, "MCQ", "?",
            concept_weights={concept_a.id: 1.0}, options=["x", "y"], correct_answer="x",
        )
        service.submit_attempt(question.id, owner.id, given_answer="x")
        report = service.get_mastery_report(course.id, owner.id, include_raw=True)
        row = next(r for r in report if r["concept_id"] == str(concept_a.id))
        assert "mastery" in row["raw"] and "uncertainty" in row["raw"]

    def test_report_for_another_users_course_is_not_found(self, db_session, other_user, course_with_concepts):
        course, _, _, _ = course_with_concepts
        service = make_service(db_session)
        with pytest.raises(MasteryNotFound):
            service.get_mastery_report(course.id, other_user.id)
