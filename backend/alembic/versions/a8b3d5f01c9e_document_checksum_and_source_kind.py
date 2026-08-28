"""document checksum and source kind

Adds checksum_sha256 (dedup: re-uploading an identical file within the same
course reuses the existing document rather than reprocessing) and
source_kind (UPLOAD vs PASTED_TEXT, so pasted text can skip the upload step
while still going through the same extraction/chunking path).

checksum_sha256 is added NOT NULL for new rows but backfilled for any
existing document with a placeholder rather than a real hash, since no
prior document is expected in a fresh deployment of this feature and
computing the real hash would require re-reading every file from disk
during the migration.

Revision ID: a8b3d5f01c9e
Revises: f3a6c9e21b47
Create Date: 2026-08-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a8b3d5f01c9e"
down_revision: Union[str, None] = "f3a6c9e21b47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("source_kind", sa.String(length=16), nullable=False, server_default="UPLOAD"),
    )
    op.add_column(
        "documents",
        sa.Column(
            "checksum_sha256",
            sa.String(length=64),
            nullable=False,
            server_default="0" * 64,  # placeholder for any pre-existing row
        ),
    )
    op.create_index("ix_documents_checksum_sha256", "documents", ["checksum_sha256"])


def downgrade() -> None:
    op.drop_index("ix_documents_checksum_sha256", table_name="documents")
    op.drop_column("documents", "checksum_sha256")
    op.drop_column("documents", "source_kind")
