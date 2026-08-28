"""experiments, experiment_conditions, cohort_assignments (Phase 8)

Revision ID: f7b3d29e1c64
Revises: e2c6a9f4d817
Create Date: 2026-08-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7b3d29e1c64"
down_revision: Union[str, None] = "e2c6a9f4d817"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_experiments_owner_id", "experiments", ["owner_id"])

    op.create_table(
        "experiment_conditions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("experiment_id", sa.Uuid(), sa.ForeignKey("experiments.id"), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("experiment_id", "code", name="uq_experiment_condition_code"),
    )
    op.create_index("ix_experiment_conditions_experiment_id", "experiment_conditions", ["experiment_id"])

    op.create_table(
        "cohort_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("experiment_id", sa.Uuid(), sa.ForeignKey("experiments.id"), nullable=False),
        sa.Column("condition_id", sa.Uuid(), sa.ForeignKey("experiment_conditions.id"), nullable=False),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("experiment_id", "owner_id", name="uq_cohort_assignment_learner"),
    )
    op.create_index("ix_cohort_assignments_experiment_id", "cohort_assignments", ["experiment_id"])
    op.create_index("ix_cohort_assignments_condition_id", "cohort_assignments", ["condition_id"])
    op.create_index("ix_cohort_assignments_owner_id", "cohort_assignments", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_cohort_assignments_owner_id", table_name="cohort_assignments")
    op.drop_index("ix_cohort_assignments_condition_id", table_name="cohort_assignments")
    op.drop_index("ix_cohort_assignments_experiment_id", table_name="cohort_assignments")
    op.drop_table("cohort_assignments")

    op.drop_index("ix_experiment_conditions_experiment_id", table_name="experiment_conditions")
    op.drop_table("experiment_conditions")

    op.drop_index("ix_experiments_owner_id", table_name="experiments")
    op.drop_table("experiments")
