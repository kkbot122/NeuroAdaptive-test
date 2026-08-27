"""add learning_events and quiz_attempts

Creates the two tables the learner-evidence paths were always meant to write
to. Until now the telemetry path posted to an endpoint that did not exist and
the quiz path never left the browser, so neither signal was ever persisted.

New tables use UUID primary keys per SYSTEM_ARCHITECTURE.md §8. The existing
seven tables keep their integer keys; this is a deliberate mixed schema during
the transition rather than an oversight.

Revision ID: b7d3e91f4c02
Revises: a1b2c3d4e5f6
Create Date: 2026-08-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7d3e91f4c02"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "learning_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("dimension", sa.String(), nullable=True),
        sa.Column("seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("target_id", sa.String(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_learning_events_user_id", "learning_events", ["user_id"])
    op.create_index("ix_learning_events_event_type", "learning_events", ["event_type"])
    op.create_index("ix_learning_events_occurred_at", "learning_events", ["occurred_at"])

    op.create_table(
        "quiz_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("topic", sa.String(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_questions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("questions", sa.JSON(), nullable=False),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quiz_attempts_user_id", "quiz_attempts", ["user_id"])
    op.create_index("ix_quiz_attempts_topic", "quiz_attempts", ["topic"])
    op.create_index("ix_quiz_attempts_submitted_at", "quiz_attempts", ["submitted_at"])


def downgrade() -> None:
    op.drop_index("ix_quiz_attempts_submitted_at", table_name="quiz_attempts")
    op.drop_index("ix_quiz_attempts_topic", table_name="quiz_attempts")
    op.drop_index("ix_quiz_attempts_user_id", table_name="quiz_attempts")
    op.drop_table("quiz_attempts")

    op.drop_index("ix_learning_events_occurred_at", table_name="learning_events")
    op.drop_index("ix_learning_events_event_type", table_name="learning_events")
    op.drop_index("ix_learning_events_user_id", table_name="learning_events")
    op.drop_table("learning_events")
