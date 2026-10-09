"""P8 owner-scoped subjects, pinned course links, and match provenance.

Revision ID: 0a8c21d4f7b9
Revises: 3c7a1e9d5b42
"""
from alembic import op
import sqlalchemy as sa


revision = "0a8c21d4f7b9"
down_revision = "3c7a1e9d5b42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "course_subjects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("normalized_name", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "normalized_name", name="uq_course_subject_owner_name"),
    )
    op.create_index("ix_course_subjects_owner_id", "course_subjects", ["owner_id"])

    with op.batch_alter_table("courses") as batch:
        batch.add_column(sa.Column("subject_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("linked_course_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("linked_version_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("linked_course_title_snapshot", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("linked_version_number", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("link_revision", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("link_revoked_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key("fk_courses_subject_id_course_subjects", "course_subjects", ["subject_id"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key("fk_courses_linked_course_id_courses", "courses", ["linked_course_id"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key("fk_courses_linked_version_id_course_versions", "course_versions", ["linked_version_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_courses_subject_id", ["subject_id"])
        batch.create_index("ix_courses_linked_course_id", ["linked_course_id"])

    op.create_table(
        "cross_course_concept_matches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("current_course_id", sa.Uuid(), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=False),
        sa.Column("current_concept_id", sa.Uuid(), nullable=False),
        sa.Column("linked_course_id", sa.Uuid(), nullable=False),
        sa.Column("linked_version_id", sa.Uuid(), nullable=False),
        sa.Column("linked_concept_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["current_course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["current_version_id"], ["course_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["current_concept_id"], ["concepts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["linked_course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["linked_version_id"], ["course_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["linked_concept_id"], ["concepts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "current_course_id", "current_version_id", "current_concept_id",
            "linked_course_id", "linked_version_id", "linked_concept_id",
            name="uq_cross_course_concept_match_versions",
        ),
    )
    with op.batch_alter_table("learning_activities") as batch:
        batch.add_column(sa.Column("linked_match_id", sa.Uuid(), nullable=True))
        batch.create_index("ix_learning_activities_linked_match_id", ["linked_match_id"])
    for column in ("owner_id", "current_course_id", "current_concept_id", "linked_course_id", "linked_concept_id"):
        op.create_index(f"ix_cross_course_concept_matches_{column}", "cross_course_concept_matches", [column])

    with op.batch_alter_table("lesson_content_artifacts") as batch:
        batch.add_column(sa.Column("link_dependency_fingerprint", sa.String(length=64), server_default="", nullable=False))
    with op.batch_alter_table("activity_preparations") as batch:
        batch.add_column(sa.Column("link_dependency_fingerprint", sa.String(length=64), server_default="", nullable=False))
    with op.batch_alter_table("questions") as batch:
        batch.add_column(sa.Column("source_revoked_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_questions_source_revoked_at", ["source_revoked_at"])


def downgrade() -> None:
    with op.batch_alter_table("questions") as batch:
        batch.drop_index("ix_questions_source_revoked_at")
        batch.drop_column("source_revoked_at")
    with op.batch_alter_table("activity_preparations") as batch:
        batch.drop_column("link_dependency_fingerprint")
    with op.batch_alter_table("lesson_content_artifacts") as batch:
        batch.drop_column("link_dependency_fingerprint")
    with op.batch_alter_table("learning_activities") as batch:
        batch.drop_index("ix_learning_activities_linked_match_id")
        batch.drop_column("linked_match_id")
    for column in ("linked_concept_id", "linked_course_id", "current_concept_id", "current_course_id", "owner_id"):
        op.drop_index(f"ix_cross_course_concept_matches_{column}", table_name="cross_course_concept_matches")
    op.drop_table("cross_course_concept_matches")
    with op.batch_alter_table("courses") as batch:
        batch.drop_index("ix_courses_linked_course_id")
        batch.drop_index("ix_courses_subject_id")
        batch.drop_constraint("fk_courses_linked_version_id_course_versions", type_="foreignkey")
        batch.drop_constraint("fk_courses_linked_course_id_courses", type_="foreignkey")
        batch.drop_constraint("fk_courses_subject_id_course_subjects", type_="foreignkey")
        for column in ("link_revoked_at", "link_revision", "linked_version_number", "linked_course_title_snapshot", "linked_version_id", "linked_course_id", "subject_id"):
            batch.drop_column(column)
    op.drop_index("ix_course_subjects_owner_id", table_name="course_subjects")
    op.drop_table("course_subjects")
