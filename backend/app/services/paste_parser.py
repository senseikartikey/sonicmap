"""Parses a user's raw pasted song list into clean "Artist - Title" query strings — the mess a
fixed-format "one 'Artist - Title' per line" splitter (app/routers/ingest.py's ingest_paste,
before this existed) can't handle: numbered lists ("1. Song - Artist"), inconsistent
separators, "Title - Artist" order instead of "Artist - Title", copy-pasted tracklist noise
(timestamps, section headers, disc/side labels). Each such line still got used *as the whole
search query, verbatim* — every bit of that noise went straight into iTunes' search, quietly
hurting resolution rate for exactly the messy-but-real input real users paste.

Only ever a best-effort cleanup pass — never blocks ingestion. Falls back to the previous naive
line-split behavior whenever Gemini isn't configured, the call fails, or the response doesn't
look usable, same degrade-gracefully contract as every other optional-LLM service here.
"""

from app.services.language import _post_gemini_json

_PASTE_RESPONSE_SCHEMA = {"type": "ARRAY", "items": {"type": "STRING"}}


async def parse_pasted_songs(raw_text: str) -> list[str] | None:
    """Returns a cleaned list of "Artist - Title" query strings in the original order, or None
    if Gemini couldn't be used at all (missing key, network failure, empty/malformed
    response) — callers should fall back to naive line-splitting in that case, never treat
    None as "no songs"."""
    prompt = (
        "The text below is a user-pasted list of songs — it may be numbered, use inconsistent "
        "separators, mix \"Artist - Title\" and \"Title - Artist\" order, or include tracklist "
        "noise like timestamps, disc/side labels, or section headers. Extract every real song "
        'entry as a clean "Artist - Title" string, one per array item, in the same order they '
        "appear. Skip anything that isn't actually a song entry (headers, blank separators, a "
        "bare track number). Don't invent songs that aren't there, and don't merge two "
        "different songs into one entry.\n\n"
        f"{raw_text}"
    )
    parsed = await _post_gemini_json(prompt, _PASTE_RESPONSE_SCHEMA)
    if not isinstance(parsed, list) or not parsed:
        return None
    lines = [line.strip() for line in parsed if isinstance(line, str) and line.strip()]
    return lines or None
