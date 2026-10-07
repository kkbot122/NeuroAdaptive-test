import importlib.util
from io import StringIO
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, Integer, MetaData, Table, Uuid, inspect
from sqlalchemy.exc import IntegrityError


def _load_migration():
    path = Path(__file__).parents[2] / "alembic/versions/b2e7c19a4d63_async_grounded_preparation.py"
    spec = importlib.util.spec_from_file_location("preparation_migration", path)
    migration = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(migration)
    return migration


def _base_metadata():
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
    return metadata


def test_preparation_migration_compiles_for_postgresql_identifier_limits():
    migration = _load_migration()
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        migration.upgrade()
    sql = output.getvalue()
    assert "CREATE TABLE activity_preparations" in sql
    assert "uq_questions_version_content_hash" in sql
    assert "last_dispatched_at" in sql


def test_preparation_migration_upgrades_downgrades_and_enforces_question_freshness():
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")
    _base_metadata().create_all(engine)
    version_id = uuid4()
    with engine.begin() as connection:
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
            sa.text("INSERT INTO questions (id, course_version_id, content_hash) VALUES (:id, :version, :hash)"),
            {"id": str(uuid4()), "version": str(version_id), "hash": "same-normalized-prompt"},
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text("INSERT INTO questions (id, course_version_id, content_hash) VALUES (:id, :version, :hash)"),
                {"id": str(uuid4()), "version": str(version_id), "hash": "same-normalized-prompt"},
            )

        with Operations.context(context):
            migration.downgrade()
        remaining = set(inspect(connection).get_table_names())
        assert not {
            "lesson_content_artifacts",
            "lesson_content_citations",
            "activity_preparations",
            "prepared_activity_questions",
            "question_sources",
        }.intersection(remaining)
        assert "content_hash" not in {
            column["name"] for column in inspect(connection).get_columns("questions")
        }
    engine.dispose()
