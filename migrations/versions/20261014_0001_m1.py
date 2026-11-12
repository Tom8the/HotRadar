"""Sources, persistent collection runs and immutable board snapshots."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261014_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sources",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("platform", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("home_url", sa.Text(), nullable=False),
        sa.Column("access_method", sa.String(80), nullable=False),
    )
    op.create_table(
        "boards",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("source_id", sa.String(40), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("item_kind", sa.String(20), nullable=False),
        sa.Column("metric_name", sa.String(50), nullable=False),
        sa.Column("metric_label", sa.String(80), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("parser_version", sa.String(40), nullable=False),
        sa.CheckConstraint("interval_seconds >= 60"),
    )
    op.create_table(
        "collection_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("board_id", sa.String(80), sa.ForeignKey("boards.id"), nullable=False),
        sa.Column("job_key", sa.String(180), nullable=False, unique=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(60)),
        sa.Column("error_message", sa.Text()),
        sa.Column("parser_version", sa.String(40), nullable=False),
        sa.CheckConstraint("attempt_count >= 0 AND item_count >= 0"),
    )
    op.create_index("ix_collection_runs_board_id", "collection_runs", ["board_id"])
    op.create_index("ix_runs_board_created", "collection_runs", ["board_id", "created_at"])
    op.create_table(
        "raw_payloads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("board_id", sa.String(80), sa.ForeignKey("boards.id"), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("board_id", "sha256"),
    )
    op.create_table(
        "board_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("board_id", sa.String(80), sa.ForeignKey("boards.id"), nullable=False),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("collection_runs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("payload_id", sa.String(36), sa.ForeignKey("raw_payloads.id"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("upstream_updated_at", sa.DateTime(timezone=True)),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("from_cache", sa.Boolean(), nullable=False),
        sa.Column("parser_version", sa.String(40), nullable=False),
    )
    op.create_index("ix_snapshots_board_observed", "board_snapshots", ["board_id", "observed_at"])
    op.create_table(
        "items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("board_id", sa.String(80), sa.ForeignKey("boards.id"), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("item_kind", sa.String(20), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("board_id", "external_id"),
    )
    op.create_table(
        "snapshot_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "snapshot_id", sa.String(36), sa.ForeignKey("board_snapshots.id"), nullable=False
        ),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("source_rank", sa.Integer()),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("is_pinned", sa.Boolean(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("source_event_at", sa.DateTime(timezone=True)),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("flags", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("snapshot_id", "item_id"),
    )
    op.create_index(
        "ix_snapshot_items_snapshot_order", "snapshot_items", ["snapshot_id", "display_order"]
    )


def downgrade():
    for table in (
        "snapshot_items",
        "items",
        "board_snapshots",
        "raw_payloads",
        "collection_runs",
        "boards",
        "sources",
    ):
        op.drop_table(table)
