"""stamp existing permanent catalog failures so the retry cooldown can measure from them

Revision ID: 0018
Revises: 0017

Jobs that gave up before PERMANENT_FAILURE_COOLDOWN existed have no `finished_at`, and the
cooldown is measured from that column. Left null they would either be retried on every enqueue
(the deadlock the cooldown fixes) or never retried again, depending on which side of the check
you read. Stamping them now puts all 13,910 on a normal footing: quiet for the cooldown, then
eligible again like anything else.
"""
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE catalog_jobs SET finished_at = now() "
        "WHERE status = 'failed' AND finished_at IS NULL"
    )


def downgrade():
    # The column is shared with completed jobs, so there is nothing safe to undo here.
    pass
