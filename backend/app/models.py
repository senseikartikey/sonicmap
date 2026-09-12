import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.db import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    spotify_id: Mapped[str | None] = mapped_column(String, unique=True, index=True, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)

    # Dev-simplicity note: stored plaintext for now. Encrypt at rest (or move to a secrets
    # manager) before this handles real user accounts in production.
    spotify_access_token: Mapped[str | None] = mapped_column(String, nullable=True)
    spotify_refresh_token: Mapped[str | None] = mapped_column(String, nullable=True)
    spotify_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False)
    guest_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # idle | processing | failed — a DB column, not in-memory state, so it's correct across a
    # container restart and (if this ever runs as more than one worker process) across
    # workers too. Set synchronously in the request that kicks off a rebuild, then updated by
    # the background job itself when it finishes — see app/services/brain.py's
    # rebuild_user_map_background and app/routers/map.py.
    map_rebuild_status: Mapped[str] = mapped_column(String, default="idle")

    user_songs: Mapped[list["UserSong"]] = relationship(back_populates="user")


class AuthIdentity(Base):
    __tablename__ = "auth_identities"
    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_auth_identity_provider_user"),
        UniqueConstraint("user_id", "provider", name="uq_auth_identity_user_provider"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(24))
    provider_user_id: Mapped[str] = mapped_column(String(320))
    provider_email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MusicConnection(Base):
    __tablename__ = "music_connections"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_music_connection_user_provider"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(24))
    provider_user_id: Mapped[str | None] = mapped_column(String(320), nullable=True)
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scopes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuthChallenge(Base):
    __tablename__ = "auth_challenges"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Song(Base):
    """Canonical catalog entry. Features are computed once and shared across every user."""

    __tablename__ = "songs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String, index=True)
    artist: Mapped[str] = mapped_column(String, index=True)
    isrc: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    musicbrainz_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    preview_url: Mapped[str | None] = mapped_column(String, nullable=True)

    # Resolution/extraction status so failures are visible instead of silently dropped.
    resolution_status: Mapped[str] = mapped_column(String, default="pending")  # pending|resolved|unresolved
    extraction_status: Mapped[str] = mapped_column(String, default="pending")  # pending|extracted|failed

    feature_vector: Mapped[list[float] | None] = mapped_column(
        Vector(settings.feature_vector_dim), nullable=True
    )
    # Discogs-EffNet embedding (see app/services/extraction.py) — a genre/style-aware signal
    # entirely separate from feature_vector's low-level MFCC/rhythm/tonal descriptors. Kept
    # as its own column (not concatenated into feature_vector) so its much higher
    # dimensionality can't silently dominate feature_vector's distance calculations, and so
    # existing rows don't need a breaking dimension migration.
    genre_vector: Mapped[list[float] | None] = mapped_column(
        Vector(settings.genre_vector_dim), nullable=True
    )
    # A lossy, weighted ANN projection used only to retrieve a bounded candidate shortlist.
    # The final order is always recomputed with exact SonicDistance.
    retrieval_vector: Mapped[list[float] | None] = mapped_column(
        Vector(settings.retrieval_vector_dim), nullable=True
    )
    catalog_status: Mapped[str] = mapped_column(String, default="metadata", index=True)
    canonical_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    popularity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_catalog_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Human-readable, from the same model: genre is the coarse Discogs top-level bucket
    # ("Electronic"), styles is the top few full "Genre---Style" predictions joined for
    # display ("Electronic - IDM, Ambient").
    genre: Mapped[str | None] = mapped_column(String, nullable=True)
    styles: Mapped[str | None] = mapped_column(String, nullable=True)

    # ISO 639-1 code (e.g. "en", "ko", "es") detected from title+artist text — see
    # app/services/language.py. A text-based proxy for vocal language, not ground truth (a
    # song titled in English can still be sung in another language, and vice versa), but
    # good enough to drive the recommendations panel's English/mixed-language filter.
    language: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # Real-world-normalized individual artist name(s) behind this song's (often multi-credit,
    # inconsistently-formatted) `artist` string, " | "-joined — see app/services/artist_identity
    # .py. E.g. "Vishal & Shekhar, Shilpa Rao" resolves to "Vishal Dadlani | Shekhar Ravjiani |
    # Shilpa Rao" so the recommendation diversity cap (app/services/brain.py's
    # _apply_diversity_cap) can recognize the same real artist across differently-credited
    # releases instead of matching the raw string exactly. Null until backfilled; falls back to
    # the raw `artist` string as a single-element set wherever it's unset.
    artist_canonical: Mapped[str | None] = mapped_column(String, nullable=True)

    bpm: Mapped[float | None] = mapped_column(nullable=True)
    key: Mapped[str | None] = mapped_column(String, nullable=True)
    energy: Mapped[float | None] = mapped_column(nullable=True)
    danceability: Mapped[float | None] = mapped_column(nullable=True)
    # Full track length as reported by the resolver (iTunes `trackTimeMillis`), NOT the length
    # of the 30-second preview the features were extracted from. Used by the set planner to
    # state a running time and place cue points; null for rows resolved before 0017.
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Beat and downbeat times within the preview clip, from app/services/beat_grid.py. Bars are
    # the point: the mix player needs downbeats to align two tracks' bar 1, and guessing them in
    # the browser produced half-bar errors that sound like a mistimed fade. Null until analysed.
    beat_grid: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user_songs: Mapped[list["UserSong"]] = relationship(back_populates="song")

    # Case-insensitive: iTunes/Deezer don't return perfectly consistent capitalization for the
    # same underlying recording across different search queries (see alembic/versions/
    # 0006_case_insensitive_song_uniqueness.py for the concrete duplicates a plain
    # case-sensitive constraint let through). Defined after the columns it references —
    # __table_args__ is evaluated as part of the class body, before `title`/`artist` exist as
    # names if declared any earlier.
    __table_args__ = (Index("uq_songs_title_artist_ci", func.lower(title), func.lower(artist), unique=True),)


class UserSong(Base):
    """A user's submission history for a song. This join history is the persistent 'brain':
    per-user recommendations and clustering always read the user's full accumulated set here,
    never just the most recent import."""

    __tablename__ = "user_songs"
    __table_args__ = (UniqueConstraint("user_id", "song_id", "source", name="uq_user_song_source"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    song_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("songs.id"))
    source: Mapped[str] = mapped_column(String)  # search | paste | spotify_playlist
    source_ref: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g. playlist id/name
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # Latest 2D projection for this song within this user's map. Recomputed by the clustering
    # service; kept here (not just in memory) so the map persists across sessions.
    map_x: Mapped[float | None] = mapped_column(nullable=True)
    map_y: Mapped[float | None] = mapped_column(nullable=True)
    cluster_label: Mapped[int | None] = mapped_column(nullable=True)

    user: Mapped[User] = relationship(back_populates="user_songs")
    song: Mapped[Song] = relationship(back_populates="user_songs")


class HiddenSong(Base):
    """A permanent per-user "never recommend this again" — deliberately separate from the
    session-scoped `exclude` query param GET /recommendations already supports, and from
    UserSong (which means "on my map", not "keep away from me"). One row per user+song they've
    dismissed; recommend_for_user filters these out of every future candidate pool."""

    __tablename__ = "hidden_songs"
    __table_args__ = (UniqueConstraint("user_id", "song_id", name="uq_hidden_song"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    song_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("songs.id"))
    hidden_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MashupSet(Base):
    """A saved DJ set: a running order plus the planned blend between every adjacent pair.

    The computed plan lives in one JSONB document rather than a normalised transitions table.
    It is always read and written whole — reordering a single track rewrites every transition
    downstream of it — and keeping it as a document means the planner can add fields (real cue
    points once beat analysis exists, per-stem transition detail) without a migration per idea.
    Only `quality` is lifted out as a column, because it is the one value worth sorting on.
    """

    __tablename__ = "mashup_sets"
    __table_args__ = (Index("ix_mashup_sets_user_created", "user_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    shape: Mapped[str] = mapped_column(String(16), default="arc")
    source: Mapped[str] = mapped_column(String(24), default="manual")  # recommendations|map|manual
    plan: Mapped[dict] = mapped_column(JSONB, default=dict)
    quality: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class ExternalTrackRef(Base):
    """A provider-specific alias for one canonical recording."""

    __tablename__ = "external_track_refs"
    __table_args__ = (
        UniqueConstraint("provider", "external_id", "market", name="uq_external_track_ref"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    song_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("songs.id"), index=True)
    provider: Mapped[str] = mapped_column(String, index=True)
    external_id: Mapped[str] = mapped_column(String)
    market: Mapped[str] = mapped_column(String, default="")
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    isrc: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    available: Mapped[bool] = mapped_column(default=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CatalogJob(Base):
    """Durable PostgreSQL work queue leased with FOR UPDATE SKIP LOCKED."""

    __tablename__ = "catalog_jobs"
    __table_args__ = (
        UniqueConstraint("song_id", "job_type", name="uq_catalog_job_song_type"),
        Index("ix_catalog_jobs_claim", "status", "available_at", "priority"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    song_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("songs.id"), index=True)
    job_type: Mapped[str] = mapped_column(String, default="resolve_extract")
    status: Mapped[str] = mapped_column(String, default="queued")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class UserClusterProfile(Base):
    """Stable ANN seed for a real HDBSCAN cluster; noise remains represented by UserSong."""

    __tablename__ = "user_cluster_profiles"
    __table_args__ = (UniqueConstraint("user_id", "cluster_label", name="uq_user_cluster_profile"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    cluster_label: Mapped[int] = mapped_column(Integer)
    medoid_song_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("songs.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TasteShare(Base):
    __tablename__ = "taste_shares"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), unique=True, index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class UserTasteWeights(Base):
    __tablename__ = "user_taste_weights"
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    genre: Mapped[float] = mapped_column(Float, default=0.45)
    timbre: Mapped[float] = mapped_column(Float, default=0.25)
    rhythm: Mapped[float] = mapped_column(Float, default=0.15)
    tonal: Mapped[float] = mapped_column(Float, default=0.10)
    energy: Mapped[float] = mapped_column(Float, default=0.05)
    feedback_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class RecommendationFeedback(Base):
    __tablename__ = "recommendation_feedback"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    song_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("songs.id"), index=True)
    best_match_song_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("songs.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class StemJob(Base):
    """A private, expiring stem-separation job; never participates in the song catalog."""

    __tablename__ = "stem_jobs"
    __table_args__ = (
        Index("ix_stem_jobs_claim", "status", "created_at"),
        Index("ix_stem_jobs_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    source_type: Mapped[str] = mapped_column(String(16))  # upload | youtube
    source_ref: Mapped[str] = mapped_column(Text)
    source_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    model: Mapped[str] = mapped_column(String(24))  # 4stem | 6stem
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(24), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    analysis_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    mapped_song_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("songs.id"), nullable=True
    )
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    reused_from_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)


class StemArtifact(Base):
    __tablename__ = "stem_artifacts"
    __table_args__ = (UniqueConstraint("job_id", "kind", "name", name="uq_stem_artifact_kind_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stem_jobs.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(24))  # stem | preview | waveform | archive
    name: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
