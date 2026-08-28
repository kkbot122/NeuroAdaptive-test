import pytest

from app.modules.courses.models import Course
from app.modules.curriculum.models import Concept, ConceptPrerequisite, CourseVersion, CourseVersionStatus
from tests.conftest import auth_headers


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
        canonical_key="virtual memory", name="Virtual Memory", definition="Uses disk as RAM.",
        importance=0.9,
    )
    concept_b = Concept(
        course_id=course.id, course_version_id=version.id, owner_id=owner.id,
        canonical_key="deadlock", name="Deadlock", definition="A circular wait.", importance=0.8,
    )
    db_session.add_all([concept_a, concept_b])
    db_session.flush()
    db_session.add(
        ConceptPrerequisite(
            course_id=course.id, course_version_id=version.id,
            prerequisite_concept_id=concept_a.id, dependent_concept_id=concept_b.id,
        )
    )
    db_session.commit()

    course.active_version_id = version.id
    db_session.commit()
    db_session.refresh(course)
    return course, version, concept_a, concept_b


class TestDiagnostic:
    def test_generates_one_mcq_per_sampled_concept(self, client, owner, fake_generation, course_with_concepts):
        course, version, concept_a, concept_b = course_with_concepts
        fake_generation.when_prompt_contains(
            "Virtual Memory",
            '{"questions": ['
            '{"concept_name": "Virtual Memory", "prompt": "What is VM?", '
            '"options": ["A", "B"], "correct_answer": "A", "difficulty": 0.3}, '
            '{"concept_name": "Deadlock", "prompt": "What is a deadlock?", '
            '"options": ["C", "D"], "correct_answer": "C", "difficulty": 0.4}]}',
        )
        resp = client.post(
            f"/api/v1/courses/{course.id}/diagnostic", json={}, headers=auth_headers(owner.email)
        )
        assert resp.status_code == 200
        questions = resp.json()
        assert len(questions) == 2
        for q in questions:
            assert "correct_answer" not in q
            assert "rubric" not in q

    def test_unknown_course_is_404(self, client, owner):
        import uuid

        resp = client.post(
            f"/api/v1/courses/{uuid.uuid4()}/diagnostic", json={}, headers=auth_headers(owner.email)
        )
        assert resp.status_code == 404


class TestAttemptsAndReport:
    def test_answering_a_question_updates_the_mastery_report(
        self, client, owner, fake_generation, course_with_concepts
    ):
        course, version, concept_a, concept_b = course_with_concepts
        fake_generation.when_prompt_contains(
            "Virtual Memory",
            '{"questions": ['
            '{"concept_name": "Virtual Memory", "prompt": "What is VM?", '
            '"options": ["A", "B"], "correct_answer": "A", "difficulty": 0.3}]}',
        )
        diagnostic = client.post(
            f"/api/v1/courses/{course.id}/diagnostic",
            json={"max_questions": 1},
            headers=auth_headers(owner.email),
        ).json()
        question_id = diagnostic[0]["id"]

        attempt = client.post(
            f"/api/v1/questions/{question_id}/attempts",
            json={"given_answer": "A"},
            headers=auth_headers(owner.email),
        )
        assert attempt.status_code == 201
        assert attempt.json()["correctness"] == 1.0

        report = client.get(
            f"/api/v1/courses/{course.id}/mastery-report", headers=auth_headers(owner.email)
        ).json()
        answered = next(r for r in report if r["concept_id"] == str(concept_a.id))
        assert answered["band"] != "Not assessed"
        assert answered.get("raw") is None  # present-but-null: MasteryReportRow always declares the field

    def test_skipping_a_concept_leaves_it_not_assessed(self, client, owner, course_with_concepts):
        """No attempt ever submitted for concept_b -- the honest default,
        not a fabricated baseline."""
        course, version, concept_a, concept_b = course_with_concepts
        report = client.get(
            f"/api/v1/courses/{course.id}/mastery-report", headers=auth_headers(owner.email)
        ).json()
        skipped = next(r for r in report if r["concept_id"] == str(concept_b.id))
        assert skipped["band"] == "Not assessed"

    def test_report_include_raw_separates_raw_from_band(self, client, owner, course_with_concepts):
        course, *_ = course_with_concepts
        report = client.get(
            f"/api/v1/courses/{course.id}/mastery-report?include_raw=true",
            headers=auth_headers(owner.email),
        ).json()
        assert all("band" in row for row in report)
        assert all(row.get("raw") is not None for row in report)

    def test_another_users_report_is_404(self, client, other_user, course_with_concepts):
        course, *_ = course_with_concepts
        resp = client.get(
            f"/api/v1/courses/{course.id}/mastery-report", headers=auth_headers(other_user.email)
        )
        assert resp.status_code == 404

    def test_attempting_another_users_question_is_404(
        self, client, owner, other_user, fake_generation, course_with_concepts
    ):
        course, version, concept_a, _ = course_with_concepts
        fake_generation.when_prompt_contains(
            "Virtual Memory",
            '{"questions": [{"concept_name": "Virtual Memory", "prompt": "What is VM?", '
            '"options": ["A", "B"], "correct_answer": "A", "difficulty": 0.3}]}',
        )
        diagnostic = client.post(
            f"/api/v1/courses/{course.id}/diagnostic",
            json={"max_questions": 1},
            headers=auth_headers(owner.email),
        ).json()
        question_id = diagnostic[0]["id"]

        resp = client.post(
            f"/api/v1/questions/{question_id}/attempts",
            json={"given_answer": "A"},
            headers=auth_headers(other_user.email),
        )
        assert resp.status_code == 404
