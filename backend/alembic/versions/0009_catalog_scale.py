"""million-scale catalog identity, durable jobs, and ANN retrieval

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

RETRIEVAL_VECTOR_DIM = 1312


def upgrade() -> None:
    op.add_column("songs", sa.Column("retrieval_vector", Vector(RETRIEVAL_VECTOR_DIM), nullable=True))
    op.add_column("songs", sa.Column("catalog_status", sa.String(), nullable=False, server_default="metadata"))
    op.add_column("songs", sa.Column("canonical_score", sa.Float(), nullable=True))
    op.add_column("songs", sa.Column("popularity_score", sa.Float(), nullable=True))
    op.add_column("songs", sa.Column("last_catalog_error", sa.Text(), nullable=True))
    op.execute("UPDATE songs SET catalog_status = 'analyzed' WHERE feature_vector IS NOT NULL")
    op.execute("CREATE UNIQUE INDEX uq_songs_musicbrainz_id_present ON songs (musicbrainz_id) WHERE musicbrainz_id IS NOT NULL")
    op.execute("CREATE INDEX ix_songs_retrieval_hnsw ON songs USING hnsw (retrieval_vector vector_l2_ops) WITH (m = 16, ef_construction = 64)")

    op.create_table(
        "external_track_refs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("song_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("songs.id"), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("market", sa.String(), nullable=False, server_default=""),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("isrc", sa.String(), nullable=True),
        sa.Column("available", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "external_id", "market", name="uq_external_track_ref"),
    )
    op.create_index("ix_external_track_refs_song_id", "external_track_refs", ["song_id"])
    op.create_index("ix_external_track_refs_provider", "external_track_refs", ["provider"])
    op.create_index("ix_external_track_refs_isrc", "external_track_refs", ["isrc"])

    op.create_table(
        "catalog_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("song_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("songs.id"), nullable=False),
        sa.Column("job_type", sa.String(), nullable=False, server_default="resolve_extract"),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("song_id", "job_type", name="uq_catalog_job_song_type"),
    )
    op.create_index("ix_catalog_jobs_song_id", "catalog_jobs", ["song_id"])
    op.create_index("ix_catalog_jobs_claim", "catalog_jobs", ["status", "available_at", "priority"])

    op.create_table(
        "user_cluster_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("cluster_label", sa.Integer(), nullable=False),
        sa.Column("medoid_song_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("songs.id"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "cluster_label", name="uq_user_cluster_profile"),
    )
    op.create_index("ix_user_cluster_profiles_user_id", "user_cluster_profiles", ["user_id"])


def downgrade() -> None:
    op.drop_table("user_cluster_profiles")
    op.drop_table("catalog_jobs")
    op.drop_table("external_track_refs")
    op.drop_index("ix_songs_retrieval_hnsw", table_name="songs")
    op.drop_index("uq_songs_musicbrainz_id_present", table_name="songs")
    op.drop_column("songs", "last_catalog_error")
    op.drop_column("songs", "popularity_score")
    op.drop_column("songs", "canonical_score")
    op.drop_column("songs", "catalog_status")
    op.drop_column("songs", "retrieval_vector")
