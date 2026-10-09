"""P7 source revisions, persisted presentation preferences, and cleanup recovery.

Revision ID: 3c7a1e9d5b42
Revises: 9fd2c74a6e11
Create Date: 2026-10-09
"""
from alembic import op
import sqlalchemy as sa


revision = "3c7a1e9d5b42"
down_revision = "9fd2c74a6e11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("courses", sa.Column("source_revision", sa.Integer(), server_default="0", nullable=False))
    op.add_column("processing_jobs", sa.Column("source_revision", sa.Integer(), server_default="0", nullable=False))
    op.add_column("course_versions", sa.Column("source_revision", sa.Integer(), server_default="0", nullable=False))
    op.add_column("users", sa.Column("default_presentation_format", sa.String(length=24), server_default="detailed", nullable=False))
    op.add_column("users", sa.Column("tutor_panel_open", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.add_column("storage_upload_intents", sa.Column("replaces_document_id", sa.Uuid(), nullable=True))
    op.create_index("ix_storage_upload_intents_replaces_document_id", "storage_upload_intents", ["replaces_document_id"])
    op.add_column("storage_upload_intents", sa.Column("source_change_applied", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("storage_upload_intents", sa.Column("finalized_document_id", sa.Uuid(), nullable=True))
    op.add_column("storage_upload_intents", sa.Column("replaced_filename", sa.String(length=255), nullable=True))
    op.add_column("storage_upload_intents", sa.Column("retired_cleanup_task_id", sa.Uuid(), nullable=True))
    op.add_column("storage_upload_intents", sa.Column("upload_cleanup_task_id", sa.Uuid(), nullable=True))

    op.create_table(
        "storage_cleanup_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("storage_target", sa.String(length=512), nullable=False),
        sa.Column("storage_kind", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_category", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_target", name="uq_storage_cleanup_target"),
    )
    op.create_index("ix_storage_cleanup_tasks_owner_id", "storage_cleanup_tasks", ["owner_id"])
    op.create_index("ix_storage_cleanup_tasks_status", "storage_cleanup_tasks", ["status"])


def downgrade() -> None:
    op.drop_index("ix_storage_cleanup_tasks_status", table_name="storage_cleanup_tasks")
    op.drop_index("ix_storage_cleanup_tasks_owner_id", table_name="storage_cleanup_tasks")
    op.drop_table("storage_cleanup_tasks")
    op.drop_column("storage_upload_intents", "source_change_applied")
    op.drop_column("storage_upload_intents", "finalized_document_id")
    op.drop_column("storage_upload_intents", "upload_cleanup_task_id")
    op.drop_column("storage_upload_intents", "retired_cleanup_task_id")
    op.drop_column("storage_upload_intents", "replaced_filename")
    op.drop_index("ix_storage_upload_intents_replaces_document_id", table_name="storage_upload_intents")
    op.drop_column("storage_upload_intents", "replaces_document_id")
    op.drop_column("users", "tutor_panel_open")
    op.drop_column("users", "default_presentation_format")
    op.drop_column("course_versions", "source_revision")
    op.drop_column("processing_jobs", "source_revision")
    op.drop_column("courses", "source_revision")
