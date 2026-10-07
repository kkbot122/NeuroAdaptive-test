import importlib.util
from io import StringIO
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, Integer, MetaData, Table, Uuid, inspect


def _load_learning_migration():
    migration_path = Path(__file__).parents[2] / "alembic/versions/a61c9e7d4b20_activity_assessment_saved_progress.py"
    spec = importlib.util.spec_from_file_location("learning_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(migration)
    return migration


def test_learning_migration_compiles_for_postgresql_identifier_limits():
    migration = _load_learning_migration()
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )

    with Operations.context(context):
        migration.upgrade()


def test_learning_migration_upgrades_and_downgrades_a_disposable_sqlite_schema():
    migration = _load_learning_migration()

    engine = sa.create_engine("sqlite://")
    metadata = MetaData()
    Table("users", metadata, Column("id", Integer, primary_key=True))
    for name in ("courses", "course_versions", "adaptation_decisions", "lessons", "questions"):
        Table(name, metadata, Column("id", Uuid, primary_key=True))
    Table("question_attempts", metadata, Column("id", Uuid, primary_key=True))
    metadata.create_all(engine)

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        inspector = inspect(connection)
        names = set(inspector.get_table_names())
        assert {
            "learning_activities",
            "assessment_sessions",
            "assessment_questions",
            "answer_submissions",
        }.issubset(names)
        attempt_columns = {column["name"] for column in inspector.get_columns("question_attempts")}
        assert "assessment_question_id" in attempt_columns
        assert any(
            index["name"] == "uq_learning_activities_one_unfinished_per_course"
            for index in inspector.get_indexes("learning_activities")
        )

        with Operations.context(context):
            migration.downgrade()
        names = set(inspect(connection).get_table_names())
        assert not {
            "learning_activities",
            "assessment_sessions",
            "assessment_questions",
            "answer_submissions",
        }.intersection(names)
        attempt_columns = {column["name"] for column in inspect(connection).get_columns("question_attempts")}
        assert "assessment_question_id" not in attempt_columns
    engine.dispose()
