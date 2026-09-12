"""mashup set planner

Revision ID: 0017
Revises: 0016
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    # Track length, captured from the resolver that already returns it. The set planner needs
    # it to state a running time and to place cue points in an exported cue sheet; without it
    # a plan can only be expressed in bars.
    op.add_column("songs", sa.Column("duration_ms", sa.Integer(), nullable=True))

    op.create_table(
        "mashup_sets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("shape", sa.String(16), nullable=False, server_default="arc"),
        sa.Column("source", sa.String(24), nullable=False, server_default="manual"),
        # The whole computed plan — running order, per-transition technique, bars, tempo and
        # key moves, notes and warnings. Held as one document rather than normalised into a
        # transitions table because it is always read and written whole, and because a user
        # editing their set rewrites every downstream transition anyway.
        sa.Column("plan", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("quality", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mashup_sets_user_created", "mashup_sets", ["user_id", "created_at"])


def downgrade():
    op.drop_index("ix_mashup_sets_user_created", table_name="mashup_sets")
    op.drop_table("mashup_sets")
    op.drop_column("songs", "duration_ms")
