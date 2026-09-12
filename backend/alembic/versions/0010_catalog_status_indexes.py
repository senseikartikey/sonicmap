"""catalog status and enrichment priority indexes

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-22
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_songs_catalog_status", "songs", ["catalog_status"])
    op.execute("""
        CREATE INDEX ix_songs_catalog_enrichment_priority
        ON songs (catalog_status, popularity_score DESC NULLS LAST, canonical_score DESC NULLS LAST)
        WHERE feature_vector IS NULL
    """)


def downgrade() -> None:
    op.drop_index("ix_songs_catalog_enrichment_priority", table_name="songs")
    op.drop_index("ix_songs_catalog_status", table_name="songs")
