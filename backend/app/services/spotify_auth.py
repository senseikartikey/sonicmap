"""Spotify OAuth (Authorization Code flow) for login + playlist import.

Deliberately scoped to identity and playlist reading only — audio_features, audio_analysis,
recommendations, and related_artists are dead for any app created after Nov 27 2024 and are
never called here. This only ever touches endpoints that remain open: /authorize, /api/token,
/v1/me, and /v1/playlists/*.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from app.config import settings

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
SCOPES = "user-read-email playlist-read-private playlist-read-collaborative playlist-modify-private"


def build_authorize_url(state: str) -> str:
    params = {
        "client_id": settings.spotify_client_id,
        "response_type": "code",
        "redirect_uri": settings.spotify_redirect_uri,
        "scope": SCOPES,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    expires_at: datetime


async def exchange_code(code: str) -> TokenSet:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.spotify_redirect_uri,
                "client_id": settings.spotify_client_id,
                "client_secret": settings.spotify_client_secret,
            },
        )
        resp.raise_for_status()
        body = resp.json()

    return TokenSet(
        access_token=body["access_token"],
        refresh_token=body["refresh_token"],
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=body["expires_in"]),
    )


async def refresh_access_token(refresh_token: str) -> TokenSet:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.spotify_client_id,
                "client_secret": settings.spotify_client_secret,
            },
        )
        resp.raise_for_status()
        body = resp.json()

    return TokenSet(
        access_token=body["access_token"],
        # Spotify doesn't always return a new refresh_token; keep the old one if so.
        refresh_token=body.get("refresh_token", refresh_token),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=body["expires_in"]),
    )


@dataclass
class SpotifyProfile:
    spotify_id: str
    display_name: str | None
    email: str | None


async def fetch_profile(access_token: str) -> SpotifyProfile:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{API_BASE}/me", headers={"Authorization": f"Bearer {access_token}"})
        resp.raise_for_status()
        body = resp.json()

    return SpotifyProfile(spotify_id=body["id"], display_name=body.get("display_name"), email=body.get("email"))


@dataclass
class PlaylistTrack:
    title: str
    artist: str


def parse_playlist_id(playlist_id_or_url: str) -> str:
    value = playlist_id_or_url.strip()
    if "open.spotify.com/playlist/" in value:
        value = value.split("open.spotify.com/playlist/", 1)[1]
    elif value.startswith("spotify:playlist:"):
        value = value.split(":")[-1]
    return value.split("?", 1)[0]


async def fetch_playlist_tracks(access_token: str, playlist_id_or_url: str) -> list[PlaylistTrack]:
    """As of Spotify's March 2026 Web API migration, /playlists/{id}/tracks is gone for
    Development Mode apps (403 on every playlist, including Spotify's own editorial ones,
    regardless of query params — confirmed live, not assumed) — replaced by /playlists/{id}/
    items, with the per-entry key renamed track -> item. Development Mode also only returns
    playlist contents for playlists the requesting user owns or collaborates on; other
    playlists return metadata only, with no items field at all."""
    playlist_id = parse_playlist_id(playlist_id_or_url)
    tracks: list[PlaylistTrack] = []
    url = f"{API_BASE}/playlists/{playlist_id}/items"
    params = {"fields": "items(item(name,artists(name))),next", "limit": 100}

    async with httpx.AsyncClient(timeout=15) as client:
        while url:
            resp = await client.get(url, headers={"Authorization": f"Bearer {access_token}"}, params=params)
            resp.raise_for_status()
            body = resp.json()
            for entry in body.get("items", []):
                track = entry.get("item")
                if not track:
                    continue
                artists = ", ".join(a["name"] for a in track.get("artists", []))
                tracks.append(PlaylistTrack(title=track["name"], artist=artists))
            url = body.get("next")
            params = None  # `next` is already a full URL with query params baked in

    return tracks
