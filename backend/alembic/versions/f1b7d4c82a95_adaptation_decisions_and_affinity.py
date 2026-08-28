"""adaptation decisions and presentation affinity

Revision ID: f1b7d4c82a95
Revises: e8a2c19f4d63
Create Date: 2026-08-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1b7d4c82a95"
down_revision: Union[str, None] = "e8a2c19f4d63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "adaptation_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("course_id", sa.Uuid(), sa.ForeignKey("courses.id"), nullable=False),
        sa.Column("selected_activity_type", sa.String(32), nullable=False),
        sa.Column("selected_concept_id", sa.Uuid(), nullable=True),
        sa.Column("selected_lesson_id", sa.Uuid(), nullable=True),
        sa.Column("reason_text", sa.String(500), nullable=False),
        sa.Column("candidates_considered", sa.JSON(), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_adaptation_decisions_owner_id", "adaptation_decisions", ["owner_id"])
    op.create_index("ix_adaptation_decisions_course_id", "adaptation_decisions", ["course_id"])

    op.create_table(
        "presentation_affinities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("format", sa.String(32), nullable=False),
        sa.Column("exposure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("effectiveness", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_presentation_affinities_owner_id", "presentation_affinities", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_presentation_affinities_owner_id", table_name="presentation_affinities")
    op.drop_table("presentation_affinities")

    op.drop_index("ix_adaptation_decisions_course_id", table_name="adaptation_decisions")
    op.drop_index("ix_adaptation_decisions_owner_id", table_name="adaptation_decisions")
    op.drop_table("adaptation_decisions")
