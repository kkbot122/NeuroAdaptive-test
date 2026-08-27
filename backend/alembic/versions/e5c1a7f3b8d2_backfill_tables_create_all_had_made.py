"""backfill tables that only create_all had ever made

Five tables the models declare were never created by any migration:

  articles, paragraphs      - dropped by 2f4c2d25f29c, created by no upgrade
  article_readings          - no migration
  chat_sessions             - no migration
  chat_messages             - no migration

86bda7902ec8 is named "add chat sessions and reading logs" but its upgrade()
is `pass`. All five existed only because Base.metadata.create_all ran on every
startup. Removing that (K-3) made Alembic the single schema mechanism and
exposed the gap: `alembic upgrade head` against an empty database produced a
schema the application could not run on.

Every create is guarded by an existence check, so this is a no-op on any
database that create_all already populated and a repair on a fresh one.

Revision ID: e5c1a7f3b8d2
Revises: d92f7e105ab3
Create Date: 2026-08-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5c1a7f3b8d2"
down_revision: Union[str, None] = "d92f7e105ab3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if not inspector.has_table("articles"):
        op.create_table(
            "articles",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(), nullable=True),
            sa.Column("topic", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_articles_id", "articles", ["id"])
        op.create_index("ix_articles_title", "articles", ["title"])
        op.create_index("ix_articles_topic", "articles", ["topic"])

    if not inspector.has_table("paragraphs"):
        op.create_table(
            "paragraphs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("article_id", sa.Integer(), nullable=True),
            sa.Column("order_index", sa.Integer(), nullable=True),
            sa.Column("original_text", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(["article_id"], ["articles.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_paragraphs_id", "paragraphs", ["id"])

    if not inspector.has_table("article_readings"):
        op.create_table(
            "article_readings",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("article_id", sa.Integer(), nullable=False),
            sa.Column("read_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["article_id"], ["articles.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_article_readings_id", "article_readings", ["id"])

    if not inspector.has_table("chat_sessions"):
        op.create_table(
            "chat_sessions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_chat_sessions_id", "chat_sessions", ["id"])

    if not inspector.has_table("chat_messages"):
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("session_id", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_chat_messages_id", "chat_messages", ["id"])


def downgrade() -> None:
    # Deliberately not dropping. These tables predate this revision on every
    # existing database, and dropping them here would destroy learner data that
    # this migration did not create.
    pass
