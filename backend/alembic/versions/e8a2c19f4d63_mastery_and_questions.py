"""mastery, questions, and evidence tables

Revision ID: e8a2c19f4d63
Revises: d4f8b2e91a37
Create Date: 2026-08-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e8a2c19f4d63"
down_revision: Union[str, None] = "d4f8b2e91a37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "questions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("course_id", sa.Uuid(), sa.ForeignKey("courses.id"), nullable=False),
        sa.Column("course_version_id", sa.Uuid(), sa.ForeignKey("course_versions.id"), nullable=False),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("question_type", sa.String(16), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=True),
        sa.Column("correct_answer", sa.JSON(), nullable=True),
        sa.Column("rubric", sa.JSON(), nullable=True),
        sa.Column("difficulty", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("is_diagnostic", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("supersedes_question_id", sa.Uuid(), sa.ForeignKey("questions.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_questions_course_id", "questions", ["course_id"])
    op.create_index("ix_questions_course_version_id", "questions", ["course_version_id"])
    op.create_index("ix_questions_owner_id", "questions", ["owner_id"])

    op.create_table(
        "question_concepts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("question_id", sa.Uuid(), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("concept_id", sa.Uuid(), sa.ForeignKey("concepts.id"), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
    )
    op.create_index("ix_question_concepts_question_id", "question_concepts", ["question_id"])
    op.create_index("ix_question_concepts_concept_id", "question_concepts", ["concept_id"])

    op.create_table(
        "question_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("question_id", sa.Uuid(), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("question_version", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("course_id", sa.Uuid(), sa.ForeignKey("courses.id"), nullable=False),
        sa.Column("given_answer", sa.JSON(), nullable=True),
        sa.Column("correctness", sa.Float(), nullable=False),
        sa.Column("time_taken_seconds", sa.Float(), nullable=True),
        sa.Column("hints_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("self_reported_confidence", sa.Float(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_question_attempts_question_id", "question_attempts", ["question_id"])
    op.create_index("ix_question_attempts_owner_id", "question_attempts", ["owner_id"])
    op.create_index("ix_question_attempts_course_id", "question_attempts", ["course_id"])

    op.create_table(
        "mastery_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("concept_id", sa.Uuid(), sa.ForeignKey("concepts.id"), nullable=False),
        sa.Column("course_id", sa.Uuid(), sa.ForeignKey("courses.id"), nullable=False),
        sa.Column("course_version_id", sa.Uuid(), sa.ForeignKey("course_versions.id"), nullable=False),
        sa.Column("question_attempt_id", sa.Uuid(), sa.ForeignKey("question_attempts.id"), nullable=True),
        sa.Column("correctness", sa.Float(), nullable=False),
        sa.Column("evidence_weight_base", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_mastery_events_owner_id", "mastery_events", ["owner_id"])
    op.create_index("ix_mastery_events_concept_id", "mastery_events", ["concept_id"])
    op.create_index("ix_mastery_events_course_id", "mastery_events", ["course_id"])
    op.create_index("ix_mastery_events_course_version_id", "mastery_events", ["course_version_id"])


def downgrade() -> None:
    op.drop_index("ix_mastery_events_course_version_id", table_name="mastery_events")
    op.drop_index("ix_mastery_events_course_id", table_name="mastery_events")
    op.drop_index("ix_mastery_events_concept_id", table_name="mastery_events")
    op.drop_index("ix_mastery_events_owner_id", table_name="mastery_events")
    op.drop_table("mastery_events")

    op.drop_index("ix_question_attempts_course_id", table_name="question_attempts")
    op.drop_index("ix_question_attempts_owner_id", table_name="question_attempts")
    op.drop_index("ix_question_attempts_question_id", table_name="question_attempts")
    op.drop_table("question_attempts")

    op.drop_index("ix_question_concepts_concept_id", table_name="question_concepts")
    op.drop_index("ix_question_concepts_question_id", table_name="question_concepts")
    op.drop_table("question_concepts")

    op.drop_index("ix_questions_owner_id", table_name="questions")
    op.drop_index("ix_questions_course_version_id", table_name="questions")
    op.drop_index("ix_questions_course_id", table_name="questions")
    op.drop_table("questions")
