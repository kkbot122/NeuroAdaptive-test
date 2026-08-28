"""adaptation_outcomes and reproducibility fields (Phase 7)

Revision ID: e2c6a9f4d817
Revises: d47c8b2e91f6
Create Date: 2026-08-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2c6a9f4d817"
down_revision: Union[str, None] = "d47c8b2e91f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "adaptation_outcomes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("decision_id", sa.Uuid(), sa.ForeignKey("adaptation_decisions.id"), nullable=False),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("outcome_type", sa.String(32), nullable=False),
        sa.Column("signal_category", sa.String(32), nullable=False),
        sa.Column("concept_id", sa.Uuid(), nullable=True),
        sa.Column("mastery_delta", sa.Float(), nullable=True),
        sa.Column("transfer_success", sa.Integer(), nullable=True),
        sa.Column("hint_usage_delta", sa.Float(), nullable=True),
        sa.Column("time_to_correct_delta", sa.Float(), nullable=True),
        sa.Column("helpfulness_rating", sa.Integer(), nullable=True),
        sa.Column("question_attempt_id", sa.Uuid(), sa.ForeignKey("question_attempts.id"), nullable=True),
        sa.Column("baseline_question_attempt_id", sa.Uuid(), sa.ForeignKey("question_attempts.id"), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_adaptation_outcomes_decision_id", "adaptation_outcomes", ["decision_id"])
    op.create_index("ix_adaptation_outcomes_owner_id", "adaptation_outcomes", ["owner_id"])

    with op.batch_alter_table("questions") as batch:
        batch.add_column(sa.Column("model_id", sa.String(128), nullable=False, server_default="unknown"))
        batch.add_column(sa.Column("prompt_version", sa.String(32), nullable=False, server_default="unknown"))
        batch.add_column(sa.Column("decision_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_questions_decision_id", "adaptation_decisions", ["decision_id"], ["id"]
        )

    with op.batch_alter_table("tutor_messages") as batch:
        batch.add_column(sa.Column("decision_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_tutor_messages_decision_id", "adaptation_decisions", ["decision_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("tutor_messages") as batch:
        batch.drop_constraint("fk_tutor_messages_decision_id", type_="foreignkey")
        batch.drop_column("decision_id")

    with op.batch_alter_table("questions") as batch:
        batch.drop_constraint("fk_questions_decision_id", type_="foreignkey")
        batch.drop_column("decision_id")
        batch.drop_column("prompt_version")
        batch.drop_column("model_id")

    op.drop_index("ix_adaptation_outcomes_owner_id", table_name="adaptation_outcomes")
    op.drop_index("ix_adaptation_outcomes_decision_id", table_name="adaptation_outcomes")
    op.drop_table("adaptation_outcomes")
