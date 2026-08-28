"""curriculum and concept graph

Adds the Phase 2 domain: course_versions (immutable, versioned course
snapshots), concepts, concept_sources (provenance), concept_prerequisites
(the graph), modules, lessons, lesson_concepts, assessment_blueprints. Adds
courses.active_version_id (no FK -- would create a circular table
dependency; enforced in the service layer instead, matching every other
cross-module ownership check in this codebase).

Revision ID: b1e4f8a92d76
Revises: a8b3d5f01c9e
Create Date: 2026-08-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b1e4f8a92d76"
down_revision: Union[str, None] = "a8b3d5f01c9e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("courses", sa.Column("active_version_id", sa.Uuid(), nullable=True))

    op.create_table(
        "course_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="DRAFT"),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("validation_errors", sa.JSON(), nullable=False),
        sa.Column("concept_carryover_map", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_course_versions_course_id", "course_versions", ["course_id"])
    op.create_index("ix_course_versions_owner_id", "course_versions", ["owner_id"])

    op.create_table(
        "concepts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("canonical_key", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("bloom_level", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_concepts_course_id", "concepts", ["course_id"])
    op.create_index("ix_concepts_owner_id", "concepts", ["owner_id"])
    op.create_index("ix_concepts_canonical_key", "concepts", ["canonical_key"])

    op.create_table(
        "concept_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("concept_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["concept_id"], ["concepts.id"]),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"]),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_concept_sources_concept_id", "concept_sources", ["concept_id"])
    op.create_index("ix_concept_sources_chunk_id", "concept_sources", ["chunk_id"])
    op.create_index("ix_concept_sources_course_id", "concept_sources", ["course_id"])
    op.create_index("ix_concept_sources_owner_id", "concept_sources", ["owner_id"])

    op.create_table(
        "concept_prerequisites",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("graph_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("prerequisite_concept_id", sa.Uuid(), nullable=False),
        sa.Column("dependent_concept_id", sa.Uuid(), nullable=False),
        sa.Column("strength", sa.String(length=8), nullable=False, server_default="SOFT"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["prerequisite_concept_id"], ["concepts.id"]),
        sa.ForeignKeyConstraint(["dependent_concept_id"], ["concepts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_concept_prerequisites_course_id", "concept_prerequisites", ["course_id"])
    op.create_index(
        "ix_concept_prerequisites_prerequisite_concept_id",
        "concept_prerequisites", ["prerequisite_concept_id"],
    )
    op.create_index(
        "ix_concept_prerequisites_dependent_concept_id",
        "concept_prerequisites", ["dependent_concept_id"],
    )

    op.create_table(
        "modules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("course_version_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.ForeignKeyConstraint(["course_version_id"], ["course_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_modules_course_version_id", "modules", ["course_version_id"])

    op.create_table(
        "lessons",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("module_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("objective", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["module_id"], ["modules.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lessons_module_id", "lessons", ["module_id"])

    op.create_table(
        "lesson_concepts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lesson_id", sa.Uuid(), nullable=False),
        sa.Column("concept_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="INTRODUCES"),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"]),
        sa.ForeignKeyConstraint(["concept_id"], ["concepts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lesson_concepts_lesson_id", "lesson_concepts", ["lesson_id"])
    op.create_index("ix_lesson_concepts_concept_id", "lesson_concepts", ["concept_id"])

    op.create_table(
        "assessment_blueprints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("course_version_id", sa.Uuid(), nullable=False),
        sa.Column("concept_id", sa.Uuid(), nullable=False),
        sa.Column("question_type", sa.String(length=16), nullable=False, server_default="MCQ"),
        sa.Column("difficulty", sa.String(length=8), nullable=False, server_default="medium"),
        sa.Column("target_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["course_version_id"], ["course_versions.id"]),
        sa.ForeignKeyConstraint(["concept_id"], ["concepts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assessment_blueprints_course_version_id", "assessment_blueprints", ["course_version_id"]
    )
    op.create_index("ix_assessment_blueprints_concept_id", "assessment_blueprints", ["concept_id"])


def downgrade() -> None:
    op.drop_table("assessment_blueprints")
    op.drop_table("lesson_concepts")
    op.drop_table("lessons")
    op.drop_table("modules")
    op.drop_table("concept_prerequisites")
    op.drop_table("concept_sources")
    op.drop_table("concepts")
    op.drop_table("course_versions")
    op.drop_column("courses", "active_version_id")
