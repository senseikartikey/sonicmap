"""songs: add genre-aware embedding (Discogs-EffNet) + human-readable genre/styles

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

GENRE_VECTOR_DIM = 1280


def upgrade() -> None:
    op.add_column("songs", sa.Column("genre_vector", Vector(GENRE_VECTOR_DIM), nullable=True))
    op.add_column("songs", sa.Column("genre", sa.String, nullable=True))
    op.add_column("songs", sa.Column("styles", sa.String, nullable=True))
    # No ivfflat index here (unlike feature_vector) — ranking against genre_vector happens
    # in Python as part of the weighted SonicDistance metric (app/services/brain.py), not
    # via a SQL ORDER BY, so an ANN index isn't earning its cost at this catalog's scale.


def downgrade() -> None:
    op.drop_column("songs", "styles")
    op.drop_column("songs", "genre")
    op.drop_column("songs", "genre_vector")
