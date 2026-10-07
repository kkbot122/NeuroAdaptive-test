import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, Integer, MetaData, Table, Uuid, inspect


def _load_migration():
    path = Path(__file__).parents[2] / "alembic/versions/d3f4a8c1e620_p4_activity_preparation.py"
    spec = importlib.util.spec_from_file_location("p4_preparation_migration", path)
    migration = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(migration)
    return migration


def _previous_schema():
    metadata = MetaData()
    Table("learning_activities", metadata,
          Column("id", Uuid, primary_key=True),
          Column("activity_type", sa.String(32), nullable=False),
          Column("target_concept_ids", sa.JSON, nullable=False))
    Table("lesson_concepts", metadata,
          Column("lesson_id", Uuid, nullable=False),
          Column("concept_id", Uuid, nullable=False))
    Table("lesson_content_artifacts", metadata,
          Column("id", Uuid, primary_key=True),
          Column("lesson_id", Uuid, nullable=False))
    Table("activity_preparations", metadata,
          Column("id", Uuid, primary_key=True),
          Column("activity_id", Uuid),
          Column("lesson_id", Uuid, nullable=False))
    return metadata


def test_p4_migration_backfills_p2_purpose_and_targets_and_guards_question_only_downgrade():
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")
    metadata = _previous_schema()
    metadata.create_all(engine)
    lesson_id, concept_id, activity_id = uuid4(), uuid4(), uuid4()
    preparation_id, artifact_id = uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO learning_activities (id, activity_type, target_concept_ids) "
                    "VALUES (:id, 'NEW_LESSON', :targets)"),
            {"id": activity_id.hex, "targets": json.dumps([str(concept_id)])},
        )
        connection.execute(
            sa.text("INSERT INTO lesson_concepts (lesson_id, concept_id) VALUES (:lesson, :concept)"),
            {"lesson": lesson_id.hex, "concept": concept_id.hex},
        )
        connection.execute(
            sa.text("INSERT INTO activity_preparations (id, activity_id, lesson_id) "
                    "VALUES (:id, :activity, :lesson)"),
            {"id": preparation_id.hex, "activity": activity_id.hex, "lesson": lesson_id.hex},
        )
        connection.execute(
            sa.text("INSERT INTO lesson_content_artifacts (id, lesson_id) VALUES (:id, :lesson)"),
            {"id": artifact_id.hex, "lesson": lesson_id.hex},
        )
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        prep = connection.execute(
            sa.text("SELECT activity_purpose, target_concept_ids FROM activity_preparations WHERE id=:id"),
            {"id": preparation_id.hex},
        ).one()
        content = connection.execute(
            sa.text("SELECT activity_purpose, target_concept_ids FROM lesson_content_artifacts WHERE id=:id"),
            {"id": artifact_id.hex},
        ).one()
        assert prep.activity_purpose == "NEW_LESSON"
        assert json.loads(prep.target_concept_ids) == [str(concept_id)]
        assert content.activity_purpose == "NEW_LESSON"
        assert json.loads(content.target_concept_ids) == [str(concept_id)]

        question_only_activity_id, question_only_prep_id, question_only_artifact_id = uuid4(), uuid4(), uuid4()
        connection.execute(
            sa.text("INSERT INTO learning_activities (id, activity_type, target_concept_ids) "
                    "VALUES (:id, 'TARGETED_PRACTICE', :targets)"),
            {"id": question_only_activity_id.hex, "targets": json.dumps([str(concept_id)])},
        )
        connection.execute(
            sa.text("INSERT INTO activity_preparations "
                    "(id, activity_id, lesson_id, activity_purpose, target_concept_ids) "
                    "VALUES (:id, :activity, NULL, 'TARGETED_PRACTICE', :targets)"),
            {"id": question_only_prep_id.hex, "activity": question_only_activity_id.hex,
             "targets": json.dumps([str(concept_id)])},
        )
        connection.execute(
            sa.text("INSERT INTO lesson_content_artifacts "
                    "(id, lesson_id, activity_purpose, target_concept_ids) "
                    "VALUES (:id, NULL, 'PREREQUISITE_REMEDIATION', :targets)"),
            {"id": question_only_artifact_id.hex, "targets": json.dumps([str(concept_id)])},
        )
        with Operations.context(context), pytest.raises(RuntimeError, match="Cannot restore"):
            migration.downgrade()

        connection.execute(
            sa.text("DELETE FROM activity_preparations WHERE id=:id"), {"id": question_only_prep_id.hex}
        )
        connection.execute(
            sa.text("DELETE FROM lesson_content_artifacts WHERE id=:id"), {"id": question_only_artifact_id.hex}
        )
        with Operations.context(context):
            migration.downgrade()
        assert "activity_purpose" not in {
            column["name"] for column in inspect(connection).get_columns("activity_preparations")
        }
        assert not inspect(connection).get_columns("activity_preparations")[2]["nullable"]
    engine.dispose()
