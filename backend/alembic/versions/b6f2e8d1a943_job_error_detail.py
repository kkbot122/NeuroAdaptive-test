"""processing_jobs.error_detail

Revision ID: b6f2e8d1a943
Revises: a3d9e6c14b72
Create Date: 2026-08-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b6f2e8d1a943"
down_revision: Union[str, None] = "a3d9e6c14b72"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("processing_jobs") as batch:
        batch.add_column(sa.Column("error_detail", sa.String(500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("processing_jobs") as batch:
        batch.drop_column("error_detail")
