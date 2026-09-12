"""initial schema: users, songs, user_songs

Revision ID: 0001
Revises:
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

FEATURE_VECTOR_DIM = 32


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String, nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "songs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("artist", sa.String, nullable=False),
        sa.Column("isrc", sa.String, nullable=True),
        sa.Column("musicbrainz_id", sa.String, nullable=True),
        sa.Column("preview_url", sa.String, nullable=True),
        sa.Column("resolution_status", sa.String, nullable=False, server_default="pending"),
        sa.Column("extraction_status", sa.String, nullable=False, server_default="pending"),
        sa.Column("feature_vector", Vector(FEATURE_VECTOR_DIM), nullable=True),
        sa.Column("bpm", sa.Float, nullable=True),
        sa.Column("key", sa.String, nullable=True),
        sa.Column("energy", sa.Float, nullable=True),
        sa.Column("danceability", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("title", "artist", name="uq_songs_title_artist"),
    )
    op.create_index("ix_songs_title", "songs", ["title"])
    op.create_index("ix_songs_artist", "songs", ["artist"])
    op.create_index("ix_songs_isrc", "songs", ["isrc"])
    op.create_index("ix_songs_musicbrainz_id", "songs", ["musicbrainz_id"])

    op.create_table(
        "user_songs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("song_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("songs.id"), nullable=False),
        sa.Column("source", sa.String, nullable=False),
        sa.Column("source_ref", sa.String, nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("map_x", sa.Float, nullable=True),
        sa.Column("map_y", sa.Float, nullable=True),
        sa.Column("cluster_label", sa.Integer, nullable=True),
        sa.UniqueConstraint("user_id", "song_id", "source", name="uq_user_song_source"),
    )

    op.execute(
        "CREATE INDEX ix_songs_feature_vector ON songs USING ivfflat (feature_vector vector_cosine_ops) WITH (lists = 100)"
    )


def downgrade() -> None:
    op.drop_table("user_songs")
    op.drop_index("ix_songs_feature_vector", table_name="songs")
    op.drop_table("songs")
    op.drop_table("users")
