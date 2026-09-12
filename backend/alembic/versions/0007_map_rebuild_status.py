"""users: map_rebuild_status for async, pollable map rebuilds

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-21
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("map_rebuild_status", sa.String(), nullable=False, server_default="idle"),
    )


def downgrade() -> None:
    op.drop_column("users", "map_rebuild_status")
