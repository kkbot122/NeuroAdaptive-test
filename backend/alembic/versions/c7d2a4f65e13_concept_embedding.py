"""concept embedding

Adds concepts.embedding, needed for regeneration's carryover matching: the
primary match key across versions is canonical_key, but a concept's name can
change between generations, so a similarity fallback needs the definition
embedding on hand rather than re-embedding every old concept at regeneration
time.

Revision ID: c7d2a4f65e13
Revises: b1e4f8a92d76
Create Date: 2026-08-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7d2a4f65e13"
down_revision: Union[str, None] = "b1e4f8a92d76"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("concepts", sa.Column("embedding", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("concepts", "embedding")
