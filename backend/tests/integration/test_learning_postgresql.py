"""PostgreSQL lifecycle checks against a per-test disposable schema.

Set P1_TEST_DATABASE_URL to a disposable PostgreSQL database URL to run these
checks. Each test creates and drops only its own schema.
"""
import importlib.util
import hashlib
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, Integer, MetaData, Table, Uuid, event, inspect, text
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.modules.auth.models import User
from app.modules.abuse.models import AIUsageDaily
from app.modules.abuse.service import AbuseControlService
from app.modules.documents.chunk_models import Chunk
from app.modules.documents.models import Document, StorageUploadIntent
from app.modules.documents.service import DocumentService, UploadIntentNotFound
from app.modules.documents.storage import ObjectInfo, S3PrivateStorage
from app.modules.courses.models import Course, CourseStatus
from app.modules.courses.service import CourseNotFound
from app.modules.curriculum.models import (
    Concept,
    ConceptSource,
    CourseVersion,
    CourseVersionStatus,
    Lesson,
    LessonConcept,
    Module,
)
from app.modules.learning.models import (
    ActivityStatus,
    AnswerStatus,
    AnswerSubmission,
    AssessmentQuestion,
    AssessmentSession,
    AssessmentStatus,
    AssessmentType,
    LearningActivity,
)
from app.modules.learning import service as learning_service_module
from app.modules.learning.service import LearningService
from app.modules.mastery.models import MasteryEvent, Question, QuestionAttempt, QuestionConcept
from app.modules.privacy.service import PrivacyService
from app.core.problem_details import ProblemDetailException
from app.modules.preparation.models import (
    ActivityPreparation,
    LessonContentArtifact,
    PreparedActivityQuestion,
)
from app.modules.preparation.service import ActivityPreparationService
from app.services.embedding.fake import FakeEmbeddingGateway
from app.services.generation.fake import FakeGenerationGateway
from tests.preparation_generation import PreparationGenerationGateway


@pytest.fixture()
def postgres_schema_engine():
    database_url = os.getenv("P1_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set P1_TEST_DATABASE_URL to a disposable PostgreSQL database")

    schema = f"p1_lifecycle_{uuid.uuid4().hex}"
    admin_engine = sa.create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = sa.create_engine(database_url, pool_size=5, max_overflow=5)

    @event.listens_for(engine, "connect")
    def set_test_search_path(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute(f'SET search_path TO "{schema}", public')
        cursor.close()
        dbapi_connection.commit()

    try:
        yield engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


def _load_learning_migration():
    path = Path(__file__).parents[2] / "alembic/versions/a61c9e7d4b20_activity_assessment_saved_progress.py"
    spec = importlib.util.spec_from_file_location("learning_migration_postgres", path)
    migration = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(migration)
    return migration


def test_learning_migration_upgrades_and_downgrades_a_postgresql_schema(postgres_schema_engine):
    metadata = MetaData()
    Table("users", metadata, Column("id", Integer, primary_key=True))
    for name in ("courses", "course_versions", "adaptation_decisions", "lessons", "questions"):
        Table(name, metadata, Column("id", Uuid, primary_key=True))
    Table("question_attempts", metadata, Column("id", Uuid, primary_key=True))
    metadata.create_all(postgres_schema_engine)
    migration = _load_learning_migration()

    with postgres_schema_engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
        assert {
            "learning_activities",
            "assessment_sessions",
            "assessment_questions",
            "answer_submissions",
        }.issubset(set(inspect(connection).get_table_names()))
        foreign_keys = inspect(connection).get_foreign_keys("question_attempts")
        assert any(fk["name"] == "fk_qattempt_assessment_question" for fk in foreign_keys)

        with Operations.context(context):
            migration.downgrade()
        assert "learning_activities" not in inspect(connection).get_table_names()
        assert "assessment_question_id" not in {
            column["name"] for column in inspect(connection).get_columns("question_attempts")
        }


def test_postgresql_submit_and_final_grading_interleave_completes_activity(postgres_schema_engine, monkeypatch):
    Base.metadata.create_all(postgres_schema_engine)
    session_factory = sessionmaker(bind=postgres_schema_engine, expire_on_commit=False)
    seed = session_factory()
    user = User(email=f"p1-{uuid.uuid4().hex}@example.test", full_name="P1 learner", is_active=True)
    seed.add(user)
    seed.flush()
    course = Course(owner_id=user.id, title="P1 PostgreSQL fixture", status=CourseStatus.PUBLISHED.value)
    seed.add(course)
    seed.flush()
    version = CourseVersion(
        course_id=course.id,
        owner_id=user.id,
        version_number=1,
        status=CourseVersionStatus.READY.value,
    )
    seed.add(version)
    seed.flush()
    course.active_version_id = version.id
    concept = Concept(
        course_id=course.id,
        course_version_id=version.id,
        owner_id=user.id,
        canonical_key=f"p1-{uuid.uuid4().hex}",
        name="Concurrency concept",
        definition="A deterministic concurrency fixture.",
        importance=0.8,
    )
    seed.add(concept)
    seed.flush()
    question = Question(
        course_id=course.id,
        course_version_id=version.id,
        owner_id=user.id,
        question_type="MCQ",
        prompt="Explain the concurrency fixture.",
        options=["A deterministic concurrency fixture.", "An unrelated option", "Another distractor", "A final distractor"],
        correct_answer="A deterministic concurrency fixture.",
        difficulty=0.5,
        is_diagnostic=1,
        version=1,
        model_id="deterministic-test",
        prompt_version="p1-test-v1",
    )
    seed.add(question)
    seed.flush()
    seed.add(QuestionConcept(question_id=question.id, concept_id=concept.id, weight=1.0))
    activity = LearningActivity(
        owner_id=user.id,
        course_id=course.id,
        course_version_id=version.id,
        activity_type="DIAGNOSTIC",
        target_concept_ids=[str(concept.id)],
        status=ActivityStatus.IN_PROGRESS.value,
    )
    seed.add(activity)
    seed.flush()
    assessment = AssessmentSession(
        activity_id=activity.id,
        course_version_id=version.id,
        assessment_type=AssessmentType.DIAGNOSTIC.value,
        status=AssessmentStatus.OPEN.value,
    )
    seed.add(assessment)
    seed.flush()
    assessment_question = AssessmentQuestion(
        session_id=assessment.id,
        question_id=question.id,
        question_version=question.version,
        position=0,
    )
    seed.add(assessment_question)
    seed.flush()
    answer = AnswerSubmission(
        assessment_question_id=assessment_question.id,
        given_answer="A deterministic concurrency fixture.",
        status=AnswerStatus.AWAITING_GRADING.value,
    )
    seed.add(answer)
    seed.commit()
    ids = (course.id, assessment.id, question.id, answer.id, user.id)
    seed.close()

    grading_started = threading.Event()
    allow_grading_to_finish = threading.Event()
    original_grade_attempt = learning_service_module.grade_attempt

    def blocking_grade_attempt(*args, **kwargs):
        grading_started.set()
        if not allow_grading_to_finish.wait(timeout=10):
            raise TimeoutError("test did not release grading")
        return original_grade_attempt(*args, **kwargs)

    monkeypatch.setattr(learning_service_module, "grade_attempt", blocking_grade_attempt)
    generation = FakeGenerationGateway()
    embeddings = FakeEmbeddingGateway()
    course_id, assessment_id, question_id, answer_id, owner_id = ids

    def grade_saved_answer():
        with session_factory() as db:
            return LearningService(db, generation, embeddings).grade_saved_answer(answer_id, owner_id)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            grading = pool.submit(grade_saved_answer)
            try:
                assert grading_started.wait(timeout=10), "grading did not reach the deterministic pause"
                with session_factory() as db:
                    submitted = LearningService(db, FakeGenerationGateway(), embeddings).submit_assessment(
                        course_id, assessment_id, owner_id
                    )
                    assert submitted["submission_state"] == AssessmentStatus.SUBMITTED.value
                    activity_state = db.query(LearningActivity.status).filter(LearningActivity.id == activity.id).scalar()
                    assert activity_state == ActivityStatus.AWAITING_GRADING.value
            finally:
                allow_grading_to_finish.set()
            grading.result(timeout=10)
            with session_factory() as db:
                graded = LearningService(db, FakeGenerationGateway(), embeddings).get_assessment(
                    course_id, assessment_id, owner_id
                )
            assert graded["grading_state"] == "COMPLETE"
            assert graded["graded_answer_count"] == 1
            assert graded["unresolved_answer_count"] == 0
            assert graded["concept_progress_reference_at"] == graded["submitted_at"]
            assert graded["concept_progress"][0]["before_band"] == "Not assessed"
            assert graded["concept_progress"][0]["after_band"] == "Developing"
            with session_factory() as db:
                refreshed = LearningService(db, FakeGenerationGateway(), embeddings).get_assessment(
                    course_id, assessment_id, owner_id
                )
                assert refreshed["concept_progress"] == graded["concept_progress"]
    finally:
        allow_grading_to_finish.set()

    with session_factory() as db:
        assert db.query(LearningActivity.status).filter(LearningActivity.id == activity.id).scalar() == ActivityStatus.COMPLETED.value
        assert db.query(AnswerSubmission).count() == 1
        assert db.query(QuestionAttempt).filter(QuestionAttempt.assessment_question_id.isnot(None)).count() == 1
        assert db.query(MasteryEvent).filter(MasteryEvent.question_attempt_id.isnot(None)).count() == 1


def test_postgresql_concurrent_retries_preserve_worker_lease_and_grade_once(postgres_schema_engine, monkeypatch):
    Base.metadata.create_all(postgres_schema_engine)
    session_factory = sessionmaker(bind=postgres_schema_engine, expire_on_commit=False)
    seed = session_factory()
    user = User(email=f"p5-{uuid.uuid4().hex}@example.test", full_name="P5 learner", is_active=True)
    seed.add(user)
    seed.flush()
    course = Course(owner_id=user.id, title="P5 retry fixture", status=CourseStatus.PUBLISHED.value)
    seed.add(course)
    seed.flush()
    version = CourseVersion(
        course_id=course.id,
        owner_id=user.id,
        version_number=1,
        status=CourseVersionStatus.READY.value,
    )
    seed.add(version)
    seed.flush()
    course.active_version_id = version.id
    concept = Concept(
        course_id=course.id,
        course_version_id=version.id,
        owner_id=user.id,
        canonical_key=f"p5-{uuid.uuid4().hex}",
        name="Retry concurrency concept",
        definition="A deterministic retry fixture.",
        importance=0.8,
    )
    seed.add(concept)
    seed.flush()
    question = Question(
        course_id=course.id,
        course_version_id=version.id,
        owner_id=user.id,
        question_type="MCQ",
        prompt="Select the supported retry answer.",
        options=["Supported answer", "Other A", "Other B", "Other C"],
        correct_answer="Supported answer",
        difficulty=0.5,
        is_diagnostic=1,
        version=1,
        model_id="deterministic-test",
        prompt_version="p5-test-v1",
    )
    seed.add(question)
    seed.flush()
    seed.add(QuestionConcept(question_id=question.id, concept_id=concept.id, weight=1.0))
    activity = LearningActivity(
        owner_id=user.id,
        course_id=course.id,
        course_version_id=version.id,
        activity_type="DIAGNOSTIC",
        target_concept_ids=[str(concept.id)],
        status=ActivityStatus.AWAITING_GRADING.value,
    )
    seed.add(activity)
    seed.flush()
    assessment = AssessmentSession(
        activity_id=activity.id,
        course_version_id=version.id,
        assessment_type=AssessmentType.DIAGNOSTIC.value,
        status=AssessmentStatus.SUBMITTED.value,
    )
    seed.add(assessment)
    seed.flush()
    assessment_question = AssessmentQuestion(
        session_id=assessment.id,
        question_id=question.id,
        question_version=question.version,
        position=0,
    )
    seed.add(assessment_question)
    seed.flush()
    answer = AnswerSubmission(
        assessment_question_id=assessment_question.id,
        given_answer="Supported answer",
        status=AnswerStatus.GRADING_FAILED.value,
        failure_code="grading_unavailable",
        grading_attempt_count=1,
        grading_call_limit=3,
    )
    seed.add(answer)
    seed.commit()
    course_id, assessment_id, question_id, answer_id, owner_id = (
        course.id,
        assessment.id,
        question.id,
        answer.id,
        user.id,
    )
    seed.close()

    retries_started = threading.Barrier(2)
    failed_answer_reads = threading.Barrier(2)
    grading_started = threading.Event()
    allow_grading_to_finish = threading.Event()
    grading_finished = threading.Event()
    worker_errors = []
    unlocked_retry_reads = []
    unlocked_retry_reads_lock = threading.Lock()
    original_grade_attempt = learning_service_module.grade_attempt

    def blocking_grade_attempt(*args, **kwargs):
        grading_started.set()
        if not allow_grading_to_finish.wait(timeout=10):
            raise TimeoutError("test did not release grading")
        return original_grade_attempt(*args, **kwargs)

    monkeypatch.setattr(learning_service_module, "grade_attempt", blocking_grade_attempt)
    generation = FakeGenerationGateway()
    embeddings = FakeEmbeddingGateway()
    worker_thread = None

    def grade_saved_answer():
        try:
            with session_factory() as db:
                LearningService(db, generation, embeddings).grade_saved_answer(answer_id, owner_id)
        except Exception as exc:  # surfaced in the test thread after the worker is released
            worker_errors.append(exc)
        finally:
            grading_finished.set()

    class BlockingDispatcher:
        def __init__(self):
            self.lock = threading.Lock()
            self.started = False

        def enqueue(self, _answer_submission_id, _owner_id):
            nonlocal worker_thread
            with self.lock:
                if not self.started:
                    self.started = True
                    worker_thread = threading.Thread(target=grade_saved_answer)
                    worker_thread.start()
            assert grading_started.wait(timeout=10), "worker did not acquire its grading lease"
            with session_factory() as db:
                active = db.query(AnswerSubmission).filter_by(id=answer_id).one()
                assert active.grading_lease_token is not None
            allow_grading_to_finish.set()
            assert grading_finished.wait(timeout=10), "worker did not finish before the competing retry"

    dispatcher = BlockingDispatcher()

    def synchronize_unlocked_retry_reads(_connection, _cursor, statement, _parameters, _context, _executemany):
        normalized = statement.upper()
        if (
            "FROM ANSWER_SUBMISSIONS" in normalized
            and "ASSESSMENT_QUESTION_ID =" in normalized
            and "FOR UPDATE" not in normalized
        ):
            with unlocked_retry_reads_lock:
                unlocked_retry_reads.append(True)
            slot = failed_answer_reads.wait(timeout=10)
            if slot == 1:
                assert grading_finished.wait(timeout=10), "first retry did not finish grading"

    event.listen(postgres_schema_engine, "after_cursor_execute", synchronize_unlocked_retry_reads)
    def retry_saved_answer():
        retries_started.wait(timeout=10)
        with session_factory() as db:
            return LearningService(db, generation, embeddings, dispatcher).retry_grading(
                course_id, assessment_id, question_id, owner_id
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            retry_futures = [pool.submit(retry_saved_answer) for _ in range(2)]
            for future in retry_futures:
                future.result(timeout=15)
        # The old unlocked query forces two stale reads; the fixed row-locked
        # query serializes them and emits no matching SELECT.
        assert len(unlocked_retry_reads) in {0, 2}
        with session_factory() as db:
            completed = db.query(AnswerSubmission).filter_by(id=answer_id).one()
            assert completed.status == AnswerStatus.GRADED.value
            assert completed.grading_lease_token is None
            assert completed.grading_lease_expires_at is None
    finally:
        allow_grading_to_finish.set()
        if worker_thread is not None:
            worker_thread.join(timeout=15)
        event.remove(postgres_schema_engine, "after_cursor_execute", synchronize_unlocked_retry_reads)

    assert worker_thread is not None and not worker_thread.is_alive()
    assert worker_errors == []
    with session_factory() as db:
        completed = db.query(AnswerSubmission).filter_by(id=answer_id).one()
        assert completed.status == AnswerStatus.GRADED.value
        assert db.query(QuestionAttempt).filter_by(assessment_question_id=assessment_question.id).count() == 1
        assert db.query(MasteryEvent).filter(MasteryEvent.question_attempt_id.isnot(None)).count() == 1


def _load_preparation_migration():
    path = Path(__file__).parents[2] / "alembic/versions/b2e7c19a4d63_async_grounded_preparation.py"
    spec = importlib.util.spec_from_file_location("preparation_migration_postgres", path)
    migration = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(migration)
    return migration


def _load_p4_preparation_migration():
    path = Path(__file__).parents[2] / "alembic/versions/d3f4a8c1e620_p4_activity_preparation.py"
    spec = importlib.util.spec_from_file_location("p4_preparation_migration_postgres", path)
    migration = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(migration)
    return migration


def test_p4_preparation_migration_backfills_and_guards_question_only_downgrade(postgres_schema_engine):
    metadata = MetaData()
    learning_activities = Table(
        "learning_activities",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("activity_type", sa.String(32), nullable=False),
        Column("target_concept_ids", sa.JSON, nullable=False),
    )
    lesson_concepts = Table(
        "lesson_concepts",
        metadata,
        Column("lesson_id", Uuid, nullable=False),
        Column("concept_id", Uuid, nullable=False),
    )
    artifacts = Table(
        "lesson_content_artifacts",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("lesson_id", Uuid, nullable=False),
    )
    preparations = Table(
        "activity_preparations",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("activity_id", Uuid),
        Column("lesson_id", Uuid, nullable=False),
    )
    metadata.create_all(postgres_schema_engine)
    migration = _load_p4_preparation_migration()
    lesson_id, concept_id, activity_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    prep_id, artifact_id = uuid.uuid4(), uuid.uuid4()
    with postgres_schema_engine.begin() as connection:
        connection.execute(
            learning_activities.insert().values(
                id=activity_id,
                activity_type="NEW_LESSON",
                target_concept_ids=[str(concept_id)],
            )
        )
        connection.execute(lesson_concepts.insert().values(lesson_id=lesson_id, concept_id=concept_id))
        connection.execute(artifacts.insert().values(id=artifact_id, lesson_id=lesson_id))
        connection.execute(
            preparations.insert().values(id=prep_id, activity_id=activity_id, lesson_id=lesson_id)
        )
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        prepared = connection.execute(
            sa.text("SELECT activity_purpose, target_concept_ids FROM activity_preparations WHERE id=:id"),
            {"id": prep_id},
        ).one()
        content = connection.execute(
            sa.text("SELECT activity_purpose, target_concept_ids FROM lesson_content_artifacts WHERE id=:id"),
            {"id": artifact_id},
        ).one()
        assert prepared.activity_purpose == "NEW_LESSON"
        assert prepared.target_concept_ids == [str(concept_id)]
        assert content.activity_purpose == "NEW_LESSON"
        assert content.target_concept_ids == [str(concept_id)]

        question_only_activity_id, question_only_prep_id, question_only_artifact_id = (
            uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        )
        connection.execute(
            learning_activities.insert().values(
                id=question_only_activity_id,
                activity_type="TARGETED_PRACTICE",
                target_concept_ids=[str(concept_id)],
            )
        )
        connection.execute(
            preparations.insert().values(
                id=question_only_prep_id,
                activity_id=question_only_activity_id,
                lesson_id=None,
            )
        )
        connection.execute(artifacts.insert().values(id=question_only_artifact_id, lesson_id=None))
        with Operations.context(context), pytest.raises(RuntimeError, match="Cannot restore"):
            migration.downgrade()

        connection.execute(preparations.delete().where(preparations.c.id == question_only_prep_id))
        connection.execute(artifacts.delete().where(artifacts.c.id == question_only_artifact_id))
        with Operations.context(context):
            migration.downgrade()
        assert "activity_purpose" not in {
            column["name"] for column in inspect(connection).get_columns("activity_preparations")
        }
        assert not next(
            column for column in inspect(connection).get_columns("activity_preparations")
            if column["name"] == "lesson_id"
        )["nullable"]


def test_preparation_migration_upgrades_and_downgrades_a_postgresql_schema(postgres_schema_engine):
    metadata = MetaData()
    Table("users", metadata, Column("id", Integer, primary_key=True))
    for name in ("courses", "course_versions", "lessons", "chunks", "learning_activities"):
        Table(name, metadata, Column("id", Uuid, primary_key=True))
    Table(
        "questions",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("course_version_id", Uuid, nullable=False),
    )
    metadata.create_all(postgres_schema_engine)
    migration = _load_preparation_migration()
    version_id = uuid.uuid4()

    with postgres_schema_engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
        tables = set(inspect(connection).get_table_names())
        assert {
            "lesson_content_artifacts",
            "lesson_content_citations",
            "activity_preparations",
            "prepared_activity_questions",
            "question_sources",
        }.issubset(tables)
        assert "last_dispatched_at" in {
            column["name"] for column in inspect(connection).get_columns("activity_preparations")
        }
        connection.execute(
            text("INSERT INTO questions (id, course_version_id, content_hash) VALUES (:id, :version, :hash)"),
            {"id": str(uuid.uuid4()), "version": str(version_id), "hash": "same-prompt"},
        )
        with pytest.raises(sa.exc.IntegrityError), connection.begin_nested():
            connection.execute(
                text("INSERT INTO questions (id, course_version_id, content_hash) VALUES (:id, :version, :hash)"),
                {"id": str(uuid.uuid4()), "version": str(version_id), "hash": "same-prompt"},
            )
        with Operations.context(context):
            migration.downgrade()
        assert not {
            "lesson_content_artifacts",
            "lesson_content_citations",
            "activity_preparations",
            "prepared_activity_questions",
            "question_sources",
        }.intersection(set(inspect(connection).get_table_names()))
        assert "content_hash" not in {
            column["name"] for column in inspect(connection).get_columns("questions")
        }


def test_postgresql_concurrent_preparation_requests_and_workers_deduplicate_artifacts(postgres_schema_engine):
    Base.metadata.create_all(postgres_schema_engine)
    session_factory = sessionmaker(bind=postgres_schema_engine, expire_on_commit=False)
    seed = session_factory()
    source_text = "A source passage supports the first course concept and its assessment."
    checksum = hashlib.sha256(source_text.encode()).hexdigest()
    user = User(email=f"p2-{uuid.uuid4().hex}@example.test", full_name="P2 learner", is_active=True)
    seed.add(user)
    seed.flush()
    course = Course(owner_id=user.id, title="P2 PostgreSQL", status=CourseStatus.PUBLISHED.value)
    seed.add(course)
    seed.flush()
    version = CourseVersion(
        course_id=course.id,
        owner_id=user.id,
        version_number=1,
        status=CourseVersionStatus.READY.value,
        source_fingerprint=hashlib.sha256(checksum.encode()).hexdigest(),
    )
    seed.add(version)
    seed.flush()
    course.active_version_id = version.id
    concept = Concept(
        course_id=course.id,
        course_version_id=version.id,
        owner_id=user.id,
        canonical_key=f"p2-{uuid.uuid4().hex}",
        name="Concurrent preparation",
        definition="One deterministic concept for the PostgreSQL worker test.",
        importance=0.8,
    )
    seed.add(concept)
    seed.flush()
    document = Document(
        course_id=course.id,
        owner_id=user.id,
        filename="source.txt",
        role="STUDY",
        status="EXTRACTED",
        storage_path=f"/tmp/{uuid.uuid4().hex}.txt",
        size_bytes=len(source_text),
        checksum_sha256=checksum,
    )
    seed.add(document)
    seed.flush()
    chunk = Chunk(
        id=uuid.uuid4(),
        document_id=document.id,
        course_id=course.id,
        owner_id=user.id,
        position=0,
        text=source_text,
        char_count=len(source_text),
        token_count=12,
    )
    seed.add(chunk)
    seed.flush()
    seed.add(ConceptSource(concept_id=concept.id, chunk_id=chunk.id, course_id=course.id, owner_id=user.id))
    module = Module(course_version_id=version.id, position=0, title="Module")
    seed.add(module)
    seed.flush()
    lesson = Lesson(module_id=module.id, position=0, title="Lesson", objective="Teach one concept")
    seed.add(lesson)
    seed.flush()
    seed.add(LessonConcept(lesson_id=lesson.id, concept_id=concept.id, weight=1.0))
    activity = LearningActivity(
        owner_id=user.id,
        course_id=course.id,
        course_version_id=version.id,
        activity_type="NEW_LESSON",
        target_concept_ids=[str(concept.id)],
        lesson_id=lesson.id,
        status=ActivityStatus.READY.value,
        presentation_format="detailed",
    )
    seed.add(activity)
    seed.commit()
    course_id, activity_id, owner_id = course.id, activity.id, user.id
    concept_id, chunk_id = concept.id, chunk.id
    seed.close()

    lesson_output = {
        "insufficient_evidence": False,
        "objective": [{"action": "explain", "concept_id": str(concept_id), "citation_chunk_ids": [str(chunk_id)]}],
        "explanation": [{"text": "The source explains the first concept in simple terms.", "concept_ids": [str(concept_id)], "citation_chunk_ids": [str(chunk_id)]}],
        "example": [{"text": "This example follows the supplied source passage.", "concept_ids": [str(concept_id)], "citation_chunk_ids": [str(chunk_id)]}],
        "recap": [{"text": "The recap repeats the source supported idea.", "concept_ids": [str(concept_id)], "citation_chunk_ids": [str(chunk_id)]}],
    }
    questions = []
    for index in range(5):
        if index == 4:
            questions.append({
                "question_type": "SHORT_TEXT",
                "concept_id": str(concept_id),
                "prompt": "Explain the source supported idea and its defining feature.",
                "expected_reasoning": "A supported response identifies the idea and explains a feature from the passage.",
                "source_chunk_ids": [str(chunk_id)],
                "rubric": [
                    {
                        "text": "Identifies the source supported idea.",
                        "expected_reasoning": "The response names the idea in the passage.",
                        "source_chunk_ids": [str(chunk_id)],
                    },
                    {
                        "text": "Explains one defining feature.",
                        "expected_reasoning": "The response explains a feature described in the passage.",
                        "source_chunk_ids": [str(chunk_id)],
                    },
                    {
                        "text": "Connects the idea to its source.",
                        "expected_reasoning": "The response ties the idea to the supplied passage.",
                        "source_chunk_ids": [str(chunk_id)],
                    },
                ],
            })
            continue
        answer = f"Supported option {index}"
        questions.append({
            "question_type": "MCQ",
            "concept_id": str(concept_id),
            "prompt": f"Which source supported idea is tested in question number {index}?",
            "options": [answer, f"Distractor {index} B", f"Distractor {index} C", f"Distractor {index} D"],
            "correct_answer": answer,
            "explanation": "The passage directly supports this answer.",
            "source_chunk_ids": [str(chunk_id)],
        })
    generation = PreparationGenerationGateway().when_prompt_contains(
        "Prepare the first lesson", json.dumps(lesson_output)
    ).when_prompt_contains(
        "Write exactly 5 single-answer", json.dumps({"insufficient_evidence": False, "questions": questions})
    ).when_prompt_contains(
        "is also a supported correct answer.", '{"supported": false}'
    ).set_default('{"supported": true}')

    class ConcurrentDispatcher:
        def __init__(self):
            self.lock = threading.Lock()
            self.jobs = []

        def enqueue(self, preparation_id, dispatched_owner_id, *, speculative=False):
            with self.lock:
                self.jobs.append((preparation_id, dispatched_owner_id, speculative))

    dispatcher = ConcurrentDispatcher()

    def request_preparation():
        with session_factory() as db:
            return ActivityPreparationService(db, generation, dispatcher).request_activity(
                course_id, activity_id, owner_id
            ).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        preparation_ids = list(pool.map(lambda _: request_preparation(), range(2)))
    assert len(set(preparation_ids)) == 1
    assert len(dispatcher.jobs) == 1
    preparation_id = preparation_ids[0]

    def run_preparation():
        with session_factory() as db:
            return ActivityPreparationService(db, generation, dispatcher).run(preparation_id, owner_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: run_preparation(), range(2)))
    with session_factory() as db:
        preparation = db.query(ActivityPreparation).filter_by(id=preparation_id).one()
        assert preparation.status == "READY"
        assert db.query(PreparedActivityQuestion).filter_by(preparation_id=preparation_id).count() == 5
        assert db.query(Question).filter_by(course_version_id=version.id, is_diagnostic=0).count() == 5
        assert db.query(LessonContentArtifact).filter_by(course_version_id=version.id).count() == 1
    assert len(generation.calls) > 0


def test_postgresql_daily_ai_budget_reservations_are_atomic(postgres_schema_engine):
    Base.metadata.create_all(postgres_schema_engine)
    session_factory = sessionmaker(bind=postgres_schema_engine, expire_on_commit=False)
    with session_factory() as db:
        user = User(email=f"quota-{uuid.uuid4().hex}@example.test", full_name="Quota", is_active=True)
        db.add(user)
        db.commit()
        owner_id = user.id

    workers = 20
    budget = 7
    barrier = threading.Barrier(workers)

    def reserve_one():
        barrier.wait(timeout=10)
        with session_factory() as db:
            try:
                AbuseControlService(db).enforce_daily_budget(owner_id, budget=budget)
                return True
            except ProblemDetailException:
                return False

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda _index: reserve_one(), range(workers)))

    assert sum(results) == budget
    with session_factory() as db:
        usage = db.query(AIUsageDaily).filter_by(owner_id=owner_id).one()
        assert usage.call_count == budget


def test_postgresql_source_finalization_and_course_deletion_serialize_without_lock_inversion(
    postgres_schema_engine, monkeypatch,
):
    from app.modules.privacy import tasks

    Base.metadata.create_all(postgres_schema_engine)
    session_factory = sessionmaker(bind=postgres_schema_engine, expire_on_commit=False)
    content = b"A replacement source used to verify course, intent, and document lock ordering."
    checksum = hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(S3PrivateStorage, "__init__", lambda _self: None)
    monkeypatch.setattr(S3PrivateStorage, "inspect", lambda _self, _key: ObjectInfo(len(content)))
    monkeypatch.setattr(S3PrivateStorage, "checksum_sha256", lambda _self, _key: checksum)
    monkeypatch.setattr(S3PrivateStorage, "read", lambda _self, _key: content)
    monkeypatch.setattr(tasks.cleanup_storage_object, "apply_async", lambda **_kwargs: None)

    with session_factory() as seed:
        user = User(email=f"p7-race-{uuid.uuid4().hex}@example.test", full_name="P7 race", is_active=True)
        seed.add(user)
        seed.flush()
        course = Course(owner_id=user.id, title="P7 finalization deletion race")
        seed.add(course)
        seed.flush()
        original = Document(
            course_id=course.id, owner_id=user.id, filename="old.txt", role="STUDY",
            storage_path="private/p7-old.txt", storage_key="private/p7-old.txt",
            size_bytes=12, checksum_sha256="a" * 64,
        )
        seed.add(original)
        seed.flush()
        intent = StorageUploadIntent(
            course_id=course.id, owner_id=user.id, object_key=f"private/p7-candidate-{uuid.uuid4().hex}.txt",
            filename="new.txt", role="STUDY", replaces_document_id=original.id,
            expected_checksum_sha256=checksum, expected_size_bytes=len(content),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        seed.add(intent)
        seed.commit()
        owner_id, course_id, intent_id = user.id, course.id, intent.id

    ready = threading.Barrier(2)
    lock_order: list[str] = []
    lock_order_guard = threading.Lock()

    def record_sql(connection, _cursor, statement, _parameters, _context, _executemany):
        operation = connection.info.get("p7_source_delete_operation")
        if operation != "finalize":
            return
        sql = statement.lower()
        label = None
        if "for update" in sql and " from courses " in f" {sql} ":
            label = "course"
        elif "for update" in sql and " from storage_upload_intents " in f" {sql} ":
            label = "intent"
        elif sql.lstrip().startswith("delete from documents"):
            label = "document"
        if label:
            with lock_order_guard:
                lock_order.append(label)

    event.listen(postgres_schema_engine, "before_cursor_execute", record_sql)

    def finalize():
        with session_factory() as db:
            db.connection().info["p7_source_delete_operation"] = "finalize"
            ready.wait(timeout=10)
            try:
                return DocumentService(db).finalize_upload_result(course_id, intent_id, owner_id)
            except (CourseNotFound, UploadIntentNotFound):
                return None
            finally:
                db.connection().info.pop("p7_source_delete_operation", None)

    def delete_course():
        with session_factory() as db:
            ready.wait(timeout=10)
            PrivacyService(db).delete_owned_course(course_id, owner_id)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            finalize_future = pool.submit(finalize)
            delete_future = pool.submit(delete_course)
            finalize_future.result(timeout=20)
            delete_future.result(timeout=20)
    finally:
        event.remove(postgres_schema_engine, "before_cursor_execute", record_sql)

    assert lock_order and lock_order[0] == "course"
    if "intent" in lock_order:
        assert lock_order.index("course") < lock_order.index("intent")
    if "document" in lock_order:
        assert lock_order.index("intent") < lock_order.index("document")
    with session_factory() as db:
        assert db.query(Course).filter_by(id=course_id).first() is None
        assert db.query(StorageUploadIntent).filter_by(id=intent_id).first() is None
        assert db.query(Document).filter_by(course_id=course_id).count() == 0
