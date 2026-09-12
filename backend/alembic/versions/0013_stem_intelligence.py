"""persistent stem intelligence

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("stem_jobs", sa.Column("analysis_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("stem_jobs", sa.Column("mapped_song_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_stem_jobs_mapped_song", "stem_jobs", "songs", ["mapped_song_id"], ["id"])


def downgrade():
    op.drop_constraint("fk_stem_jobs_mapped_song", "stem_jobs", type_="foreignkey")
    op.drop_column("stem_jobs", "mapped_song_id")
    op.drop_column("stem_jobs", "analysis_json")
