"""stem studio jobs and artifacts

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "stem_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("source_name", sa.String(500)),
        sa.Column("model", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("stage", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_stem_jobs_user_id", "stem_jobs", ["user_id"])
    op.create_index("ix_stem_jobs_status", "stem_jobs", ["status"])
    op.create_index("ix_stem_jobs_claim", "stem_jobs", ["status", "created_at"])
    op.create_index("ix_stem_jobs_user_created", "stem_jobs", ["user_id", "created_at"])
    op.create_table(
        "stem_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stem_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", "kind", "name", name="uq_stem_artifact_kind_name"),
    )
    op.create_index("ix_stem_artifacts_job_id", "stem_artifacts", ["job_id"])


def downgrade():
    op.drop_table("stem_artifacts")
    op.drop_table("stem_jobs")
