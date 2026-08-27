"""add chunks

Structure-aware document slices with provenance. owner_id and course_id are
denormalised from documents so the retrieval filter can be applied inside the
query rather than through a join on every search.

Revision ID: d92f7e105ab3
Revises: c4a81b26df57
Create Date: 2026-08-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d92f7e105ab3"
down_revision: Union[str, None] = "c4a81b26df57"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("heading_path", sa.String(length=500), nullable=True),
        sa.Column("content_type", sa.String(length=32), nullable=False, server_default="prose"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("embedding_model", sa.String(length=64), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    op.create_index("ix_chunks_course_id", "chunks", ["course_id"])
    op.create_index("ix_chunks_owner_id", "chunks", ["owner_id"])
    # The retrieval hot path is always owner + course scoped.
    op.create_index("ix_chunks_owner_course", "chunks", ["owner_id", "course_id"])


def downgrade() -> None:
    op.drop_index("ix_chunks_owner_course", table_name="chunks")
    for column in ("owner_id", "course_id", "document_id"):
        op.drop_index(f"ix_chunks_{column}", table_name="chunks")
    op.drop_table("chunks")
