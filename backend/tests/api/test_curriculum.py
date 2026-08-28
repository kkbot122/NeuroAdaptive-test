"""
API tests for the curriculum surface: /courses/{id}/structure, /graph,
/publish-structure.

Naming follows architecture.md (GET/PUT .../structure, GET/PUT .../graph,
POST .../publish-structure), not the Phase 2 pack's suggested /outline and
/concept-graph -- see curriculum/router.py's module docstring for the
declared conflict and its resolution.
"""
import pytest

from uuid import UUID

from app.modules.documents.chunk_models import Chunk
from tests.conftest import auth_headers

PROSE = ("Parsing is the process of analysing a string of symbols. " * 12).strip()


@pytest.fixture()
def generated_course(client, owner):
    """A course with a real (fake-gateway) generated version, via the same
    /process endpoint every ingestion test uses."""
    course = client.post(
        "/api/v1/courses", json={"title": "Compilers"}, headers=auth_headers(owner.email)
    ).json()
    client.post(
        f"/api/v1/courses/{course['id']}/documents",
        files={"file": ("notes.txt", PROSE.encode(), "text/plain")},
        data={"role": "STUDY"},
        headers=auth_headers(owner.email),
    )
    job = client.post(
        f"/api/v1/courses/{course['id']}/process", headers=auth_headers(owner.email)
    ).json()
    return course, job


class TestStructureOwnership:
    def test_owner_can_read_the_structure(self, client, owner, generated_course):
        course, _ = generated_course
        response = client.get(
            f"/api/v1/courses/{course['id']}/structure", headers=auth_headers(owner.email)
        )
        assert response.status_code == 200
        assert "modules" in response.json()

    def test_other_user_gets_404_not_the_structure(self, client, other_user, generated_course):
        course, _ = generated_course
        response = client.get(
            f"/api/v1/courses/{course['id']}/structure", headers=auth_headers(other_user.email)
        )
        assert response.status_code == 404

    def test_anonymous_request_is_rejected(self, client, generated_course):
        course, _ = generated_course
        assert client.get(f"/api/v1/courses/{course['id']}/structure").status_code == 422

    def test_course_with_no_generated_version_returns_404(self, client, owner):
        course = client.post(
            "/api/v1/courses", json={"title": "Empty"}, headers=auth_headers(owner.email)
        ).json()
        response = client.get(
            f"/api/v1/courses/{course['id']}/structure", headers=auth_headers(owner.email)
        )
        assert response.status_code == 404


class TestOutlineReviewGate:
    def test_renaming_a_lesson_persists(self, client, owner, generated_course, db_session):
        """The mandate's specific requirement: a learner-edited outline is
        what gets used, not the original proposal -- and the rename is
        visible on the very next read."""
        from app.modules.curriculum.models import CourseVersion, Module

        course, _ = generated_course
        version = (
            db_session.query(CourseVersion)
            .filter(CourseVersion.course_id == UUID(course["id"]))
            .first()
        )
        module = Module(course_version_id=version.id, position=99, title="Injected Module")
        db_session.add(module)
        db_session.commit()
        from app.modules.curriculum.models import Lesson

        lesson = Lesson(module_id=module.id, position=0, title="Original Title")
        db_session.add(lesson)
        db_session.commit()
        db_session.refresh(lesson)

        response = client.put(
            f"/api/v1/courses/{course['id']}/structure",
            json={"lesson_renames": [{"lesson_id": str(lesson.id), "title": "Renamed Lesson"}]},
            headers=auth_headers(owner.email),
        )
        assert response.status_code == 200

        follow_up = client.get(
            f"/api/v1/courses/{course['id']}/structure", headers=auth_headers(owner.email)
        ).json()
        titles = [l["title"] for m in follow_up["modules"] for l in m["lessons"]]
        assert "Renamed Lesson" in titles
        assert "Original Title" not in titles

    def test_other_user_cannot_rename_your_lesson(self, client, owner, other_user, generated_course, db_session):
        from app.modules.curriculum.models import CourseVersion, Lesson, Module

        course, _ = generated_course
        version = (
            db_session.query(CourseVersion).filter(CourseVersion.course_id == UUID(course["id"])).first()
        )
        module = Module(course_version_id=version.id, position=0, title="M")
        db_session.add(module)
        db_session.commit()
        lesson = Lesson(module_id=module.id, position=0, title="Original")
        db_session.add(lesson)
        db_session.commit()
        db_session.refresh(lesson)

        response = client.put(
            f"/api/v1/courses/{course['id']}/structure",
            json={"lesson_renames": [{"lesson_id": str(lesson.id), "title": "Hijacked"}]},
            headers=auth_headers(other_user.email),
        )
        assert response.status_code == 404
        db_session.refresh(lesson)
        assert lesson.title == "Original"


class TestGraphOwnership:
    def test_owner_can_read_the_graph(self, client, owner, generated_course):
        course, _ = generated_course
        response = client.get(
            f"/api/v1/courses/{course['id']}/graph", headers=auth_headers(owner.email)
        )
        assert response.status_code == 200
        assert "concepts" in response.json() and "edges" in response.json()

    def test_other_user_cannot_read_your_concept_graph(self, client, other_user, generated_course):
        """The required case: a course's graph is only visible to its owner."""
        course, _ = generated_course
        response = client.get(
            f"/api/v1/courses/{course['id']}/graph", headers=auth_headers(other_user.email)
        )
        assert response.status_code == 404

    def test_nonexistent_course_graph_gives_the_same_404(self, client, other_user, generated_course):
        course, _ = generated_course
        real = client.get(
            f"/api/v1/courses/{course['id']}/graph", headers=auth_headers(other_user.email)
        )
        fake = client.get(
            "/api/v1/courses/00000000-0000-0000-0000-000000000000/graph",
            headers=auth_headers(other_user.email),
        )
        assert real.status_code == fake.status_code == 404


class TestPublishStructure:
    def test_publishing_activates_the_version(self, client, owner, generated_course, db_session):
        course, job = generated_course
        assert job["status"] == "READY"

        response = client.post(
            f"/api/v1/courses/{course['id']}/publish-structure", headers=auth_headers(owner.email)
        )
        assert response.status_code == 200
        assert response.json()["active_version_id"]

    def test_other_user_cannot_publish_your_course(self, client, other_user, generated_course):
        course, _ = generated_course
        response = client.post(
            f"/api/v1/courses/{course['id']}/publish-structure", headers=auth_headers(other_user.email)
        )
        assert response.status_code == 404

    def test_generating_alone_does_not_publish(self, client, owner, generated_course, db_session):
        """Explicit confirmation required: /process (generation) never
        activates a version on its own."""
        from app.modules.courses.models import Course

        course, _ = generated_course
        structure = client.get(
            f"/api/v1/courses/{course['id']}/structure", headers=auth_headers(owner.email)
        ).json()
        assert structure["status"] == "READY"  # generated and valid...

        row = db_session.query(Course).filter(Course.id == UUID(course["id"])).first()
        assert row.active_version_id is None  # ...but not activated without publish-structure


class TestEndToEnd:
    def test_two_documents_produce_a_valid_traceable_course(
        self, client, owner, db_session, fake_generation
    ):
        """
        From two small fixture documents covering overlapping material,
        generate a course through the real API; confirm every generated
        Lesson traces to at least one ConceptSource chunk, and the concept
        graph contains no cycles.

        fake_generation is the exact same instance `client` already wired
        into JobService (see conftest.py) -- registering responses on it
        here controls what the pipeline "discovers" without touching the
        network, while still exercising the real extraction -> normalization
        -> graph -> clustering -> validation -> persistence chain.
        """
        from app.modules.curriculum.models import Concept, ConceptSource, Lesson, LessonConcept

        fake_generation.when_prompt_contains(
            "Memory management", '{"concepts": [{"name": "Virtual Memory", '
            '"definition": "Uses disk as an extension of RAM.", "importance": 0.9}]}'
        ).when_prompt_contains(
            "A deadlock", '{"concepts": [{"name": "Deadlock", '
            '"definition": "A circular wait condition.", "importance": 0.9}]}'
        ).when_prompt_contains(
            "Propose prerequisite", '{"edges": [{"prerequisite": "Virtual Memory", '
            '"dependent": "Deadlock", "strength": "SOFT", "confidence": 0.4}]}'
        )

        course = client.post(
            "/api/v1/courses", json={"title": "OS Fundamentals"}, headers=auth_headers(owner.email)
        ).json()

        doc1_text = "Memory management allows a process to use more memory than physically available. " * 8
        doc2_text = "A deadlock is a state where processes wait on each other indefinitely. " * 8

        client.post(
            f"/api/v1/courses/{course['id']}/documents",
            files={"file": ("memory.txt", doc1_text.encode(), "text/plain")},
            data={"role": "STUDY"},
            headers=auth_headers(owner.email),
        )
        client.post(
            f"/api/v1/courses/{course['id']}/documents",
            files={"file": ("deadlocks.txt", doc2_text.encode(), "text/plain")},
            data={"role": "STUDY"},
            headers=auth_headers(owner.email),
        )
        job = client.post(
            f"/api/v1/courses/{course['id']}/process", headers=auth_headers(owner.email)
        ).json()
        assert job["status"] == "READY"

        graph = client.get(
            f"/api/v1/courses/{course['id']}/graph", headers=auth_headers(owner.email)
        ).json()
        assert len(graph["concepts"]) == 2
        assert len(graph["edges"]) == 1

        structure = client.get(
            f"/api/v1/courses/{course['id']}/structure", headers=auth_headers(owner.email)
        ).json()
        assert structure["status"] == "READY"
        lesson_ids = [
            lesson["id"] for module in structure["modules"] for lesson in module["lessons"]
        ]
        assert lesson_ids

        # Every generated Lesson traces to at least one ConceptSource chunk.
        for lesson_id in lesson_ids:
            concept_ids = [
                lc.concept_id
                for lc in db_session.query(LessonConcept)
                .filter(LessonConcept.lesson_id == UUID(lesson_id))
                .all()
            ]
            assert concept_ids
            for concept_id in concept_ids:
                sources = (
                    db_session.query(ConceptSource)
                    .filter(ConceptSource.concept_id == concept_id)
                    .all()
                )
                assert sources, f"concept {concept_id} has no source chunk"

        # No cycles in the persisted graph -- checked with the real
        # acyclicity function, not a hand-rolled approximation.
        from app.modules.curriculum.graph import ProposedEdge, is_acyclic

        proposed = [
            ProposedEdge(
                prerequisite_id=e["prerequisite_concept_id"],
                dependent_id=e["dependent_concept_id"],
                strength=e["strength"],
                confidence=e["confidence"],
            )
            for e in graph["edges"]
        ]
        assert is_acyclic(proposed)
