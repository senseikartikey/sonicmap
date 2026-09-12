"""Shared result shape for every track-resolution source (iTunes, Deezer, ...) — lives outside
both so neither service module has to import the other just to share this type."""

from dataclasses import dataclass


@dataclass
class ResolvedTrack:
    title: str
    artist: str
    preview_url: str | None
    source_track_id: str | None = None
    source: str | None = None
    duration_ms: int | None = None
    """Length of the full track, where the provider reports it. Distinct from the preview
    clip that gets downloaded for feature extraction."""
