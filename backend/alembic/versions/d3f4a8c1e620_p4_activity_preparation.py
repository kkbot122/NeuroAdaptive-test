"""Prepare activity-specific content and question sets.

Revision ID: d3f4a8c1e620
Revises: b2e7c19a4d63
"""
from alembic import op
import sqlalchemy as sa


revision = "d3f4a8c1e620"
down_revision = "b2e7c19a4d63"
branch_labels = None
depends_on = None


def _backfill_activity_preparations(bind):
    preparations = sa.table(
        "activity_preparations",
        sa.column("id", sa.Uuid()),
        sa.column("activity_id", sa.Uuid()),
        sa.column("lesson_id", sa.Uuid()),
        sa.column("activity_purpose", sa.String()),
        sa.column("target_concept_ids", sa.JSON()),
    )
    activities = sa.table(
        "learning_activities",
        sa.column("id", sa.Uuid()),
        sa.column("activity_type", sa.String()),
        sa.column("target_concept_ids", sa.JSON()),
    )
    lesson_concepts = sa.table(
        "lesson_concepts",
        sa.column("lesson_id", sa.Uuid()),
        sa.column("concept_id", sa.Uuid()),
    )

    for row in bind.execute(
        sa.select(
            preparations.c.id,
            preparations.c.activity_id,
            preparations.c.lesson_id,
        )
    ):
        if row.activity_id is not None:
            activity = bind.execute(
                sa.select(activities.c.activity_type, activities.c.target_concept_ids).where(
                    activities.c.id == row.activity_id
                )
            ).first()
            purpose = activity.activity_type if activity is not None else "NEW_LESSON"
            targets = activity.target_concept_ids if activity is not None else []
        else:
            purpose = "NEW_LESSON"
            targets = [
                str(item[0])
                for item in bind.execute(
                    sa.select(lesson_concepts.c.concept_id).where(
                        lesson_concepts.c.lesson_id == row.lesson_id
                    )
                )
            ]
        bind.execute(
            preparations.update()
            .where(preparations.c.id == row.id)
            .values(activity_purpose=purpose, target_concept_ids=targets or [])
        )


def _backfill_content_artifacts(bind):
    artifacts = sa.table(
        "lesson_content_artifacts",
        sa.column("id", sa.Uuid()),
        sa.column("lesson_id", sa.Uuid()),
        sa.column("activity_purpose", sa.String()),
        sa.column("target_concept_ids", sa.JSON()),
    )
    lesson_concepts = sa.table(
        "lesson_concepts",
        sa.column("lesson_id", sa.Uuid()),
        sa.column("concept_id", sa.Uuid()),
    )
    for row in bind.execute(sa.select(artifacts.c.id, artifacts.c.lesson_id)):
        targets = (
            [
                str(item[0])
                for item in bind.execute(
                    sa.select(lesson_concepts.c.concept_id).where(
                        lesson_concepts.c.lesson_id == row.lesson_id
                    )
                )
            ]
            if row.lesson_id is not None
            else []
        )
        bind.execute(
            artifacts.update()
            .where(artifacts.c.id == row.id)
            .values(activity_purpose="NEW_LESSON", target_concept_ids=targets)
        )


def upgrade():
    with op.batch_alter_table("lesson_content_artifacts") as batch:
        batch.alter_column("lesson_id", existing_type=sa.Uuid(), nullable=True)
        batch.add_column(
            sa.Column(
                "activity_purpose",
                sa.String(length=32),
                nullable=False,
                server_default="NEW_LESSON",
            )
        )
        batch.add_column(
            sa.Column("target_concept_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )
    with op.batch_alter_table("activity_preparations") as batch:
        batch.alter_column("lesson_id", existing_type=sa.Uuid(), nullable=True)
        batch.add_column(
            sa.Column(
                "activity_purpose",
                sa.String(length=32),
                nullable=False,
                server_default="NEW_LESSON",
            )
        )
        batch.add_column(
            sa.Column("target_concept_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )

    bind = op.get_bind()
    _backfill_activity_preparations(bind)
    _backfill_content_artifacts(bind)


def downgrade():
    bind = op.get_bind()
    for table_name in ("lesson_content_artifacts", "activity_preparations"):
        table = sa.table(table_name, sa.column("lesson_id", sa.Uuid()))
        if bind.execute(sa.select(table.c.lesson_id).where(table.c.lesson_id.is_(None)).limit(1)).first():
            raise RuntimeError(f"Cannot restore {table_name}.lesson_id NOT NULL while question-only records exist")

    with op.batch_alter_table("activity_preparations") as batch:
        batch.drop_column("target_concept_ids")
        batch.drop_column("activity_purpose")
        batch.alter_column("lesson_id", existing_type=sa.Uuid(), nullable=False)
    with op.batch_alter_table("lesson_content_artifacts") as batch:
        batch.drop_column("target_concept_ids")
        batch.drop_column("activity_purpose")
        batch.alter_column("lesson_id", existing_type=sa.Uuid(), nullable=False)
