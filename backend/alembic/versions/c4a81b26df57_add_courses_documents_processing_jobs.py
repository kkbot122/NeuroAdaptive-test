"""add courses, documents, processing jobs and stages

Foundation tables for the frozen-scope loop: upload material -> generate a
course. Built alongside the legacy Article/Paragraph model rather than
extending it; that model is pre-loaded reading content with no upload path and
no existing data needs migrating.

UUID primary keys per SYSTEM_ARCHITECTURE.md §8.

Revision ID: c4a81b26df57
Revises: b7d3e91f4c02
Create Date: 2026-08-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4a81b26df57"
down_revision: Union[str, None] = "b7d3e91f4c02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "courses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("goal", sa.Text(), nullable=True),
        sa.Column("starting_confidence", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="DRAFT"),
        sa.Column("sources_finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_courses_owner_id", "courses", ["owner_id"])
    op.create_index("ix_courses_status", "courses", ["status"])

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="STUDY"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="UPLOADED"),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("needs_input_reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_documents_course_id", "documents", ["course_id"])
    op.create_index("ix_documents_owner_id", "documents", ["owner_id"])

    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("current_stage", sa.String(length=48), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_category", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_processing_jobs_course_id", "processing_jobs", ["course_id"])
    op.create_index("ix_processing_jobs_owner_id", "processing_jobs", ["owner_id"])
    op.create_index("ix_processing_jobs_status", "processing_jobs", ["status"])

    op.create_table(
        "processing_stages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=48), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_category", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["processing_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_processing_stages_job_id", "processing_stages", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_processing_stages_job_id", table_name="processing_stages")
    op.drop_table("processing_stages")

    for idx in ("status", "owner_id", "course_id"):
        op.drop_index(f"ix_processing_jobs_{idx}", table_name="processing_jobs")
    op.drop_table("processing_jobs")

    for idx in ("owner_id", "course_id"):
        op.drop_index(f"ix_documents_{idx}", table_name="documents")
    op.drop_table("documents")

    for idx in ("status", "owner_id"):
        op.drop_index(f"ix_courses_{idx}", table_name="courses")
    op.drop_table("courses")
