"""ephemeral guest sessions

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("is_guest", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("guest_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("users", "guest_expires_at")
    op.drop_column("users", "is_guest")
