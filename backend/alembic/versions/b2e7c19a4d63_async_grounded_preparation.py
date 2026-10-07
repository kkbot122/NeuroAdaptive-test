"""asynchronous grounded lesson and MCQ preparation

Revision ID: b2e7c19a4d63
Revises: a61c9e7d4b20
Create Date: 2026-10-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2e7c19a4d63"
down_revision: Union[str, None] = "a61c9e7d4b20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "lesson_content_artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("artifact_key", sa.String(64), nullable=False, unique=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("course_id", sa.Uuid(), sa.ForeignKey("courses.id"), nullable=False),
        sa.Column("course_version_id", sa.Uuid(), sa.ForeignKey("course_versions.id"), nullable=False),
        sa.Column("lesson_id", sa.Uuid(), sa.ForeignKey("lessons.id"), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("curriculum_fingerprint", sa.String(64), nullable=False),
        sa.Column("presentation_format", sa.String(32), nullable=False),
        sa.Column("sections", sa.JSON(), nullable=False),
        sa.Column("source_chunk_ids", sa.JSON(), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("validation_model_id", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("validation_policy_version", sa.String(32), nullable=False),
        sa.Column("validation_status", sa.String(16), nullable=False, server_default="PASSED"),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for column in ("owner_id", "course_id", "course_version_id", "lesson_id"):
        op.create_index(f"ix_lesson_content_artifacts_{column}", "lesson_content_artifacts", [column])

    op.create_table(
        "lesson_content_citations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("artifact_id", sa.Uuid(), sa.ForeignKey("lesson_content_artifacts.id"), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), sa.ForeignKey("chunks.id"), nullable=False),
        sa.UniqueConstraint("artifact_id", "chunk_id", name="uq_lesson_content_citation"),
    )
    op.create_index("ix_lesson_content_citations_artifact_id", "lesson_content_citations", ["artifact_id"])
    op.create_index("ix_lesson_content_citations_chunk_id", "lesson_content_citations", ["chunk_id"])

    op.create_table(
        "activity_preparations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("preparation_key", sa.String(200), nullable=False, unique=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("course_id", sa.Uuid(), sa.ForeignKey("courses.id"), nullable=False),
        sa.Column("course_version_id", sa.Uuid(), sa.ForeignKey("course_versions.id"), nullable=False),
        sa.Column("activity_id", sa.Uuid(), sa.ForeignKey("learning_activities.id"), nullable=True),
        sa.Column("lesson_id", sa.Uuid(), sa.ForeignKey("lessons.id"), nullable=False),
        sa.Column("presentation_format", sa.String(32), nullable=False),
        sa.Column("include_assessment", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_speculative", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("stage", sa.String(24), nullable=False, server_default="CONTENT"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider_call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_category", sa.String(64), nullable=True),
        sa.Column("artifact_keys", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("content_artifact_id", sa.Uuid(), sa.ForeignKey("lesson_content_artifacts.id"), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column in ("owner_id", "course_id", "course_version_id", "activity_id", "lesson_id", "status"):
        op.create_index(f"ix_activity_preparations_{column}", "activity_preparations", [column])

    op.create_table(
        "prepared_activity_questions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("preparation_id", sa.Uuid(), sa.ForeignKey("activity_preparations.id"), nullable=False),
        sa.Column("question_id", sa.Uuid(), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("question_version", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint("preparation_id", "question_id", name="uq_prepared_activity_question"),
        sa.UniqueConstraint("preparation_id", "position", name="uq_prepared_activity_position"),
    )
    op.create_index("ix_prepared_activity_questions_preparation_id", "prepared_activity_questions", ["preparation_id"])
    op.create_index("ix_prepared_activity_questions_question_id", "prepared_activity_questions", ["question_id"])

    op.create_table(
        "question_sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("question_id", sa.Uuid(), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), sa.ForeignKey("chunks.id"), nullable=False),
        sa.UniqueConstraint("question_id", "chunk_id", name="uq_question_source"),
    )
    op.create_index("ix_question_sources_question_id", "question_sources", ["question_id"])
    op.create_index("ix_question_sources_chunk_id", "question_sources", ["chunk_id"])

    with op.batch_alter_table("questions") as batch_op:
        batch_op.add_column(sa.Column("explanation", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("content_hash", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("schema_version", sa.String(32), nullable=True))
        batch_op.add_column(sa.Column("validation_policy_version", sa.String(32), nullable=True))
    op.create_index("uq_questions_version_content_hash", "questions", ["course_version_id", "content_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_questions_version_content_hash", table_name="questions")
    with op.batch_alter_table("questions") as batch_op:
        batch_op.drop_column("validation_policy_version")
        batch_op.drop_column("schema_version")
        batch_op.drop_column("content_hash")
        batch_op.drop_column("explanation")

    op.drop_index("ix_question_sources_chunk_id", table_name="question_sources")
    op.drop_index("ix_question_sources_question_id", table_name="question_sources")
    op.drop_table("question_sources")
    op.drop_index("ix_prepared_activity_questions_question_id", table_name="prepared_activity_questions")
    op.drop_index("ix_prepared_activity_questions_preparation_id", table_name="prepared_activity_questions")
    op.drop_table("prepared_activity_questions")
    for column in ("owner_id", "course_id", "course_version_id", "activity_id", "lesson_id", "status"):
        op.drop_index(f"ix_activity_preparations_{column}", table_name="activity_preparations")
    op.drop_table("activity_preparations")
    op.drop_index("ix_lesson_content_citations_chunk_id", table_name="lesson_content_citations")
    op.drop_index("ix_lesson_content_citations_artifact_id", table_name="lesson_content_citations")
    op.drop_table("lesson_content_citations")
    for column in ("owner_id", "course_id", "course_version_id", "lesson_id"):
        op.drop_index(f"ix_lesson_content_artifacts_{column}", table_name="lesson_content_artifacts")
    op.drop_table("lesson_content_artifacts")
