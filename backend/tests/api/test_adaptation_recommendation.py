import uuid

import pytest

from app.modules.courses.models import Course
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
from app.modules.mastery.models import MasteryEvent
from tests.conftest import auth_headers


@pytest.fixture()
def course_setup(db_session, owner):
    course = Course(owner_id=owner.id, title="OS Course")
    db_session.add(course)
    db_session.commit()
    db_session.refresh(course)

    version = CourseVersion(
        course_id=course.id, owner_id=owner.id, version_number=1, status=CourseVersionStatus.READY.value,
    )
    db_session.add(version)
    db_session.flush()

    concept_x = Concept(
        course_id=course.id, course_version_id=version.id, owner_id=owner.id,
        canonical_key="x", name="Deadlock Detection", definition="def x", importance=0.8,
    )
    concept_y = Concept(
        course_id=course.id, course_version_id=version.id, owner_id=owner.id,
        canonical_key="y", name="Mutual Exclusion", definition="def y", importance=0.8,
    )
    db_session.add_all([concept_x, concept_y])
    db_session.flush()
    db_session.add(
        ConceptPrerequisite(
            course_id=course.id, course_version_id=version.id,
            prerequisite_concept_id=concept_y.id, dependent_concept_id=concept_x.id,
            strength=EdgeStrength.HARD.value,
        )
    )
    module = Module(course_version_id=version.id, position=0, title="Course Module")
    db_session.add(module)
    db_session.flush()
    lesson = Lesson(module_id=module.id, position=0, title="Mutual Exclusion Basics")
    db_session.add(lesson)
    db_session.flush()
    db_session.add(LessonConcept(lesson_id=lesson.id, concept_id=concept_y.id, weight=1.0))
    db_session.commit()

    course.active_version_id = version.id
    db_session.commit()
    db_session.refresh(course)
    return course, version, concept_x, concept_y


def set_mastery(db_session, owner, concept_id, mastery):
    db_session.add(
        MasteryEvent(
            owner_id=owner.id, concept_id=concept_id, course_id=uuid.uuid4(), course_version_id=uuid.uuid4(),
            correctness=mastery, evidence_weight_base=1000.0,
        )
    )
    db_session.commit()


class TestNextActivityContract:
    def test_response_has_decision_id_recommended_reason_and_alternatives(
        self, client, owner, course_setup
    ):
        course, *_ = course_setup
        resp = client.get(f"/api/v1/courses/{course.id}/next-activity", headers=auth_headers(owner.email))
        assert resp.status_code == 200
        body = resp.json()
        assert "decision_id" in body
        assert "reason" in body["recommended"] and body["recommended"]["reason"]
        assert isinstance(body["alternatives"], list)

    def test_unknown_course_is_404(self, client, owner):
        resp = client.get(f"/api/v1/courses/{uuid.uuid4()}/next-activity", headers=auth_headers(owner.email))
        assert resp.status_code == 404

    def test_another_users_course_is_404(self, client, other_user, course_setup):
        course, *_ = course_setup
        resp = client.get(
            f"/api/v1/courses/{course.id}/next-activity", headers=auth_headers(other_user.email)
        )
        assert resp.status_code == 404


class TestEndToEndRemediation:
    def test_demonstrated_concept_weakness_surfaces_a_specific_remediation_reason(self, client, owner, db_session, course_setup):
        course, version, _concept_x, concept_y = course_setup
        set_mastery(db_session, owner, concept_y.id, 0.2)

        resp = client.get(f"/api/v1/courses/{course.id}/next-activity", headers=auth_headers(owner.email))
        body = resp.json()
        assert body["recommended"]["activity_type"] == "PREREQUISITE_REMEDIATION"
        assert body["recommended"]["concept_ids"] == [str(concept_y.id)]
        assert concept_y.name in body["recommended"]["reason"]
        assert "Needs attention" in body["recommended"]["reason"]
        assert "more supporting evidence" in body["recommended"]["reason"]


class TestPresentationAffinityEndpoints:
    def test_recording_an_outcome_moves_effectiveness(self, client, owner):
        resp = client.post(
            "/api/v1/presentation-affinity/outcome",
            json={"format": "worked_example", "success": True},
            headers=auth_headers(owner.email),
        )
        assert resp.status_code == 200
        assert resp.json()["effectiveness"] > 0.5

    def test_manual_switch_is_recorded(self, client, owner):
        resp = client.post(
            "/api/v1/presentation-affinity/switch",
            json={"from_format": "diagram", "to_format": "worked_example"},
            headers=auth_headers(owner.email),
        )
        assert resp.status_code == 200
