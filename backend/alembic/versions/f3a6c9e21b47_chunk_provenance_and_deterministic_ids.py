"""chunk provenance and deterministic ids

Adds token_count, char_start/char_end and extraction_version to chunks, and
drops the server-side default on id: the job runner now assigns a
deterministic uuid5 derived from (document_id, extraction_version, position)
instead of a random uuid4, so a retried job overwrites identically instead of
duplicating, and a citation recorded elsewhere keeps pointing at the same
chunk across a reprocess with the same extraction_version.

Revision ID: f3a6c9e21b47
Revises: e5c1a7f3b8d2
Create Date: 2026-08-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3a6c9e21b47"
down_revision: Union[str, None] = "e5c1a7f3b8d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("chunks", sa.Column("char_start", sa.Integer(), nullable=True))
    op.add_column("chunks", sa.Column("char_end", sa.Integer(), nullable=True))
    op.add_column(
        "chunks",
        sa.Column("extraction_version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("chunks", "extraction_version")
    op.drop_column("chunks", "char_end")
    op.drop_column("chunks", "char_start")
    op.drop_column("chunks", "token_count")
