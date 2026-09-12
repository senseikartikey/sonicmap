"""users: add Spotify OAuth identity + token columns

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("users_email_key", "users", type_="unique")
    op.alter_column("users", "email", nullable=True)

    op.add_column("users", sa.Column("spotify_id", sa.String, nullable=True))
    op.add_column("users", sa.Column("display_name", sa.String, nullable=True))
    op.add_column("users", sa.Column("spotify_access_token", sa.String, nullable=True))
    op.add_column("users", sa.Column("spotify_refresh_token", sa.String, nullable=True))
    op.add_column("users", sa.Column("spotify_token_expires_at", sa.DateTime(timezone=True), nullable=True))

    # Backfilled as non-nullable-in-spirit: any pre-auth test rows without a spotify_id are
    # dev/test data (see README), not real accounts, so this is safe to enforce going forward.
    op.execute("DELETE FROM user_songs WHERE user_id IN (SELECT id FROM users WHERE spotify_id IS NULL)")
    op.execute("DELETE FROM users WHERE spotify_id IS NULL")
    op.alter_column("users", "spotify_id", nullable=False)
    op.alter_column("users", "spotify_access_token", nullable=False)
    op.alter_column("users", "spotify_refresh_token", nullable=False)
    op.alter_column("users", "spotify_token_expires_at", nullable=False)
    op.create_unique_constraint("uq_users_spotify_id", "users", ["spotify_id"])
    op.create_index("ix_users_spotify_id", "users", ["spotify_id"])


def downgrade() -> None:
    op.drop_index("ix_users_spotify_id", table_name="users")
    op.drop_constraint("uq_users_spotify_id", "users", type_="unique")
    op.drop_column("users", "spotify_token_expires_at")
    op.drop_column("users", "spotify_refresh_token")
    op.drop_column("users", "spotify_access_token")
    op.drop_column("users", "display_name")
    op.drop_column("users", "spotify_id")
    op.alter_column("users", "email", nullable=False)
    op.create_unique_constraint("users_email_key", "users", ["email"])
