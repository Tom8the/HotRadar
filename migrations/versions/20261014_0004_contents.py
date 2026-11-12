"""Separate immutable observations for verified concrete content."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261014_0004"
down_revision = "20261014_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "content_observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(40), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("method", sa.String(40), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("source_id IN ('douyin', 'weibo')"),
    )
    op.create_index(
        "ix_content_source_external_observed",
        "content_observations",
        ["source_id", "external_id", "observed_at"],
    )


def downgrade():
    op.drop_index("ix_content_source_external_observed", table_name="content_observations")
    op.drop_table("content_observations")
