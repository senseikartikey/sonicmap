"""product features

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("taste_shares", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False), sa.Column("token", sa.String(64), nullable=False), sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("user_id"), sa.UniqueConstraint("token"))
    op.create_index("ix_taste_shares_user_id", "taste_shares", ["user_id"])
    op.create_index("ix_taste_shares_token", "taste_shares", ["token"])
    op.create_table("user_taste_weights", sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True), sa.Column("genre", sa.Float(), nullable=False, server_default="0.45"), sa.Column("timbre", sa.Float(), nullable=False, server_default="0.25"), sa.Column("rhythm", sa.Float(), nullable=False, server_default="0.15"), sa.Column("tonal", sa.Float(), nullable=False, server_default="0.10"), sa.Column("energy", sa.Float(), nullable=False, server_default="0.05"), sa.Column("feedback_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("recommendation_feedback", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False), sa.Column("song_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("songs.id"), nullable=False), sa.Column("best_match_song_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("songs.id")), sa.Column("action", sa.String(32), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_recommendation_feedback_user_id", "recommendation_feedback", ["user_id"])
    op.create_index("ix_recommendation_feedback_song_id", "recommendation_feedback", ["song_id"])

def downgrade():
    op.drop_table("recommendation_feedback")
    op.drop_table("user_taste_weights")
    op.drop_table("taste_shares")
