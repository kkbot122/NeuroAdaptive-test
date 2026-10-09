"""Owner-scoped provider attempts and returned token usage."""
from alembic import op
import sqlalchemy as sa

revision = "9fd2c74a6e11"
down_revision = "f5a1c9d2e7b4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_provider_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column("feature", sa.String(32), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("retry_index", sa.Integer(), nullable=False),
        sa.Column("input_items", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("capacity_wait_ms", sa.Integer(), nullable=False),
        sa.Column("error_category", sa.String(32), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
    )
    op.create_index("ix_ai_provider_calls_owner_started", "ai_provider_calls", ["owner_id", "started_at"])


def downgrade():
    op.drop_index("ix_ai_provider_calls_owner_started", table_name="ai_provider_calls")
    op.drop_table("ai_provider_calls")
