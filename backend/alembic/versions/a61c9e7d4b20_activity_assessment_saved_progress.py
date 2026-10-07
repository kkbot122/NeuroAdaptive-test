"""durable activity, assessment session, and answer lifecycle

Revision ID: a61c9e7d4b20
Revises: 83c6e2f41b85
Create Date: 2026-10-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a61c9e7d4b20"
down_revision: Union[str, None] = "83c6e2f41b85"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "learning_activities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("course_id", sa.Uuid(), sa.ForeignKey("courses.id"), nullable=False),
        sa.Column("course_version_id", sa.Uuid(), sa.ForeignKey("course_versions.id"), nullable=False),
        sa.Column("decision_id", sa.Uuid(), sa.ForeignKey("adaptation_decisions.id"), nullable=True),
        sa.Column("activity_type", sa.String(32), nullable=False),
        sa.Column("target_concept_ids", sa.JSON(), nullable=False),
        sa.Column("lesson_id", sa.Uuid(), sa.ForeignKey("lessons.id"), nullable=True),
        sa.Column("reason_text", sa.String(500), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="READY"),
        sa.Column("presentation_format", sa.String(32), nullable=False, server_default="detailed"),
        sa.Column("reading_position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reading_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_learning_activities_owner_id", "learning_activities", ["owner_id"])
    op.create_index("ix_learning_activities_course_id", "learning_activities", ["course_id"])
    op.create_index("ix_learning_activities_course_version_id", "learning_activities", ["course_version_id"])
    op.create_index("ix_learning_activities_decision_id", "learning_activities", ["decision_id"])
    op.create_index("ix_learning_activities_lesson_id", "learning_activities", ["lesson_id"])
    op.create_index("ix_learning_activities_status", "learning_activities", ["status"])
    op.create_index(
        "uq_learning_activities_one_unfinished_per_course",
        "learning_activities",
        ["owner_id", "course_id"],
        unique=True,
        postgresql_where=sa.text("status != 'COMPLETED'"),
        sqlite_where=sa.text("status != 'COMPLETED'"),
    )

    op.create_table(
        "assessment_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("activity_id", sa.Uuid(), sa.ForeignKey("learning_activities.id"), nullable=False),
        sa.Column("course_version_id", sa.Uuid(), sa.ForeignKey("course_versions.id"), nullable=False),
        sa.Column("assessment_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("activity_id", name="uq_assessment_sessions_activity_id"),
    )
    op.create_index("ix_assessment_sessions_course_version_id", "assessment_sessions", ["course_version_id"])

    op.create_table(
        "assessment_questions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("assessment_sessions.id"), nullable=False),
        sa.Column("question_id", sa.Uuid(), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("question_version", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint("session_id", "question_id", name="uq_assessment_questions_session_question"),
        sa.UniqueConstraint("session_id", "position", name="uq_assessment_questions_session_position"),
    )
    op.create_index("ix_assessment_questions_session_id", "assessment_questions", ["session_id"])
    op.create_index("ix_assessment_questions_question_id", "assessment_questions", ["question_id"])

    op.create_table(
        "answer_submissions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("assessment_question_id", sa.Uuid(), sa.ForeignKey("assessment_questions.id"), nullable=False),
        sa.Column("given_answer", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="AWAITING_GRADING"),
        sa.Column("failure_code", sa.String(32), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("assessment_question_id", name="uq_answer_submissions_assessment_question_id"),
    )
    op.create_index("ix_answer_submissions_status", "answer_submissions", ["status"])

    with op.batch_alter_table("question_attempts") as batch_op:
        batch_op.add_column(sa.Column("assessment_question_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_qattempt_assessment_question",
            "assessment_questions",
            ["assessment_question_id"],
            ["id"],
        )
        batch_op.create_unique_constraint(
            "uq_question_attempts_assessment_question", ["assessment_question_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("question_attempts") as batch_op:
        batch_op.drop_constraint("uq_question_attempts_assessment_question", type_="unique")
        batch_op.drop_constraint(
            "fk_qattempt_assessment_question", type_="foreignkey"
        )
        batch_op.drop_column("assessment_question_id")

    op.drop_index("ix_answer_submissions_status", table_name="answer_submissions")
    op.drop_table("answer_submissions")
    op.drop_index("ix_assessment_questions_question_id", table_name="assessment_questions")
    op.drop_index("ix_assessment_questions_session_id", table_name="assessment_questions")
    op.drop_table("assessment_questions")
    op.drop_index("ix_assessment_sessions_course_version_id", table_name="assessment_sessions")
    op.drop_table("assessment_sessions")
    op.drop_index("uq_learning_activities_one_unfinished_per_course", table_name="learning_activities")
    op.drop_index("ix_learning_activities_status", table_name="learning_activities")
    op.drop_index("ix_learning_activities_lesson_id", table_name="learning_activities")
    op.drop_index("ix_learning_activities_decision_id", table_name="learning_activities")
    op.drop_index("ix_learning_activities_course_version_id", table_name="learning_activities")
    op.drop_index("ix_learning_activities_course_id", table_name="learning_activities")
    op.drop_index("ix_learning_activities_owner_id", table_name="learning_activities")
    op.drop_table("learning_activities")
