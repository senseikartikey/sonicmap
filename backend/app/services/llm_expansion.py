"""Uses an LLM purely as a candidate-generator for catalog expansion — never as a source of
audio-feature truth. Given a handful of real songs from one of a user's actual taste-clusters
(see app/routers/recommend.py), asks for more real songs that would sonically fit alongside
them. Every suggestion still has to survive the exact same iTunes/Deezer resolve + Essentia
extract pipeline any manually-searched song goes through — a hallucinated or malformed
suggestion just fails to resolve, the same as a typo in a user's search box, rather than
polluting the catalog with unverified data. This is what keeps "real audio analysis, not a
black box" true regardless of how a song was discovered.

Gemini specifically (not Claude/GPT) for its free tier — 2.5 Flash-Lite gives 1,000
requests/day with no card on file, which this task's real volume (one call per user per
LLM_EXPANSION_INTERVAL_HOURS, see catalog_expansion.py) never comes close to exhausting.
Degrades to a no-op without GEMINI_API_KEY configured, same contract as
app/services/lastfm.py without LASTFM_API_KEY.
"""

import json

import httpx

from app.config import settings

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MODEL = "gemini-3.5-flash-lite"

# Constrains the response to exactly the shape callers need, so there's no markdown-fence or
# prose-wrapper stripping to get right — Gemini's JSON mode enforces this schema directly
# rather than relying on the prompt asking nicely.
_RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "artist": {"type": "STRING"},
            "title": {"type": "STRING"},
        },
        "required": ["artist", "title"],
    },
}


async def suggest_similar_songs(seed_songs: list[tuple[str, str]], count: int = 15) -> list[tuple[str, str]]:
    """seed_songs: (title, artist) pairs from a real taste-cluster. Returns (artist, title)
    pairs — deliberately artist-first, matching the "Artist - Title" free-text format every
    other ingestion path in this codebase already builds, so callers can hand each result
    straight to itunes.resolve_track without reformatting."""
    if not settings.gemini_api_key or not seed_songs:
        return []

    seed_list = "\n".join(f"- {title} by {artist}" for title, artist in seed_songs)
    prompt = (
        "Here are songs from one listener's taste cluster:\n"
        f"{seed_list}\n\n"
        f"Suggest {count} other real, released songs (not already in this list) that would "
        "sonically fit alongside them — similar tempo, energy, genre, and production style. "
        "Prioritize well-known, correctly-titled tracks a music search API would actually find."
    )

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                GEMINI_API_URL.format(model=MODEL),
                params={"key": settings.gemini_api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "responseSchema": _RESPONSE_SCHEMA,
                    },
                },
            )
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPError:
        return []

    try:
        text = body["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []

    results: list[tuple[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        artist, title = item.get("artist"), item.get("title")
        if isinstance(artist, str) and isinstance(title, str) and artist.strip() and title.strip():
            results.append((artist.strip(), title.strip()))
    return results
