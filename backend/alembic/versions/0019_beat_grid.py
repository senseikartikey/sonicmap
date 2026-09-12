"""per-song beat and downbeat grid

Revision ID: 0019
Revises: 0018

Stored as one JSONB document rather than columns because it is always read and written whole,
and because the shape (beats, downbeats, beats_per_bar, source) is likely to gain fields as the
analyser improves. `source` is inside the document so a future model swap is identifiable
without guessing from the numbers.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("songs", sa.Column("beat_grid", postgresql.JSONB(), nullable=True))
    # The beat worker leases from this index; without it every poll scans the whole job table.
    op.create_index(
        "ix_catalog_jobs_type_claim", "catalog_jobs",
        ["job_type", "status", "available_at", "priority"],
    )


def downgrade():
    op.drop_index("ix_catalog_jobs_type_claim", table_name="catalog_jobs")
    op.drop_column("songs", "beat_grid")
