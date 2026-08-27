"""
Ownership isolation for the course surface.

The build mandate's first non-negotiable: every new table and query enforces
owner filters inside the service layer, never only in the route. These tests
exercise the HTTP surface with two real users and assert that neither can
reach the other's course by any verb.

frozen-scope.md: "Every course, document, chunk, generated artifact,
assessment, attempt, mastery state, object, graph projection, and retrieval
query is scoped to the authenticated owner and course."
"""
import pytest

from app.modules.courses.models import Course
from tests.conftest import auth_headers

COURSES = "/api/v1/courses"


@pytest.fixture()
def owner_course(client, owner):
    response = client.post(
        COURSES,
        json={"title": "Owner's Course", "goal": "learn compilers", "starting_confidence": 3},
        headers=auth_headers(owner.email),
    )
    assert response.status_code == 201
    return response.json()


class TestCreate:
    def test_creates_for_the_authenticated_user(self, client, owner, db_session, owner_course):
        course = db_session.query(Course).one()
        assert course.owner_id == owner.id
        assert course.title == "Owner's Course"
        assert course.status == "DRAFT"

    def test_owner_id_in_the_body_is_ignored(self, client, owner, other_user, db_session):
        """Identity is never taken from the payload."""
        client.post(
            COURSES,
            json={"title": "X", "owner_id": other_user.id},
            headers=auth_headers(owner.email),
        )
        assert db_session.query(Course).one().owner_id == owner.id

    def test_anonymous_cannot_create(self, client):
        assert client.post(COURSES, json={"title": "X"}).status_code == 422

    def test_rejects_blank_title(self, client, owner):
        response = client.post(COURSES, json={"title": ""}, headers=auth_headers(owner.email))
        assert response.status_code == 422

    def test_rejects_out_of_range_confidence(self, client, owner):
        response = client.post(
            COURSES,
            json={"title": "X", "starting_confidence": 9},
            headers=auth_headers(owner.email),
        )
        assert response.status_code == 422


class TestCrossUserIsolation:
    """Two real users. Neither may reach the other's course by any route."""

    def test_list_shows_only_your_own(self, client, owner, other_user, owner_course):
        mine = client.get(COURSES, headers=auth_headers(owner.email)).json()
        theirs = client.get(COURSES, headers=auth_headers(other_user.email)).json()
        assert len(mine) == 1
        assert theirs == []

    def test_get_returns_404_not_403(self, client, other_user, owner_course):
        """403 would confirm the course exists. It must be indistinguishable
        from a course that was never created."""
        response = client.get(
            f"{COURSES}/{owner_course['id']}", headers=auth_headers(other_user.email)
        )
        assert response.status_code == 404

    def test_nonexistent_course_gives_the_same_404(self, client, other_user, owner_course):
        real = client.get(
            f"{COURSES}/{owner_course['id']}", headers=auth_headers(other_user.email)
        )
        fake = client.get(
            f"{COURSES}/00000000-0000-0000-0000-000000000000",
            headers=auth_headers(other_user.email),
        )
        assert real.status_code == fake.status_code == 404
        assert real.json() == fake.json()

    def test_patch_is_refused(self, client, other_user, owner_course, db_session):
        response = client.patch(
            f"{COURSES}/{owner_course['id']}",
            json={"title": "Hijacked"},
            headers=auth_headers(other_user.email),
        )
        assert response.status_code == 404
        assert db_session.query(Course).one().title == "Owner's Course"

    def test_delete_is_refused(self, client, other_user, owner_course, db_session):
        response = client.delete(
            f"{COURSES}/{owner_course['id']}", headers=auth_headers(other_user.email)
        )
        assert response.status_code == 404
        assert db_session.query(Course).count() == 1

    def test_finalize_sources_is_refused(self, client, other_user, owner_course, db_session):
        response = client.post(
            f"{COURSES}/{owner_course['id']}/finalize-sources",
            headers=auth_headers(other_user.email),
        )
        assert response.status_code == 404
        assert db_session.query(Course).one().sources_finalized_at is None


class TestOwnerCanOperate:
    def test_get_own_course(self, client, owner, owner_course):
        response = client.get(f"{COURSES}/{owner_course['id']}", headers=auth_headers(owner.email))
        assert response.status_code == 200
        assert response.json()["title"] == "Owner's Course"

    def test_patch_own_course(self, client, owner, owner_course):
        response = client.patch(
            f"{COURSES}/{owner_course['id']}",
            json={"title": "Renamed"},
            headers=auth_headers(owner.email),
        )
        assert response.status_code == 200
        assert response.json()["title"] == "Renamed"

    def test_delete_own_course(self, client, owner, owner_course, db_session):
        response = client.delete(
            f"{COURSES}/{owner_course['id']}", headers=auth_headers(owner.email)
        )
        assert response.status_code == 204
        assert db_session.query(Course).count() == 0

    def test_response_does_not_leak_owner_id(self, client, owner, owner_course):
        assert "owner_id" not in owner_course


class TestSourceImmutability:
    def test_finalize_moves_course_to_processing(self, client, owner, owner_course):
        response = client.post(
            f"{COURSES}/{owner_course['id']}/finalize-sources",
            headers=auth_headers(owner.email),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "PROCESSING"
        assert response.json()["sources_finalized_at"] is not None

    def test_finalizing_twice_conflicts(self, client, owner, owner_course):
        url = f"{COURSES}/{owner_course['id']}/finalize-sources"
        client.post(url, headers=auth_headers(owner.email))
        second = client.post(url, headers=auth_headers(owner.email))
        assert second.status_code == 409
