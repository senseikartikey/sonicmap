"""stem content deduplication

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("stem_jobs", sa.Column("source_hash", sa.String(64), nullable=True))
    op.add_column("stem_jobs", sa.Column("reused_from_job_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_stem_jobs_source_hash", "stem_jobs", ["source_hash"])


def downgrade():
    op.drop_index("ix_stem_jobs_source_hash", table_name="stem_jobs")
    op.drop_column("stem_jobs", "reused_from_job_id")
    op.drop_column("stem_jobs", "source_hash")
