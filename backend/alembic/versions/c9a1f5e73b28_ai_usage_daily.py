"""ai_usage_daily (T5 daily AI budget)

Revision ID: c9a1f5e73b28
Revises: b6f2e8d1a943
Create Date: 2026-08-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9a1f5e73b28"
down_revision: Union[str, None] = "b6f2e8d1a943"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_usage_daily",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("owner_id", "usage_date", name="uq_ai_usage_daily_owner_date"),
    )
    op.create_index("ix_ai_usage_daily_owner_id", "ai_usage_daily", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_usage_daily_owner_id", table_name="ai_usage_daily")
    op.drop_table("ai_usage_daily")
