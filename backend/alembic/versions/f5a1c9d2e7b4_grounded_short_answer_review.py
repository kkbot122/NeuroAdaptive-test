"""Grounded short answers, durable grading, reports, and correction history.

Revision ID: f5a1c9d2e7b4
Revises: d3f4a8c1e620
"""
from alembic import op
import sqlalchemy as sa


revision = "f5a1c9d2e7b4"
down_revision = "d3f4a8c1e620"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("questions") as batch:
        batch.add_column(sa.Column("rubric_details", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("rubric_passing_criteria", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("expected_reasoning", sa.Text(), nullable=True))

    with op.batch_alter_table("answer_submissions") as batch:
        batch.add_column(
            sa.Column("grading_attempt_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("grading_call_limit", sa.Integer(), nullable=False, server_default="3")
        )
        batch.add_column(sa.Column("grading_lease_token", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("grading_lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("grading_last_dispatched_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "grading_judgments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("answer_submission_id", sa.Uuid(), nullable=False),
        sa.Column("criteria_met", sa.JSON(), nullable=False),
        sa.Column("rubric_score", sa.Integer(), nullable=False),
        sa.Column("evidence_correctness", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["answer_submission_id"], ["answer_submissions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("answer_submission_id", name="uq_grading_judgments_answer_submission"),
    )
    op.create_index("ix_grading_judgments_answer_submission_id", "grading_judgments", ["answer_submission_id"])

    op.create_table(
        "grading_issue_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("answer_submission_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("report_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="OPEN", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["answer_submission_id"], ["answer_submissions.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("answer_submission_id", name="uq_grading_issue_reports_answer_submission"),
    )
    op.create_index("ix_grading_issue_reports_answer_submission_id", "grading_issue_reports", ["answer_submission_id"])
    op.create_index("ix_grading_issue_reports_owner_id", "grading_issue_reports", ["owner_id"])
    op.create_index("ix_grading_issue_reports_course_id", "grading_issue_reports", ["course_id"])
    op.create_index("ix_grading_issue_reports_status", "grading_issue_reports", ["status"])

    op.create_table(
        "grading_review_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("correction_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["grading_issue_reports.id"]),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_grading_review_events_report_id", "grading_review_events", ["report_id"])
    op.create_index("ix_grading_review_events_reviewer_id", "grading_review_events", ["reviewer_id"])

    op.create_table(
        "grading_corrections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("question_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("criteria_met", sa.JSON(), nullable=False),
        sa.Column("rubric_score", sa.Integer(), nullable=False),
        sa.Column("effective_correctness", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["grading_issue_reports.id"]),
        sa.ForeignKeyConstraint(["question_attempt_id"], ["question_attempts.id"]),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_id", "version", name="uq_grading_corrections_report_version"),
    )
    op.create_index("ix_grading_corrections_report_id", "grading_corrections", ["report_id"])
    op.create_index("ix_grading_corrections_question_attempt_id", "grading_corrections", ["question_attempt_id"])
    op.create_index("ix_grading_corrections_reviewer_id", "grading_corrections", ["reviewer_id"])


def downgrade():
    op.drop_index("ix_grading_corrections_reviewer_id", table_name="grading_corrections")
    op.drop_index("ix_grading_corrections_question_attempt_id", table_name="grading_corrections")
    op.drop_index("ix_grading_corrections_report_id", table_name="grading_corrections")
    op.drop_table("grading_corrections")
    op.drop_index("ix_grading_review_events_reviewer_id", table_name="grading_review_events")
    op.drop_index("ix_grading_review_events_report_id", table_name="grading_review_events")
    op.drop_table("grading_review_events")
    op.drop_index("ix_grading_issue_reports_status", table_name="grading_issue_reports")
    op.drop_index("ix_grading_issue_reports_course_id", table_name="grading_issue_reports")
    op.drop_index("ix_grading_issue_reports_owner_id", table_name="grading_issue_reports")
    op.drop_index("ix_grading_issue_reports_answer_submission_id", table_name="grading_issue_reports")
    op.drop_table("grading_issue_reports")
    op.drop_index("ix_grading_judgments_answer_submission_id", table_name="grading_judgments")
    op.drop_table("grading_judgments")

    with op.batch_alter_table("answer_submissions") as batch:
        batch.drop_column("grading_last_dispatched_at")
        batch.drop_column("grading_lease_expires_at")
        batch.drop_column("grading_lease_token")
        batch.drop_column("grading_attempt_count")
        batch.drop_column("grading_call_limit")
    with op.batch_alter_table("questions") as batch:
        batch.drop_column("expected_reasoning")
        batch.drop_column("rubric_passing_criteria")
        batch.drop_column("rubric_details")
