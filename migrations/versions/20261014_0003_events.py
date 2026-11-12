"""M3 traceable events, human corrections and source summaries."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261014_0003"
down_revision = "20261014_0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("manual_locked", sa.Boolean(), nullable=False),
        sa.Column("followed", sa.Boolean(), nullable=False),
        sa.Column("tags", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("revision >= 1"),
    )
    op.create_table(
        "event_members",
        sa.Column("item_id", sa.String(36), sa.ForeignKey("items.id"), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("basis", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_event_members_event_id", "event_members", ["event_id"])
    op.create_table(
        "event_changes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=False),
        sa.Column("after", postgresql.JSONB(), nullable=False),
        sa.Column("reverted_by", sa.String(36)),
    )
    op.create_table(
        "event_summaries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(60), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("evidence_ids", postgresql.JSONB(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id", "signature"),
    )
    op.create_index("ix_event_summaries_event_id", "event_summaries", ["event_id"])
    op.create_table(
        "event_processed_snapshots",
        sa.Column(
            "snapshot_id", sa.String(36), sa.ForeignKey("board_snapshots.id"), primary_key=True
        ),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    for table in (
        "event_processed_snapshots",
        "event_summaries",
        "event_changes",
        "event_members",
        "events",
    ):
        op.drop_table(table)
