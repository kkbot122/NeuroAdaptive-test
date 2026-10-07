"""Required CI seams: migrations, PostgreSQL concurrency/fencing, real Redis worker."""
import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Event

import pytest
from sqlalchemy import create_engine, inspect, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.modules.auth.models import User
from app.modules.courses.models import Course
from app.modules.curriculum.models import CourseVersion
from app.modules.documents.models import Document
from app.modules.jobs.models import ProcessingJob, ProcessingStage
from app.modules.jobs.service import JobAlreadyActive, JobService
from app.services.embedding.fake import FakeEmbeddingGateway
from app.services.generation.fake import FakeGenerationGateway
from app.services.vectorstore.pgvector_store import PgVectorStore


pytestmark = pytest.mark.integration


def integration_url(name):
    value = os.getenv(name)
    if not value:
        if os.getenv("REQUIRE_INTEGRATION") == "1":
            pytest.fail(f"Required integration configuration missing: {name}")
        pytest.skip(f"Local fast suite: configure {name} to run this integration seam")
    return value


@pytest.fixture()
def pg_engine():
    url = integration_url("PGVECTOR_TEST_DATABASE_URL")
    assert make_url(url).database.startswith("neurolearn_test"), "Disposable database required"
    engine = create_engine(url)
    yield engine
    engine.dispose()


def owned_course(engine):
    with Session(engine) as db:
        owner = User(email=f"integration-{uuid.uuid4()}@example.com", is_active=True)
        db.add(owner)
        db.flush()
        course = Course(owner_id=owner.id, title="Synthetic integration course")
        db.add(course)
        db.commit()
        return owner.id, course.id


def test_migrated_schema_and_expression_index_match(pg_engine):
    from app.db.base import Base
    tables = set(inspect(pg_engine).get_table_names())
    assert set(Base.metadata.tables).issubset(tables)
    with pg_engine.connect() as db:
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "83c6e2f41b85"
        definition = db.execute(text("SELECT indexdef FROM pg_indexes WHERE indexname='ix_chunks_embedding_hnsw' AND schemaname='public'")).scalar_one()
        assert "USING hnsw" in definition and "halfvec(3072)" in definition and "halfvec_cosine_ops" in definition
    env = dict(os.environ, DATABASE_URL=str(pg_engine.url.render_as_string(hide_password=False)))
    subprocess.run([sys.executable, "-m", "alembic", "check"], env=env, check=True, capture_output=True)


def test_incremental_upgrade_preserves_existing_rows(pg_engine):
    # Only this uniquely named disposable database is created and removed.
    name = "neurolearn_test_upgrade_" + uuid.uuid4().hex[:12]
    admin = create_engine(pg_engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as db:
        db.execute(text(f'CREATE DATABASE "{name}"'))
    target = pg_engine.url.set(database=name)
    env = dict(os.environ, DATABASE_URL=target.render_as_string(hide_password=False))
    try:
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "f7b3d29e1c64"], env=env, check=True, capture_output=True)
        old = create_engine(target)
        with old.begin() as db:
            user_id = db.execute(text("INSERT INTO users(email, is_active) VALUES ('synthetic-existing@example.com', true) RETURNING id")).scalar_one()
        old.dispose()
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True, capture_output=True)
        new = create_engine(target)
        with new.connect() as db:
            assert db.execute(text("SELECT email FROM users WHERE id=:id"), {"id": user_id}).scalar_one() == "synthetic-existing@example.com"
            assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "83c6e2f41b85"
        new.dispose()
    finally:
        with admin.connect() as db:
            db.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


def test_simultaneous_course_job_creation_is_atomic(pg_engine):
    owner_id, course_id = owned_course(pg_engine)
    barrier = Barrier(2)
    def create():
        with Session(pg_engine) as db:
            barrier.wait(timeout=5)
            try:
                return str(JobService(db).create_for_course(course_id, owner_id).id)
            except JobAlreadyActive:
                db.rollback()
                return "conflict"
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: create(), range(2)))
    assert results.count("conflict") == 1
    with Session(pg_engine) as db:
        assert db.query(ProcessingJob).filter_by(course_id=course_id).count() == 1


@pytest.mark.parametrize("blocked", ["embedding", "edges"])
def test_takeover_fences_slow_worker_and_preserves_completed_stages(pg_engine, tmp_path, blocked):
    owner_id, course_id = owned_course(pg_engine)
    source = tmp_path / "fixture.txt"
    source.write_text("An algorithm is a sequence of steps that computes a result. " * 10)
    with Session(pg_engine) as db:
        db.add(Document(course_id=course_id, owner_id=owner_id, filename="fixture.txt", storage_path=str(source), size_bytes=source.stat().st_size, checksum_sha256=uuid.uuid4().hex*2))
        db.commit()
        job_id = JobService(db).create_for_course(course_id, owner_id).id
    entered, released = Event(), Event()
    class SlowEmbedding(FakeEmbeddingGateway):
        def embed_texts(self, texts):
            entered.set()
            assert released.wait(10), "Test provider was not released"
            return super().embed_texts(texts)
    def generation(slow=False):
        class Gateway(FakeGenerationGateway):
            def generate(self, prompt, **kwargs):
                if slow and blocked == "edges" and "Propose prerequisite" in prompt:
                    entered.set()
                    assert released.wait(10), "Test provider was not released"
                return super().generate(prompt, **kwargs)
        return Gateway().set_default('{"concepts":[{"name":"Algorithm","definition":"A sequence of steps."},{"name":"Result","definition":"The value returned by a computation."}],"edges":[]}')
    def first():
        with Session(pg_engine) as db:
            return JobService(db, SlowEmbedding(3072) if blocked == "embedding" else FakeEmbeddingGateway(3072), PgVectorStore(db), generation(slow=True)).run(job_id, owner_id).status
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(first)
        assert entered.wait(10)
        with Session(pg_engine) as db:
            first_token = db.get(ProcessingJob, job_id).lease_token
            assert first_token is not None
            db.execute(update(ProcessingJob).where(ProcessingJob.id == job_id).values(lease_expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)))
            db.commit()
        try:
            with Session(pg_engine) as db:
                assert JobService(db, FakeEmbeddingGateway(3072), PgVectorStore(db), generation()).run(job_id, owner_id).status == "READY"
        finally:
            released.set()
        assert future.result(timeout=10) == "READY"
    with Session(pg_engine) as db:
        assert db.query(CourseVersion).filter_by(course_id=course_id).count() == 1
        stages = {s.name: s for s in db.query(ProcessingStage).filter_by(job_id=job_id)}
        assert stages["EXTRACTING"].attempts == 1
        assert stages["INDEXING"].attempts == (2 if blocked == "embedding" else 1)
        assert db.get(ProcessingJob, job_id).lease_token is None


def test_real_redis_worker_duplicate_delivery_is_inert(pg_engine, tmp_path):
    redis_url = integration_url("REDIS_TEST_URL")
    from redis import Redis
    assert Redis.from_url(redis_url).ping()
    from app.core.celery_app import celery_app
    from app.modules.jobs.tasks import run_processing_job
    celery_app.conf.update(broker_url=redis_url, result_backend=redis_url)
    queue = "baseline-" + uuid.uuid4().hex
    env = dict(os.environ, DATABASE_URL=pg_engine.url.render_as_string(hide_password=False),
               CELERY_BROKER_URL=redis_url, CELERY_RESULT_BACKEND=redis_url)
    log = (tmp_path / "worker.log").open("w")
    worker = subprocess.Popen([sys.executable, "-m", "celery", "-A", "app.core.celery_app.celery_app", "worker", "--pool=solo", "--concurrency=1", "-Q", queue, "--loglevel=WARNING"], env=env, stdout=log, stderr=log)
    try:
        owner_id, course_id = owned_course(pg_engine)
        with Session(pg_engine) as db:
            job_id = JobService(db).create_for_course(course_id, owner_id).id
        # No sources deliberately exercises durable failure without any AI call.
        first = run_processing_job.apply_async(args=[str(job_id), owner_id], queue=queue)
        first.get(timeout=25)
        second = run_processing_job.apply_async(args=[str(job_id), owner_id], queue=queue)
        second.get(timeout=25)
        with Session(pg_engine) as db:
            assert db.get(ProcessingJob, job_id).status == "FAILED"
            stage = db.query(ProcessingStage).filter_by(job_id=job_id, name="VALIDATING").one()
            assert stage.attempts == 1
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=5)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait()
        log.close()


@pytest.mark.parametrize("artifact_status", ["DRAFT", "READY"])
def test_curriculum_artifact_survives_interruption_before_stage_commit(pg_engine, tmp_path, artifact_status):
    owner_id, course_id = owned_course(pg_engine)
    source = tmp_path / "fixture.txt"
    source.write_text("Algorithms compute results through a sequence of steps. " * 10)
    gateway = FakeGenerationGateway().set_default('{"concepts":[{"name":"Algorithm","definition":"A sequence of steps."}],"edges":[]}')
    with Session(pg_engine) as db:
        db.add(Document(course_id=course_id, owner_id=owner_id, filename="fixture.txt", storage_path=str(source), size_bytes=source.stat().st_size, checksum_sha256=uuid.uuid4().hex*2))
        db.commit()
        service = JobService(db, FakeEmbeddingGateway(3072), PgVectorStore(db), gateway)
        job = service.create_for_course(course_id, owner_id)
        assert service.run(job.id, owner_id).status == "READY"
        version = db.query(CourseVersion).filter_by(processing_job_id=job.id).one()
        version_id = version.id
        calls = len(gateway.calls)
        # Persisted state at either crash window: committed artifacts but the
        # stage acknowledgement/validation not yet committed by the worker.
        version.status = artifact_status
        stage = db.query(ProcessingStage).filter_by(job_id=job.id, name="EXTRACTING_CONCEPTS").one()
        stage.status = "RUNNING"
        job.status = "RUNNING"
        job.lease_token = uuid.uuid4()
        job.lease_expires_at = datetime.now(timezone.utc)-timedelta(seconds=1)
        db.commit()
        service.prepare_retry(job.id, owner_id)
        assert service.run(job.id, owner_id).status == "READY"
        assert len(gateway.calls) == calls  # same committed artifact, no regeneration
        assert db.query(CourseVersion).filter_by(course_id=course_id).count() == 1
        assert db.query(CourseVersion).filter_by(processing_job_id=job.id).one().id == version_id


def test_existing_course_flow_through_http_and_real_postgres(pg_engine):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db.session import get_db
    from app.modules.jobs.router import _service as job_dependency, _dispatcher
    from app.modules.preparation.dependencies import get_preparation_service as preparation_dependency
    from app.modules.tutor.router import _service as tutor_dependency
    from app.modules.tutor.service import TutorService
    from app.modules.documents.chunk_models import Chunk
    from tests.conftest import auth_headers
    import json
    owner_id, _ = owned_course(pg_engine)
    saved = dict(app.dependency_overrides)
    with Session(pg_engine) as db:
        email = db.get(User, owner_id).email
        generation = FakeGenerationGateway().set_default('{"concepts":[{"name":"Algorithm","definition":"A sequence of steps."}],"edges":[]}')
        def job_service():
            return JobService(db, FakeEmbeddingGateway(3072), PgVectorStore(db), generation)
        def database():
            yield db
        class Inline:
            def enqueue(self, job_id, owner_id):
                job_service().run(job_id, owner_id)
        def tutor_service():
            chunk = db.query(Chunk).filter_by(course_id=course_id).first()
            answer = json.dumps({"insufficient_evidence":False,"answer_markdown":"An algorithm is a sequence of steps.",
                "claims":[{"text":"An algorithm is a sequence of steps.","chunk_id":str(chunk.id)}]})
            tutor_generation = FakeGenerationGateway().set_default(answer)
            return TutorService(db, tutor_generation, FakeEmbeddingGateway(3072), PgVectorStore(db),
                cheap_generation=FakeGenerationGateway().set_default('{"supported": true}'))
        app.dependency_overrides.update({get_db: database, job_dependency: job_service, _dispatcher: Inline, tutor_dependency: tutor_service, preparation_dependency: lambda: None})
        try:
            with TestClient(app) as client:
                headers = auth_headers(email)
                created = client.post("/api/v1/courses", headers=headers, json={"title":"HTTP fixture"})
                assert created.status_code == 201
                course_id = uuid.UUID(created.json()["id"])
                uploaded = client.post(f"/api/v1/courses/{course_id}/documents/paste", headers=headers,
                    json={"title":"Notes","text":"An algorithm is a sequence of steps computing a result from input. "*20})
                assert uploaded.status_code == 201
                started = client.post(f"/api/v1/courses/{course_id}/process", headers=headers)
                assert started.status_code == 202
                recovered = client.get(f"/api/v1/courses/{course_id}/jobs/latest", headers=headers).json()
                assert recovered["id"] == started.json()["id"] and recovered["status"] == "READY"
                structure = client.get(f"/api/v1/courses/{course_id}/structure", headers=headers).json()
                assert structure["status"] == "READY"
                lesson_id = structure["modules"][0]["lessons"][0]["id"]
                assert client.post(f"/api/v1/courses/{course_id}/publish-structure", headers=headers).status_code == 200
                assert client.get(f"/api/v1/courses/{course_id}", headers=headers).json()["status"] == "PUBLISHED"
                taught = client.get(f"/api/v1/courses/{course_id}/lessons/{lesson_id}/content", headers=headers)
                assert taught.status_code == 200 and taught.json()["grounding_mode"] == "source_only"
                citation = taught.json()["citations"][0]
                source = client.get(f"/api/v1/courses/{course_id}/chunks/{citation['chunk_id']}", headers=headers)
                assert source.status_code == 200 and source.json()["page_start"] == 1
                assert source.json()["filename"] == "Notes.txt"
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(saved)


def test_historical_destructive_upgrade_is_blocked_without_data_loss(pg_engine):
    name = "neurolearn_test_legacy_"+uuid.uuid4().hex[:12]
    admin = create_engine(pg_engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as db:
        db.execute(text(f'CREATE DATABASE "{name}"'))
    target = pg_engine.url.set(database=name)
    env = dict(os.environ, DATABASE_URL=target.render_as_string(hide_password=False))
    try:
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "ea9facb393a3"], env=env, check=True, capture_output=True)
        old = create_engine(target)
        with old.begin() as db:
            db.execute(text("CREATE TABLE articles (id integer primary key, title text)"))
            db.execute(text("INSERT INTO articles VALUES (1, 'synthetic preservation fixture')"))
        refused = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, capture_output=True, text=True)
        assert refused.returncode != 0 and "data-preservation" in refused.stderr
        with old.connect() as db:
            assert db.execute(text("SELECT count(*) FROM articles")).scalar_one() == 1
            assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "ea9facb393a3"
        old.dispose()
    finally:
        with admin.connect() as db:
            db.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


def test_source_upload_and_finalization_serialize_through_http(pg_engine):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db.session import get_db
    from tests.conftest import auth_headers
    owner_id, course_id = owned_course(pg_engine)
    with Session(pg_engine) as db:
        email = db.get(User, owner_id).email
    def database():
        with Session(pg_engine) as db:
            yield db
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = database
    try:
        with TestClient(app) as client:
            headers = auth_headers(email)
            initial = client.post(f"/api/v1/courses/{course_id}/documents", headers=headers,
                files={"file":("initial.txt",b"An algorithm computes an output using defined steps. "*20,"text/plain")})
            assert initial.status_code == 201
            barrier = Barrier(2)
            def upload():
                barrier.wait(timeout=5)
                return client.post(f"/api/v1/courses/{course_id}/documents", headers=headers,
                    files={"file":("second.txt",b"A graph connects vertices with directed or undirected edges. "*20,"text/plain")}).status_code
            def finalize():
                barrier.wait(timeout=5)
                return client.post(f"/api/v1/courses/{course_id}/finalize-sources", headers=headers).status_code
            with ThreadPoolExecutor(max_workers=2) as pool:
                upload_result, finalized = pool.submit(upload), pool.submit(finalize)
                upload_status, finalize_status = upload_result.result(timeout=10), finalized.result(timeout=10)
            assert finalize_status == 200
            assert upload_status in (201,409)
            with Session(pg_engine) as db:
                assert db.get(Course, course_id).sources_finalized_at is not None
                assert db.query(Document).filter_by(course_id=course_id).count() == (2 if upload_status==201 else 1)
            refused = client.post(f"/api/v1/courses/{course_id}/documents", headers=headers,
                files={"file":("late.txt",b"Late synthetic source. "*20,"text/plain")})
            assert refused.status_code == 409
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)
