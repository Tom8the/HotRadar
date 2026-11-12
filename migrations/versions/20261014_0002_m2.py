"""M2 source setting audit and per-observation cadence.

Revision ID: 20261014_0002
Revises: 20261014_0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261014_0002"
down_revision = "20261014_0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("board_snapshots", sa.Column("interval_seconds", sa.Integer(), nullable=True))
    op.create_table(
        "source_setting_changes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("board_id", sa.String(80), sa.ForeignKey("boards.id"), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=False),
        sa.Column("after", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_source_setting_changes_board_id", "source_setting_changes", ["board_id"])


def downgrade():
    op.drop_table("source_setting_changes")
    op.drop_column("board_snapshots", "interval_seconds")
