"""
T1, mandate test case 2: a resource ID that is real and IS owned by the
caller, but belongs to a DIFFERENT course than the one named in the URL
path. Distinct from ordinary cross-user ownership (already covered
extensively elsewhere, see the Phase 6 fact-finding report) -- this is
"you own both courses, but this lesson isn't in *this* one."
"""
import uuid

import pytest

from app.modules.courses.models import Course
from app.modules.curriculum.models import CourseVersion, CourseVersionStatus, Lesson, Module
from tests.conftest import auth_headers


@pytest.fixture()
def two_owned_courses_with_lessons(db_session, owner):
    def make_course_with_lesson(title):
        course = Course(owner_id=owner.id, title=title)
        db_session.add(course)
        db_session.commit()
        version = CourseVersion(
            course_id=course.id, owner_id=owner.id, version_number=1, status=CourseVersionStatus.READY.value,
        )
        db_session.add(version)
        db_session.flush()
        module = Module(course_version_id=version.id, position=0, title="M1")
        db_session.add(module)
        db_session.flush()
        lesson = Lesson(module_id=module.id, position=0, title="L1", objective="obj")
        db_session.add(lesson)
        db_session.commit()
        course.active_version_id = version.id
        db_session.commit()
        return course, lesson

    course_a, lesson_a = make_course_with_lesson("Course A")
    course_b, lesson_b = make_course_with_lesson("Course B")
    return course_a, lesson_a, course_b, lesson_b


class TestLessonRenameCrossCourse:
    def test_renaming_via_the_wrong_course_path_is_rejected(
        self, client, owner, two_owned_courses_with_lessons
    ):
        course_a, lesson_a, course_b, lesson_b = two_owned_courses_with_lessons
        # lesson_b is real and IS owned by `owner` -- just not part of course_a.
        resp = client.put(
            f"/api/v1/courses/{course_a.id}/structure",
            json={"lesson_renames": [{"lesson_id": str(lesson_b.id), "title": "Hijacked"}]},
            headers=auth_headers(owner.email),
        )
        assert resp.status_code == 404

    def test_the_lessons_actual_course_is_unaffected(
        self, client, owner, db_session, two_owned_courses_with_lessons
    ):
        course_a, lesson_a, course_b, lesson_b = two_owned_courses_with_lessons
        client.put(
            f"/api/v1/courses/{course_a.id}/structure",
            json={"lesson_renames": [{"lesson_id": str(lesson_b.id), "title": "Hijacked"}]},
            headers=auth_headers(owner.email),
        )
        db_session.refresh(lesson_b)
        assert lesson_b.title == "L1"


class TestLessonContentCrossCourse:
    def test_generating_content_via_the_wrong_course_path_is_rejected(
        self, client, owner, two_owned_courses_with_lessons
    ):
        course_a, lesson_a, course_b, lesson_b = two_owned_courses_with_lessons
        resp = client.get(
            f"/api/v1/courses/{course_a.id}/lessons/{lesson_b.id}/content?format=detailed",
            headers=auth_headers(owner.email),
        )
        assert resp.status_code == 404
