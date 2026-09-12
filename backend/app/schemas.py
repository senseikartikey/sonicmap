import uuid
from typing import Literal

from pydantic import BaseModel, Field


class SongOut(BaseModel):
    id: uuid.UUID
    title: str
    artist: str
    resolution_status: str
    extraction_status: str
    bpm: float | None = None
    key: str | None = None
    energy: float | None = None
    danceability: float | None = None
    genre: str | None = None
    styles: str | None = None
    language: str | None = None
    preview_url: str | None = None

    model_config = {"from_attributes": True}


class CurrentUserOut(BaseModel):
    id: uuid.UUID
    spotify_id: str | None
    display_name: str | None
    email: str | None
    auth_providers: list[str] = Field(default_factory=list)
    spotify_connected: bool = False
    is_guest: bool = False

    model_config = {"from_attributes": True}


class MagicLinkRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class IngestSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=300)  # e.g. "Radiohead - Weird Fishes"


class SearchSuggestion(BaseModel):
    title: str
    artist: str


class IngestPasteRequest(BaseModel):
    raw_text: str = Field(min_length=2, max_length=50_000)
    source_ref: str | None = Field(default=None, max_length=500)


class IngestSpotifyPlaylistRequest(BaseModel):
    playlist_id_or_url: str = Field(min_length=5, max_length=500)


class IngestResult(BaseModel):
    song: SongOut
    was_new: bool


class MapPoint(BaseModel):
    song: SongOut
    x: float | None
    y: float | None
    cluster_label: int | None
    added_at: str
    source: str


class RecommendationOut(BaseModel):
    song: SongOut
    distance: float
    reason: str
    best_match_song_id: uuid.UUID
    xray: dict[str, float] = Field(default_factory=dict)


class FeedbackIn(BaseModel):
    song_id: uuid.UUID
    best_match_song_id: uuid.UUID | None = None
    action: Literal["more_like", "less_like", "save", "wrong_genre", "wrong_language"]


class PromptDiscoveryIn(BaseModel):
    prompt: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=12, ge=1, le=30)


class JourneyIn(BaseModel):
    song_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    name: str = Field(default="My Sonicmap Journey", min_length=1, max_length=100)


class ShareCompareIn(BaseModel):
    token: str = Field(min_length=20, max_length=64)


class CatalogStatusOut(BaseModel):
    metadata: int
    analyzed: int
    queued: int
    failed: int
    version: int
    enrichment_active: bool


class StemUploadIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=3, max_length=100)
    size_bytes: int = Field(gt=0)


class StemJobIn(BaseModel):
    source_type: Literal["upload", "youtube"]
    upload_key: str | None = Field(default=None, max_length=1000)
    youtube_url: str | None = Field(default=None, max_length=1000)
    source_name: str | None = Field(default=None, max_length=500)
    model: Literal["4stem", "6stem"] = "4stem"
    rights_confirmed: bool


class StemMixIn(BaseModel):
    weights: dict[str, float] = Field(default_factory=dict)
    limit: int = Field(default=12, ge=1, le=30)


class StemPromptIn(BaseModel):
    prompt: str = Field(min_length=2, max_length=300)
    weights: dict[str, float] = Field(default_factory=dict)


# --- Mashup set planner -------------------------------------------------------------------
EnergyShapeIn = Literal["arc", "build", "peak", "wind_down", "wave"]

# Bounded because the exact sequencer is exponential and the heuristic is O(n^2) per 2-opt
# sweep; 60 tracks is already a five-hour set, well past any real use for one plan.
MAX_SET_TRACKS = 60


class MashupPlanIn(BaseModel):
    """Either name the tracks explicitly, or ask for the current recommendations."""

    song_ids: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_SET_TRACKS)
    source: Literal["manual", "recommendations", "map"] = "manual"
    limit: int = Field(default=12, ge=2, le=MAX_SET_TRACKS)
    """Only consulted when `source` is recommendations or map — how many to pull."""
    shape: EnergyShapeIn = "arc"
    open_with: uuid.UUID | None = None
    name: str | None = Field(default=None, max_length=200)
    save: bool = True


class MashupReorderIn(BaseModel):
    """A DJ's own running order. Every transition downstream is replanned against it — the
    engine never silently keeps a stale blend beside a track that moved."""

    song_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_SET_TRACKS)


class MashupRenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
