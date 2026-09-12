"""songs: add canonical artist identity list, for the recommendation diversity cap

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-21
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("songs", sa.Column("artist_canonical", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("songs", "artist_canonical")
