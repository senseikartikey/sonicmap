"""provider independent authentication

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("users", "spotify_id", existing_type=sa.String(), nullable=True)
    op.alter_column("users", "spotify_access_token", existing_type=sa.String(), nullable=True)
    op.alter_column("users", "spotify_refresh_token", existing_type=sa.String(), nullable=True)
    op.alter_column("users", "spotify_token_expires_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.create_table("auth_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False), sa.Column("provider_user_id", sa.String(320), nullable=False),
        sa.Column("provider_email", sa.String(320)), sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "provider_user_id", name="uq_auth_identity_provider_user"),
        sa.UniqueConstraint("user_id", "provider", name="uq_auth_identity_user_provider"))
    op.create_index("ix_auth_identities_user_id", "auth_identities", ["user_id"])
    op.create_index("ix_auth_identities_provider_email", "auth_identities", ["provider_email"])
    op.create_table("music_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False), sa.Column("provider_user_id", sa.String(320)),
        sa.Column("access_token", sa.Text()), sa.Column("refresh_token", sa.Text()),
        sa.Column("token_expires_at", sa.DateTime(timezone=True)), sa.Column("scopes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "provider", name="uq_music_connection_user_provider"))
    op.create_index("ix_music_connections_user_id", "music_connections", ["user_id"])
    op.create_table("auth_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True), sa.Column("email", sa.String(320)),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_auth_challenges_kind", "auth_challenges", ["kind"])
    op.create_index("ix_auth_challenges_token_hash", "auth_challenges", ["token_hash"])
    op.create_index("ix_auth_challenges_expires_at", "auth_challenges", ["expires_at"])
    op.execute("""INSERT INTO auth_identities (id,user_id,provider,provider_user_id,provider_email,email_verified,created_at)
        SELECT gen_random_uuid(),id,'spotify',spotify_id,email,true,created_at FROM users WHERE spotify_id IS NOT NULL""")
    op.execute("""INSERT INTO music_connections (id,user_id,provider,provider_user_id,access_token,refresh_token,token_expires_at,scopes,created_at,updated_at)
        SELECT gen_random_uuid(),id,'spotify',spotify_id,spotify_access_token,spotify_refresh_token,spotify_token_expires_at,
        'user-read-email playlist-read-private playlist-read-collaborative playlist-modify-private',created_at,created_at
        FROM users WHERE spotify_id IS NOT NULL""")


def downgrade():
    op.drop_table("auth_challenges"); op.drop_table("music_connections"); op.drop_table("auth_identities")
    op.alter_column("users", "spotify_token_expires_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column("users", "spotify_refresh_token", existing_type=sa.String(), nullable=False)
    op.alter_column("users", "spotify_access_token", existing_type=sa.String(), nullable=False)
    op.alter_column("users", "spotify_id", existing_type=sa.String(), nullable=False)
