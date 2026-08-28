"""scope concepts and prerequisites to course_version_id

Without this, regenerating a course would mix every prior version's
concepts into one course-scoped pile: a FAILED version's concepts would
pollute queries for the active one, and a third regeneration's carryover
comparison would match against a stale blend of v1 and v2 instead of
specifically the version being replaced.

Revision ID: d4f8b2e91a37
Revises: c7d2a4f65e13
Create Date: 2026-08-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4f8b2e91a37"
down_revision: Union[str, None] = "c7d2a4f65e13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("concepts") as batch:
        batch.add_column(sa.Column("course_version_id", sa.Uuid(), nullable=False))
        batch.create_foreign_key(
            "fk_concepts_course_version_id", "course_versions", ["course_version_id"], ["id"]
        )
    op.create_index("ix_concepts_course_version_id", "concepts", ["course_version_id"])

    with op.batch_alter_table("concept_prerequisites") as batch:
        batch.add_column(sa.Column("course_version_id", sa.Uuid(), nullable=False))
        batch.create_foreign_key(
            "fk_concept_prerequisites_course_version_id",
            "course_versions", ["course_version_id"], ["id"],
        )
    op.create_index(
        "ix_concept_prerequisites_course_version_id", "concept_prerequisites", ["course_version_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_concept_prerequisites_course_version_id", table_name="concept_prerequisites")
    with op.batch_alter_table("concept_prerequisites") as batch:
        batch.drop_constraint("fk_concept_prerequisites_course_version_id", type_="foreignkey")
        batch.drop_column("course_version_id")

    op.drop_index("ix_concepts_course_version_id", table_name="concepts")
    with op.batch_alter_table("concepts") as batch:
        batch.drop_constraint("fk_concepts_course_version_id", type_="foreignkey")
        batch.drop_column("course_version_id")
