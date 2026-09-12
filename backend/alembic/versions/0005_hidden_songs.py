"""hidden_songs: permanent per-user "never recommend this again" list

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hidden_songs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("song_id", UUID(as_uuid=True), sa.ForeignKey("songs.id"), nullable=False),
        sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "song_id", name="uq_hidden_song"),
    )
    op.create_index("ix_hidden_songs_user_id", "hidden_songs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_hidden_songs_user_id", table_name="hidden_songs")
    op.drop_table("hidden_songs")
