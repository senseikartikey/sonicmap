"""Resolves a song's often multi-credit, inconsistently-formatted `artist` string into the
real individual artist(s) behind it — purely so the recommendation diversity cap
(app/services/brain.py's _apply_diversity_cap) can recognize the same real artist across
differently-formatted credits, not as a general "who performed this" feature.

Why this needs an LLM rather than a splitter: naive separator-splitting ("&", ",", "feat.")
gets the *count* of names right but not their *identity* — "Vishal & Shekhar" is itself a
well-known Bollywood composer duo's stage name for Vishal Dadlani and Shekhar Ravjiani, not two
half-written names a splitter could reconstruct. A real "Vishal Dadlani & Shekhar Ravjiani"
credit and a real "Vishal & Shekhar" credit are the same two people under MAX_PER_ARTIST's
diversity cap, but no string transformation turns one into the other — recognizing that takes
the same real-world knowledge an LLM already has, the same reason app/services/language.py
escalates to Gemini for what fastText structurally can't read.

Text-only, same as language detection — this has no more claim to "ground truth" than a
person's own best guess reading the same credit string would, and is only ever used to loosen
(never to tighten) the diversity cap: a wrong guess here means two actually-different artists
occasionally get treated as one (a candidate that would've been allowed gets capped), not a
false claim about who performed what.
"""

from app.services.language import GEMINI_API_URL, GEMINI_MODEL, _post_gemini_json

# Same as language.py's LLM_BATCH_SIZE. Tried smaller (15) on the theory that heavier
# per-song responses (a whole array of full names, not one short code) were getting truncated
# mid-batch — that made coverage *worse*, not better, which pointed at the real cause instead:
# per-minute rate limiting (confirmed live — a single call succeeded immediately after a run
# that left most of a batch job unanswered), and smaller batches meant more total sequential
# calls hitting that limit more often. _post_gemini_json now retries a 429 with backoff instead
# of silently giving up, which is the actual fix; batch size went back to a normal size since
# fewer total requests is strictly better once rate-limit retries are handled properly.
_ARTIST_BATCH_SIZE = 40

_ARTIST_RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "STRING"},
            "artists": {"type": "ARRAY", "items": {"type": "STRING"}},
        },
        "required": ["id", "artists"],
    },
}


async def _canonicalize_batch(batch: list[tuple[str, str]]) -> dict[str, list[str]]:
    """batch: (id, raw_artist_string) pairs. Returns {id: [canonical individual artist names]}
    for whatever ids Gemini actually answered — same degrade-gracefully contract as
    language.py's classify_languages_via_llm (missing key, network error, malformed response
    all just mean that id is absent from the result, not an exception)."""
    if not batch:
        return {}

    credit_list = "\n".join(f'- id={song_id}: "{artist}"' for song_id, artist in batch)
    prompt = (
        "Each line below is a song's artist credit string, exactly as stored (it may name one "
        "artist, a duo/group stage name, or several comma/&-separated collaborators). For each "
        "id, list the real individual(s) or named group(s) actually behind that credit. The "
        "goal is recognizing when two differently-worded credits refer to the same real "
        "person(s) — e.g. this same catalog credits one composer duo as both \"Ajay-Atul\" and "
        "\"Ajay Gogavale\" (one member's real name) in different songs, and those need to "
        "resolve to the same identity to be recognized as one artist. So: a solo performer's "
        "well-known stage/performing name (e.g. \"Drake\", \"Eminem\") is who they are — return "
        "it completely unchanged, never substitute a birth/legal name for a solo act you happen "
        "to know one for. But a duo/group credited under a shared alias should resolve to each "
        "member's actual full name when you know them (never truncate to first names alone — "
        "\"Vishal & Shekhar\" resolves to \"Vishal Dadlani\" and \"Shekhar Ravjiani\", not "
        "\"Vishal\" and \"Shekhar\"), since that's what lets a later credit naming one member by "
        "their real name match back to this one. If you don't confidently know a group's real "
        "member names, return the group's own name unchanged as one entry rather than guessing "
        "or truncating it. Split several genuinely separate collaborators into separate entries "
        "the same way. Return exactly one entry per id.\n\n"
        f"{credit_list}"
    )

    parsed = await _post_gemini_json(prompt, _ARTIST_RESPONSE_SCHEMA)
    if not isinstance(parsed, list):
        return {}

    results: dict[str, list[str]] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        song_id, artists = item.get("id"), item.get("artists")
        if not isinstance(song_id, str) or not isinstance(artists, list):
            continue
        names = [a.strip() for a in artists if isinstance(a, str) and a.strip()]
        if names:
            results[song_id] = names
    return results


async def canonicalize_artists(songs: list[tuple[str, str]]) -> dict[str, list[str]]:
    """The public batch entry point — songs: (id, raw_artist_string) pairs, any size; batches
    internally at _ARTIST_BATCH_SIZE per Gemini call."""
    results: dict[str, list[str]] = {}
    for i in range(0, len(songs), _ARTIST_BATCH_SIZE):
        batch = songs[i : i + _ARTIST_BATCH_SIZE]
        results.update(await _canonicalize_batch(batch))
    return results
