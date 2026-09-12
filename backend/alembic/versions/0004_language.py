"""songs: add detected language (ISO 639-1) for the recommendations language filter

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("songs", sa.Column("language", sa.String, nullable=True))
    op.create_index("ix_songs_language", "songs", ["language"])


def downgrade() -> None:
    op.drop_index("ix_songs_language", table_name="songs")
    op.drop_column("songs", "language")
