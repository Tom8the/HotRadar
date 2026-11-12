"""Separate configuration and audited tasks for validated content session collectors."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261014_0005"
down_revision = "20261014_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "content_source_settings",
        sa.Column("source_id", sa.String(40), sa.ForeignKey("sources.id"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("verification", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("source_id IN ('douyin', 'weibo') AND interval_seconds >= 900"),
    )
    op.create_table(
        "content_collection_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(40), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("job_key", sa.String(180), nullable=False, unique=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("item_count", sa.Integer()),
        sa.Column("rejected_count", sa.Integer()),
        sa.Column("error_code", sa.String(60)),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("observation_ids", postgresql.JSONB(), nullable=False),
    )
    op.create_index(
        "ix_content_collection_runs_source_id", "content_collection_runs", ["source_id"]
    )
    op.execute(
        "INSERT INTO content_source_settings "
        "(source_id, enabled, interval_seconds, verification) "
        "SELECT id, false, 1800, '{}'::jsonb FROM sources WHERE id IN ('weibo','douyin')"
    )


def downgrade():
    op.drop_index("ix_content_collection_runs_source_id", table_name="content_collection_runs")
    op.drop_table("content_collection_runs")
    op.drop_table("content_source_settings")
