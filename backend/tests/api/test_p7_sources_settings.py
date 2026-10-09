"""P7 source replacement, settings, and deletion recovery regressions."""
from datetime import datetime, timedelta, timezone
import hashlib
from uuid import UUID, uuid4

import pytest

from app.modules.auth.models import User
from app.modules.courses.models import Course
from app.modules.documents.chunk_models import Chunk
from app.modules.documents.models import Document, StorageUploadIntent
from app.modules.documents.storage import ObjectInfo
from app.modules.jobs.models import ProcessingJob
from app.modules.jobs.service import JobNotFound, JobService
from app.modules.privacy.models import StorageCleanupTask
from app.modules.privacy.service import PrivacyService
from app.modules.documents.storage import S3PrivateStorage, StorageUnavailable
from tests.conftest import auth_headers


def _new_course(client, email, title="P7 course"):
    return client.post("/api/v1/courses", json={"title": title}, headers=auth_headers(email)).json()


def _upload(client, email, course_id, filename, text, *, role="STUDY", replaces_document_id=None):
    data = {"role": role}
    if replaces_document_id is not None:
        data["replaces_document_id"] = str(replaces_document_id)
    return client.post(
        f"/api/v1/courses/{course_id}/documents",
        files={"file": (filename, text.encode(), "text/plain")},
        data=data,
        headers=auth_headers(email),
    )


def test_multipart_replacement_keeps_the_original_when_matching_bytes_have_another_role(
    client, owner, db_session, monkeypatch,
):
    from app.modules.privacy import tasks

    monkeypatch.setattr(tasks.cleanup_storage_object, "apply_async", lambda **_kwargs: None)
    course = _new_course(client, owner.email, "Role preserving replacement")
    study_content = "Shared reference material. " * 20
    syllabus = _upload(client, owner.email, course["id"], "syllabus.txt", "Original course plan. " * 20, role="SYLLABUS")
    study = _upload(client, owner.email, course["id"], "study.txt", study_content, role="STUDY")
    assert syllabus.status_code == study.status_code == 201
    syllabus_id = UUID(syllabus.json()["id"])
    study_id = UUID(study.json()["id"])
    revision_before = db_session.query(Course).filter_by(id=UUID(course["id"])).one().source_revision

    rejected = _upload(
        client, owner.email, course["id"], "replacement-syllabus.txt", study_content,
        role="SYLLABUS", replaces_document_id=syllabus_id,
    )

    assert rejected.status_code == 400
    assert "different role" in rejected.json()["detail"]
    assert db_session.query(Document).filter_by(id=syllabus_id).one().filename == "syllabus.txt"
    assert db_session.query(Document).filter_by(id=study_id).one().role == "STUDY"
    assert db_session.query(Course).filter_by(id=UUID(course["id"])).one().source_revision == revision_before


def test_private_replacement_rejects_duplicate_from_another_role_and_tracks_candidate_cleanup(
    db_session, owner, monkeypatch,
):
    from app.modules.documents.service import DocumentService, UploadRejected
    from app.modules.privacy import tasks

    monkeypatch.setattr(tasks.cleanup_storage_object, "apply_async", lambda **_kwargs: None)
    monkeypatch.setattr(S3PrivateStorage, "__init__", lambda _self: None)
    candidate = b"Private source bytes already used in a different role."
    checksum = hashlib.sha256(candidate).hexdigest()
    monkeypatch.setattr(S3PrivateStorage, "inspect", lambda _self, _key: ObjectInfo(len(candidate)))
    monkeypatch.setattr(S3PrivateStorage, "checksum_sha256", lambda _self, _key: checksum)
    monkeypatch.setattr(S3PrivateStorage, "read", lambda _self, _key: candidate)

    course = Course(owner_id=owner.id, title="Private role preserving replacement")
    db_session.add(course)
    db_session.flush()
    original = Document(
        course_id=course.id, owner_id=owner.id, filename="syllabus.txt", role="SYLLABUS",
        storage_path="private/original-syllabus.txt", storage_key="private/original-syllabus.txt",
        size_bytes=15, checksum_sha256="d" * 64,
    )
    duplicate = Document(
        course_id=course.id, owner_id=owner.id, filename="study.txt", role="STUDY",
        storage_path="private/study.txt", storage_key="private/study.txt",
        size_bytes=len(candidate), checksum_sha256=checksum,
    )
    db_session.add_all([original, duplicate])
    db_session.flush()
    intent = StorageUploadIntent(
        course_id=course.id, owner_id=owner.id, object_key="private/candidate-syllabus.txt",
        filename="candidate-syllabus.txt", role="SYLLABUS", replaces_document_id=original.id,
        expected_checksum_sha256=checksum, expected_size_bytes=len(candidate),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db_session.add(intent)
    db_session.commit()
    revision_before = course.source_revision

    with pytest.raises(UploadRejected, match="different role"):
        DocumentService(db_session).finalize_upload_result(course.id, intent.id, owner.id)

    assert db_session.query(Document).filter_by(id=original.id).one().filename == "syllabus.txt"
    assert db_session.query(Document).filter_by(id=duplicate.id).one().role == "STUDY"
    assert db_session.query(Course).filter_by(id=course.id).one().source_revision == revision_before
    db_session.refresh(intent)
    assert intent.finalized is True
    assert intent.finalized_document_id is None
    cleanup = db_session.query(StorageCleanupTask).filter_by(id=intent.upload_cleanup_task_id).one()
    assert cleanup.storage_target == intent.object_key
    assert cleanup.status == "PENDING"


def test_replacement_invalidates_old_outline_preserves_unchanged_extraction_and_fences_old_job(
    client, owner, db_session, fake_embeddings, fake_vectors, fake_generation, monkeypatch,
):
    from app.main import app
    from app.modules.documents.router import _job_dispatcher as documents_dispatcher
    from app.modules.documents.router import _job_service as documents_job_service
    from app.modules.curriculum.models import CourseVersion
    from app.modules.jobs.models import JobStatus
    from app.modules.privacy import tasks

    class DeferredDispatcher:
        def enqueue(self, job_id, owner_id):
            return None

    monkeypatch.setattr(tasks.cleanup_storage_object, "apply_async", lambda **_kwargs: None)
    app.dependency_overrides[documents_job_service] = lambda: JobService(
        db_session, embeddings=fake_embeddings, vectors=fake_vectors, generation=fake_generation,
    )
    app.dependency_overrides[documents_dispatcher] = DeferredDispatcher

    course = _new_course(client, owner.email)
    course_id = course["id"]
    target_text = "Compiler parsing turns a sequence of symbols into a structured representation. " * 18
    keep_text = "A symbol table records names and their associated declarations. " * 18
    assert _upload(client, owner.email, course_id, "parser.txt", target_text).status_code == 201
    assert _upload(client, owner.email, course_id, "symbols.txt", keep_text).status_code == 201
    started = client.post(f"/api/v1/courses/{course_id}/process", headers=auth_headers(owner.email))
    assert started.status_code == 202
    ready = client.get(f"/api/v1/jobs/{started.json()['id']}", headers=auth_headers(owner.email)).json()
    assert ready["status"] == "READY"

    old_version = db_session.query(CourseVersion).filter_by(course_id=UUID(course_id)).one()
    docs = db_session.query(Document).filter_by(course_id=UUID(course_id)).all()
    original = next(doc for doc in docs if doc.filename == "parser.txt")
    unchanged = next(doc for doc in docs if doc.filename == "symbols.txt")
    unchanged_chunk_rows = db_session.query(Chunk).filter_by(document_id=unchanged.id).order_by(Chunk.position).all()
    assert unchanged_chunk_rows
    unchanged_chunks = [(row.id, row.text, row.embedding_model, row.indexed_at) for row in unchanged_chunk_rows]

    stale_job = JobService(db_session).create_for_course(UUID(course_id), owner.id)
    failed_replacement = _upload(
        client, owner.email, course_id, "bad.pdf", "ordinary text", replaces_document_id=original.id,
    )
    assert failed_replacement.status_code == 400
    db_session.refresh(original)
    assert original.filename == "parser.txt"

    replacement = _upload(
        client, owner.email, course_id, "parser-revised.txt",
        "Compiler parsing organizes source symbols into a syntax tree. " * 19,
        replaces_document_id=original.id,
    )
    assert replacement.status_code == 201, replacement.text
    result = replacement.json()
    assert result["source_changed"] is True
    assert result["replaced_filename"] == "parser.txt"
    assert result["rebuild_job_id"]
    assert result["cleanup_pending"] is True

    db_session.refresh(old_version)
    assert old_version.status == "STALE"
    assert db_session.query(Document).filter_by(id=original.id).first() is None
    db_session.refresh(unchanged)
    assert unchanged.status == "EXTRACTED"
    assert [(row.id, row.text, row.embedding_model, row.indexed_at) for row in
            db_session.query(Chunk).filter_by(document_id=unchanged.id).order_by(Chunk.position).all()] == unchanged_chunks
    db_session.refresh(stale_job)
    assert stale_job.status == JobStatus.CANCELLED.value

    # An old queued delivery cannot restore artifacts after the revision moves.
    old_run = JobService(
        db_session, embeddings=fake_embeddings, vectors=fake_vectors, generation=fake_generation,
    ).run(stale_job.id, owner.id)
    assert old_run.status == JobStatus.CANCELLED.value

    blocked = client.post(f"/api/v1/courses/{course_id}/publish-structure", headers=auth_headers(owner.email))
    assert blocked.status_code == 409

    rebuilt = JobService(
        db_session, embeddings=fake_embeddings, vectors=fake_vectors, generation=fake_generation,
    ).run(UUID(result["rebuild_job_id"]), owner.id)
    assert rebuilt.status == JobStatus.READY.value
    db_session.refresh(old_version)
    assert old_version.status == "STALE"
    new_outline = client.get(f"/api/v1/courses/{course_id}/structure", headers=auth_headers(owner.email))
    assert new_outline.status_code == 200
    assert UUID(new_outline.json()["version_id"]) != old_version.id


def test_in_flight_worker_cannot_commit_after_a_source_revision_wins(db_session, owner, monkeypatch):
    from sqlalchemy import update

    from app.modules.curriculum.models import CourseVersion
    from app.modules.jobs.models import JobStatus

    course = Course(owner_id=owner.id, title="Worker fence")
    db_session.add(course)
    db_session.commit()
    job = JobService(db_session).create_for_course(course.id, owner.id)
    service = JobService(db_session)

    def source_change_wins(job_id, owner_id):
        # Let the worker's lease claim commit, then model a separate source
        # replacement committing before this worker's next artifact write.
        db_session.commit()
        with db_session.get_bind().begin() as connection:
            connection.execute(update(Course).where(Course.id == course.id).values(
                source_revision=job.source_revision + 1, status="PROCESSING",
            ))
            connection.execute(update(ProcessingJob).where(ProcessingJob.id == job_id).values(
                status="CANCELLED", lease_token=None, lease_expires_at=None,
            ))
        db_session.add(CourseVersion(
            processing_job_id=job_id, course_id=course.id, owner_id=owner_id,
            version_number=77, source_revision=job.source_revision, status="DRAFT",
        ))
        db_session.commit()
        raise AssertionError("the source revision fence should reject this artifact commit")

    monkeypatch.setattr(service, "_execute", source_change_wins)
    result = service.run(job.id, owner.id)
    assert result.status == "CANCELLED"
    assert db_session.query(CourseVersion).filter_by(version_number=77).count() == 0
    db_session.refresh(course)
    assert course.source_revision == job.source_revision + 1


def test_finalized_replacement_is_idempotent_and_returns_the_same_file_change(
    db_session, owner, monkeypatch,
):
    from app.modules.documents.service import DocumentService
    from app.modules.privacy import tasks

    monkeypatch.setattr(tasks.cleanup_storage_object, "apply_async", lambda **_kwargs: None)
    monkeypatch.setattr(S3PrivateStorage, "__init__", lambda _self: None)
    content = b"Updated source text with enough plain text for signature validation."
    monkeypatch.setattr(S3PrivateStorage, "inspect", lambda _self, _key: ObjectInfo(len(content)))
    monkeypatch.setattr(S3PrivateStorage, "checksum_sha256", lambda _self, _key: hashlib.sha256(content).hexdigest())
    monkeypatch.setattr(S3PrivateStorage, "read", lambda _self, _key: content)

    course = Course(owner_id=owner.id, title="Idempotent replacement", sources_finalized_at=datetime.now(timezone.utc))
    db_session.add(course)
    db_session.flush()
    original = Document(
        course_id=course.id, owner_id=owner.id, filename="old.txt", role="STUDY",
        storage_path="private/original.txt", storage_key="private/original.txt", size_bytes=18,
        checksum_sha256="d" * 64,
    )
    db_session.add(original)
    db_session.flush()
    intent = StorageUploadIntent(
        course_id=course.id, owner_id=owner.id, object_key="private/candidate.txt",
        filename="new.txt", role="STUDY", replaces_document_id=original.id,
        expected_checksum_sha256=hashlib.sha256(content).hexdigest(), expected_size_bytes=len(content),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db_session.add(intent)
    db_session.commit()

    service = DocumentService(db_session)
    first = service.finalize_upload_result(course.id, intent.id, owner.id)
    source_revision = db_session.query(Course).filter_by(id=course.id).one().source_revision
    retry = service.finalize_upload_result(course.id, intent.id, owner.id)
    assert first.document.id == retry.document.id
    assert first.source_changed is retry.source_changed is True
    assert first.replaced_filename == retry.replaced_filename == "old.txt"
    assert first.cleanup_pending is retry.cleanup_pending is True
    assert db_session.query(Course).filter_by(id=course.id).one().source_revision == source_revision == 1
    assert db_session.query(StorageCleanupTask).count() == 1


def test_source_and_upload_intent_ownership_are_enforced(client, owner, other_user, db_session, monkeypatch):
    from app.modules.privacy import tasks

    monkeypatch.setattr(tasks.cleanup_storage_object, "apply_async", lambda **_kwargs: None)
    course = _new_course(client, owner.email, "Owned sources")
    doc_response = _upload(client, owner.email, course["id"], "owned.txt", "Owned text. " * 20)
    document_id = doc_response.json()["id"]

    foreign_replace = _upload(
        client, other_user.email, course["id"], "intrusion.txt", "Intruding text. " * 20,
        replaces_document_id=document_id,
    )
    assert foreign_replace.status_code == 404
    foreign_remove = client.delete(
        f"/api/v1/courses/{course['id']}/documents/{document_id}", headers=auth_headers(other_user.email),
    )
    assert foreign_remove.status_code == 404
    own_remove = client.delete(
        f"/api/v1/courses/{course['id']}/documents/{document_id}", headers=auth_headers(owner.email),
    )
    assert own_remove.status_code == 200
    assert own_remove.json()["filename"] == "owned.txt"
    assert own_remove.json()["status"] == "RETIRED"
    assert own_remove.json()["source_changed"] is True
    assert db_session.query(Document).filter_by(id=UUID(document_id)).first() is None

    intent = StorageUploadIntent(
        id=uuid4(), course_id=UUID(course["id"]), owner_id=owner.id,
        object_key="private/p7-owner-check.txt", filename="replacement.txt",
        role="STUDY", replaces_document_id=UUID(document_id),
        expected_checksum_sha256="a" * 64, expected_size_bytes=20,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db_session.add(intent)
    db_session.commit()
    finalize = client.post(
        f"/api/v1/courses/{course['id']}/documents/finalize/{intent.id}",
        headers=auth_headers(other_user.email),
    )
    assert finalize.status_code == 404
    owner_finalize = client.post(
        f"/api/v1/courses/{course['id']}/documents/finalize/{intent.id}",
        headers=auth_headers(owner.email),
    )
    assert owner_finalize.status_code == 409
    retry_finalize = client.post(
        f"/api/v1/courses/{course['id']}/documents/finalize/{intent.id}",
        headers=auth_headers(owner.email),
    )
    assert retry_finalize.status_code == 409
    assert db_session.query(Document).filter_by(id=UUID(document_id)).first() is None


def test_course_deletion_retires_upload_intents_before_document_rows(db_session, owner, monkeypatch):
    from sqlalchemy import event
    from app.modules.privacy import tasks

    monkeypatch.setattr(tasks.cleanup_storage_object, "apply_async", lambda **_kwargs: None)
    course = Course(owner_id=owner.id, title="Deletion lock order")
    db_session.add(course)
    db_session.flush()
    document = Document(
        course_id=course.id, owner_id=owner.id, filename="source.txt", role="STUDY",
        storage_path="private/source.txt", storage_key="private/source.txt", size_bytes=10,
        checksum_sha256="b" * 64,
    )
    intent = StorageUploadIntent(
        course_id=course.id, owner_id=owner.id, object_key="private/unfinalized.txt",
        filename="unfinalized.txt", role="STUDY", expected_checksum_sha256="c" * 64,
        expected_size_bytes=12, expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db_session.add_all([document, intent])
    db_session.commit()
    course_id = course.id
    deletes: list[str] = []

    def record_delete(_connection, _cursor, statement, _parameters, _context, _executemany):
        sql = statement.lower().lstrip()
        if sql.startswith("delete from storage_upload_intents"):
            deletes.append("intent")
        elif sql.startswith("delete from documents"):
            deletes.append("document")

    event.listen(db_session.get_bind(), "before_cursor_execute", record_delete)
    try:
        PrivacyService(db_session).delete_owned_course(course_id, owner.id)
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", record_delete)

    assert deletes.index("intent") < deletes.index("document")
    assert db_session.query(Course).filter_by(id=course_id).first() is None


def test_local_replacement_retry_returns_the_saved_file_without_a_second_revision(
    client, owner, db_session, monkeypatch,
):
    from app.modules.privacy import tasks

    monkeypatch.setattr(tasks.cleanup_storage_object, "apply_async", lambda **_kwargs: None)
    course = _new_course(client, owner.email, "Retryable source replacement")
    original = _upload(client, owner.email, course["id"], "original.txt", "Original material. " * 20)
    original_id = original.json()["id"]
    replacement_content = "Revised replacement material. " * 20

    replaced = _upload(
        client, owner.email, course["id"], "replacement.txt", replacement_content,
        replaces_document_id=original_id,
    )
    assert replaced.status_code == 201
    replacement_id = replaced.json()["id"]
    revision = db_session.query(Course).filter_by(id=UUID(course["id"])).one().source_revision

    retry = _upload(
        client, owner.email, course["id"], "replacement.txt", replacement_content,
        replaces_document_id=original_id,
    )
    assert retry.status_code == 200
    assert retry.json()["id"] == replacement_id
    assert retry.json()["source_changed"] is False
    assert db_session.query(Course).filter_by(id=UUID(course["id"])).one().source_revision == revision


def test_settings_persist_minimal_tracking_and_reset_presentation_independently(
    client, owner, db_session,
):
    from app.modules.adaptation.models import PresentationAffinity
    from app.modules.events.models import LearningEvent

    accepted = client.post(
        "/api/v1/events/batch", json={"events": [{"event_type": "paragraph_view", "seconds": 3}]},
        headers=auth_headers(owner.email),
    )
    assert accepted.json()["accepted"] == 1

    saved = client.patch(
        "/api/v1/me/settings",
        json={"tracking_consent": "minimal", "default_presentation_format": "analogy", "tutor_panel_open": False},
        headers=auth_headers(owner.email),
    )
    assert saved.status_code == 200
    assert client.get("/api/v1/me/settings", headers=auth_headers(owner.email)).json() == {
        "tracking_consent": "minimal", "default_presentation_format": "analogy", "tutor_panel_open": False,
    }

    declined = client.post(
        "/api/v1/events/batch", json={"events": [{"event_type": "paragraph_view", "seconds": 3}]},
        headers=auth_headers(owner.email),
    )
    assert declined.status_code == 202
    assert declined.json()["accepted"] == 0
    assert db_session.query(LearningEvent).filter_by(user_id=owner.id).count() == 1

    db_session.add(PresentationAffinity(owner_id=owner.id, format="analogy", effectiveness=0.6))
    db_session.commit()
    reset = client.post("/api/v1/me/preferences/reset", headers=auth_headers(owner.email))
    assert reset.status_code == 200
    assert reset.json()["presentation_affinity_rows_removed"] == 1
    assert client.get("/api/v1/me/settings", headers=auth_headers(owner.email)).json() == {
        "tracking_consent": "minimal", "default_presentation_format": "detailed", "tutor_panel_open": True,
    }
    assert db_session.query(LearningEvent).filter_by(user_id=owner.id).count() == 1
    assert db_session.query(PresentationAffinity).filter_by(owner_id=owner.id).count() == 0


def test_account_deletion_tracks_private_storage_cleanup_failures_and_fences_jobs(
    client, owner, db_session, monkeypatch,
):
    from app.modules.privacy import tasks

    course = Course(owner_id=owner.id, title="Disposable P7 deletion course")
    db_session.add(course)
    db_session.flush()
    document = Document(
        course_id=course.id, owner_id=owner.id, filename="private.txt", role="STUDY",
        storage_path="private/private.txt", storage_key="private/private.txt", size_bytes=30,
        checksum_sha256="b" * 64,
    )
    intent = StorageUploadIntent(
        course_id=course.id, owner_id=owner.id, object_key="private/unfinished.txt",
        filename="unfinished.txt", role="STUDY", expected_checksum_sha256="c" * 64,
        expected_size_bytes=20, expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    job = ProcessingJob(course_id=course.id, owner_id=owner.id, source_revision=0, status="PENDING")
    db_session.add_all([document, intent, job])
    db_session.commit()
    course_id = course.id
    job_id = job.id
    owner_id, email = owner.id, owner.email
    monkeypatch.setattr(tasks.cleanup_storage_object, "apply_async", lambda **_kwargs: None)

    def storage_unavailable(_self, _key):
        raise StorageUnavailable()

    monkeypatch.setattr(S3PrivateStorage, "delete", storage_unavailable)
    response = client.delete("/api/v1/me", headers=auth_headers(email))
    assert response.status_code == 202
    assert response.json()["status"] == "cleanup_pending"
    assert response.json()["pending_storage_cleanups"] == 2
    assert db_session.query(User).filter_by(id=owner_id).first() is None
    assert db_session.query(Course).filter_by(id=course_id).first() is None
    cleanup_rows = db_session.query(StorageCleanupTask).order_by(StorageCleanupTask.storage_target).all()
    assert len(cleanup_rows) == 2

    with pytest.raises(StorageUnavailable):
        PrivacyService(db_session).run_storage_cleanup(cleanup_rows[0].id)
    db_session.refresh(cleanup_rows[0])
    assert cleanup_rows[0].status == "PENDING"
    assert cleanup_rows[0].error_category == "STORAGE_UNAVAILABLE"

    removed = []
    monkeypatch.setattr(S3PrivateStorage, "__init__", lambda _self: None)
    monkeypatch.setattr(S3PrivateStorage, "delete", lambda _self, key: removed.append(key))
    assert PrivacyService(db_session).run_storage_cleanup(cleanup_rows[0].id) is True
    db_session.refresh(cleanup_rows[0])
    assert cleanup_rows[0].status == "COMPLETE"
    assert cleanup_rows[0].storage_target in removed
    with pytest.raises(JobNotFound):
        JobService(db_session).run(job_id, owner_id)
