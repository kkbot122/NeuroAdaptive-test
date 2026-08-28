"""tutor messages

Revision ID: a3d9e6c14b72
Revises: f1b7d4c82a95
Create Date: 2026-08-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3d9e6c14b72"
down_revision: Union[str, None] = "f1b7d4c82a95"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tutor_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("course_id", sa.Uuid(), sa.ForeignKey("courses.id"), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("context_lesson_id", sa.Uuid(), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer_markdown", sa.Text(), nullable=False),
        sa.Column("retrieved_chunk_ids", sa.JSON(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("grounding_mode", sa.String(16), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_tutor_messages_owner_id", "tutor_messages", ["owner_id"])
    op.create_index("ix_tutor_messages_course_id", "tutor_messages", ["course_id"])
    op.create_index("ix_tutor_messages_conversation_id", "tutor_messages", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_tutor_messages_conversation_id", table_name="tutor_messages")
    op.drop_index("ix_tutor_messages_course_id", table_name="tutor_messages")
    op.drop_index("ix_tutor_messages_owner_id", table_name="tutor_messages")
    op.drop_table("tutor_messages")
