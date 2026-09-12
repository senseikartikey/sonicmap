"""songs: case-insensitive uniqueness on (title, artist)

The plain uq_songs_title_artist constraint is case-sensitive, so "Rhythm and Blues" and
"Rhythm And Blues" (the same recording, differently capitalized by iTunes/Deezer across
different search queries) could both insert successfully — no IntegrityError, no signal to
the application that anything was wrong. Every dedup-check query in the app was already fixed
to compare case-insensitively (see app/routers/ingest.py, app/services/catalog_expansion.py,
app/scripts/seed_catalog.py), but that only prevents duplicates when the check and the insert
happen to not race — a real DB-level constraint is what actually closes the gap for
concurrent requests, same reason uq_songs_title_artist existed in the first place.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-21
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_songs_title_artist", "songs", type_="unique")
    op.create_index(
        "uq_songs_title_artist_ci",
        "songs",
        [sa.text("lower(title)"), sa.text("lower(artist)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_songs_title_artist_ci", table_name="songs")
    op.create_unique_constraint("uq_songs_title_artist", "songs", ["title", "artist"])
