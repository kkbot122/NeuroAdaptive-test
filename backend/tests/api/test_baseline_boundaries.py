import uuid
import pytest
from app.core.config import settings
from app.modules.tutor.entailment import EntailmentUnavailable, GeminiEntailmentChecker
from app.modules.tutor.parsing import parse_tutor_response, TutorParseError
from app.services.generation.fake import FakeGenerationGateway
from app.modules.jobs.router import _dispatcher
from app.modules.jobs.models import ProcessingJob
from tests.conftest import auth_headers

@pytest.mark.parametrize("value", ['"false"', '"true"', '1', 'null', '{}'])
def test_entailment_requires_a_real_boolean(value):
    gateway = FakeGenerationGateway().set_default('{"supported": ' + value + '}')
    assert GeminiEntailmentChecker(gateway)("claim", "source") is False


def test_strict_entailment_distinguishes_unavailable_checks_from_unsupported_claims():
    malformed = FakeGenerationGateway().set_default('{"supported": "false"}')
    with pytest.raises(EntailmentUnavailable):
        GeminiEntailmentChecker(malformed, raise_on_error=True)("claim", "source")

    unavailable = FakeGenerationGateway()
    with pytest.raises(EntailmentUnavailable):
        GeminiEntailmentChecker(unavailable, raise_on_error=True)("claim", "source")

    supported_false = FakeGenerationGateway().set_default('{"supported": false}')
    assert GeminiEntailmentChecker(supported_false, raise_on_error=True)("claim", "source") is False


def test_tutor_parse_errors_never_include_response_content():
    with pytest.raises(TutorParseError) as error:
        parse_tutor_response("private-answer-marker")
    assert "private-answer-marker" not in str(error.value)


def test_evaluator_is_closed_by_default(client, owner, monkeypatch):
    monkeypatch.setattr(settings, "EVALUATOR_EMAILS", "")
    response = client.post("/api/v1/evaluation/experiments", headers=auth_headers(owner.email),
        json={"name": "Denied", "conditions": []})
    assert response.status_code == 404


def test_dispatch_failure_is_durable_and_retryable(client, owner, db_session):
    class BrokenDispatcher:
        def enqueue(self, *args):
            raise RuntimeError("private broker address")
    from app.main import app
    app.dependency_overrides[_dispatcher] = BrokenDispatcher
    course = client.post("/api/v1/courses", headers=auth_headers(owner.email), json={"title": "Fixture"}).json()
    client.post(f"/api/v1/courses/{course['id']}/documents/paste", headers=auth_headers(owner.email),
        json={"text": "A synthetic source about algorithms."})
    response = client.post(f"/api/v1/courses/{course['id']}/process", headers=auth_headers(owner.email))
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "PAUSED"
    assert body["error_category"] == "DISPATCH_UNAVAILABLE"
    assert "private broker address" not in response.text
    db_session.expire_all()
    assert db_session.get(ProcessingJob, uuid.UUID(body["id"])).status == "PAUSED"
    retry = client.post(f"/api/v1/jobs/{body['id']}/retry", headers=auth_headers(owner.email))
    assert retry.status_code == 202
    assert retry.json()["retry_count"] == 1


def test_empty_generation_cannot_become_ready(client, owner, fake_generation):
    fake_generation.set_default('{"concepts": [], "edges": []}')
    course = client.post("/api/v1/courses", headers=auth_headers(owner.email), json={"title": "Empty"}).json()
    client.post(f"/api/v1/courses/{course['id']}/documents/paste", headers=auth_headers(owner.email),
        json={"text": "This is synthetic source text about computation and algorithms. " * 10})
    started = client.post(f"/api/v1/courses/{course['id']}/process", headers=auth_headers(owner.email)).json()
    job = client.get(f"/api/v1/jobs/{started['id']}", headers=auth_headers(owner.email)).json()
    assert job["status"] == "FAILED"
    assert client.post(f"/api/v1/courses/{course['id']}/publish-structure", headers=auth_headers(owner.email)).status_code == 409


def test_tutor_rejects_a_lesson_from_another_owned_course(client, owner, db_session):
    from app.modules.courses.models import Course
    from app.modules.curriculum.models import CourseVersion, Module, Lesson
    courses = [Course(owner_id=owner.id, title=x) for x in ["A", "B"]]
    db_session.add_all(courses)
    db_session.flush()
    version = CourseVersion(course_id=courses[1].id, owner_id=owner.id, version_number=1)
    db_session.add(version)
    db_session.flush()
    module = Module(course_version_id=version.id, title="Foreign", position=0)
    db_session.add(module)
    db_session.flush()
    lesson = Lesson(module_id=module.id, title="Foreign", position=0)
    db_session.add(lesson)
    db_session.commit()
    response = client.post(f"/api/v1/courses/{courses[0].id}/tutor", headers=auth_headers(owner.email),
        json={"question": "What is this?", "context_lesson_id": str(lesson.id)})
    assert response.status_code == 404


def test_outcome_rejects_wrong_course_path(client, owner, db_session):
    from tests.api.test_adaptation_history import make_course_with_decision
    _, _, decision, _ = make_course_with_decision(client, db_session, owner)
    other = client.post("/api/v1/courses", headers=auth_headers(owner.email), json={"title": "Other"}).json()
    response = client.post(f"/api/v1/courses/{other['id']}/adaptation-decisions/{decision.id}/outcomes/engagement",
        headers=auth_headers(owner.email), json={"outcome_type": "VIEWED"})
    assert response.status_code == 404


@pytest.mark.parametrize("foreign_owner", [False, True])
def test_explicit_graph_version_is_scoped_to_url_course(client, owner, other_user, db_session, foreign_owner):
    from app.modules.courses.models import Course
    from app.modules.curriculum.models import CourseVersion, Concept
    owned = Course(owner_id=owner.id, title="Owned")
    foreign = Course(owner_id=other_user.id if foreign_owner else owner.id, title="Other")
    db_session.add_all([owned, foreign])
    db_session.flush()
    version = CourseVersion(course_id=foreign.id, owner_id=foreign.owner_id, version_number=1)
    db_session.add(version)
    db_session.flush()
    db_session.add(Concept(course_id=foreign.id, course_version_id=version.id, owner_id=foreign.owner_id,
        canonical_key="foreign", name="Foreign", definition="Private source"))
    db_session.commit()
    response = client.get(f"/api/v1/courses/{owned.id}/graph", params={"version_id": str(version.id)}, headers=auth_headers(owner.email))
    assert response.status_code == 404
    assert "Private source" not in response.text


def test_expired_worker_job_exposes_and_accepts_manual_retry(client, owner, db_session):
    from datetime import datetime, timedelta, timezone
    from app.modules.courses.models import Course
    course = Course(owner_id=owner.id, title="Interrupted")
    db_session.add(course)
    db_session.flush()
    job = ProcessingJob(course_id=course.id, owner_id=owner.id, status="RUNNING", lease_token=uuid.uuid4(),
        lease_expires_at=datetime.now(timezone.utc)-timedelta(seconds=1))
    db_session.add(job)
    db_session.commit()
    body = client.get(f"/api/v1/jobs/{job.id}", headers=auth_headers(owner.email)).json()
    assert body["retry_available"] is True and body["error_category"] == "INTERRUPTED"
    # Supply a dispatcher that queues without processing to verify the
    # retry transaction independently of an incomplete synthetic job's stages.
    class Queued:
        def enqueue(self, *args):
            pass
    from app.main import app
    app.dependency_overrides[_dispatcher] = Queued
    retried = client.post(f"/api/v1/jobs/{job.id}/retry", headers=auth_headers(owner.email))
    assert retried.status_code == 202 and retried.json()["status"] == "PENDING"
    assert retried.json()["retry_count"] == 1
    assert retried.json()["retry_available"] is False
    db_session.refresh(job)
    assert job.lease_token is None


def test_explicit_retry_regenerates_failed_curriculum_but_retains_diagnostics(client, owner, fake_generation, db_session):
    from app.modules.curriculum.models import CourseVersion
    fake_generation.set_default('{"concepts": [], "edges": []}')
    course = client.post("/api/v1/courses", headers=auth_headers(owner.email), json={"title": "Recover"}).json()
    client.post(f"/api/v1/courses/{course['id']}/documents/paste", headers=auth_headers(owner.email),
        json={"text": "Algorithms compute results from input using a sequence of steps. " * 10})
    started = client.post(f"/api/v1/courses/{course['id']}/process", headers=auth_headers(owner.email)).json()
    assert client.get(f"/api/v1/jobs/{started['id']}", headers=auth_headers(owner.email)).json()["status"] == "FAILED"
    fake_generation.set_default('{"concepts": [{"name":"Algorithm","definition":"A sequence of computation steps."}],"edges":[]}')
    retried = client.post(f"/api/v1/jobs/{started['id']}/retry", headers=auth_headers(owner.email))
    assert retried.status_code == 202
    assert client.get(f"/api/v1/jobs/{started['id']}", headers=auth_headers(owner.email)).json()["status"] == "READY", [(v.processing_retry_count, v.status, v.validation_errors) for v in db_session.query(CourseVersion).filter_by(processing_job_id=uuid.UUID(started["id"])).all()]
    versions = db_session.query(CourseVersion).filter_by(processing_job_id=uuid.UUID(started["id"])).order_by(CourseVersion.processing_retry_count).all()
    assert [(v.processing_retry_count, v.status) for v in versions] == [(0, "FAILED"), (1, "READY")]


def test_unconfigured_upload_intent_is_actionable_and_does_not_persist(client, owner, db_session, monkeypatch):
    from app.modules.documents.models import StorageUploadIntent
    monkeypatch.setattr(settings, "STORAGE_S3_ENDPOINT", "")
    course = client.post("/api/v1/courses", headers=auth_headers(owner.email), json={"title": "Local"}).json()
    response = client.post(f"/api/v1/courses/{course['id']}/documents/upload-intents", headers=auth_headers(owner.email),
        json={"filename": "fixture.txt", "size_bytes": 10, "checksum_sha256": "a"*64})
    assert response.status_code == 503
    assert response.json()["type"].endswith("/storage-not-configured")
    assert db_session.query(StorageUploadIntent).count() == 0
