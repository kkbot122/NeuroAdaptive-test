"""PostgreSQL lifecycle checks against a per-test disposable schema.

Set P1_TEST_DATABASE_URL to a disposable PostgreSQL database URL to run these
checks. Each test creates and drops only its own schema.
"""
import importlib.util
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, Integer, MetaData, Table, Uuid, event, inspect, text
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.modules.auth.models import User
from app.modules.courses.models import Course, CourseStatus
from app.modules.curriculum.models import Concept, CourseVersion, CourseVersionStatus
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
from app.modules.learning.service import LearningService
from app.modules.mastery.models import MasteryEvent, Question, QuestionAttempt, QuestionConcept
from app.services.embedding.fake import FakeEmbeddingGateway
from app.services.generation.fake import FakeGenerationGateway


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


def test_postgresql_submit_and_final_grading_interleave_completes_activity(postgres_schema_engine):
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
        question_type="SHORT_TEXT",
        prompt="Explain the concurrency fixture.",
        options=None,
        correct_answer=None,
        rubric=["mentions the fixture"],
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
        given_answer="It mentions the fixture.",
        status=AnswerStatus.AWAITING_GRADING.value,
    )
    seed.add(answer)
    seed.commit()
    ids = (course.id, assessment.id, question.id, user.id)
    seed.close()

    grading_started = threading.Event()
    allow_grading_to_finish = threading.Event()

    class BlockingGeneration(FakeGenerationGateway):
        def generate(self, *args, **kwargs):
            grading_started.set()
            if not allow_grading_to_finish.wait(timeout=10):
                raise TimeoutError("test did not release grading")
            return '{"criteria_met": [true]}'

    generation = BlockingGeneration()
    embeddings = FakeEmbeddingGateway()
    course_id, assessment_id, question_id, owner_id = ids

    def retry_grading():
        with session_factory() as db:
            return LearningService(db, generation, embeddings).retry_grading(
                course_id, assessment_id, question_id, owner_id
            )

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            grading = pool.submit(retry_grading)
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
            graded = grading.result(timeout=10)
            assert graded["grading_state"] == "COMPLETE"
    finally:
        allow_grading_to_finish.set()

    with session_factory() as db:
        assert db.query(LearningActivity.status).filter(LearningActivity.id == activity.id).scalar() == ActivityStatus.COMPLETED.value
        assert db.query(AnswerSubmission).count() == 1
        assert db.query(QuestionAttempt).filter(QuestionAttempt.assessment_question_id.isnot(None)).count() == 1
        assert db.query(MasteryEvent).filter(MasteryEvent.question_attempt_id.isnot(None)).count() == 1
