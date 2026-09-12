"""Finds catalog duplicates the existing case-insensitive exact-match dedup
(uq_songs_title_artist_ci, app/routers/ingest.py's func.lower() checks) structurally can't
catch — two rows for the same real recording that differ by more than casing: a remix/version
suffix, reordered or differently-credited featured artists, minor spelling differences. Exact
matching is free and already runs on every ingest; this is the paid, offline sweep for what it
misses, same shape as this codebase's other fuzzy-then-LLM tools (language, artist identity,
genre sanity): a cheap prefilter narrows the search space, Gemini makes the final call only on
what's actually ambiguous.

Prefilter, not Gemini, does the expensive part: comparing every song to every other song
(O(n^2)) doesn't scale, so candidates are only ever compared *within* the same real artist
(using artist_canonical where available, same as the diversity cap — see
app/services/brain.py's _canonical_artist_names) and only when their titles are already
similar by a cheap stdlib string-similarity measure (difflib, no new dependency). Only pairs
that clear that bar ever reach an LLM call.
"""

import difflib
from dataclasses import dataclass

from app.models import Song
from app.services.brain import _canonical_artist_names
from app.services.language import LLM_BATCH_SIZE, _post_gemini_json

# Below this, two titles are treated as clearly different songs — not worth an LLM call. Above
# 0.97, they're treated as certainly the same without needing one (this is essentially what the
# existing case-insensitive exact match already catches, e.g. trailing whitespace/punctuation
# noise) — only the band in between is genuinely ambiguous and worth Gemini's judgment.
TITLE_SIMILARITY_FLOOR = 0.55
TITLE_SIMILARITY_CEILING = 0.97


@dataclass
class DuplicateCandidate:
    song_a: Song
    song_b: Song
    title_similarity: float


def find_candidate_pairs(songs: list[Song]) -> list[DuplicateCandidate]:
    """Groups songs by shared canonical artist name, then within each group flags title pairs
    in the ambiguous similarity band. A song can appear in multiple groups (multi-artist
    credits) — pairs are de-duplicated by song-id pair regardless of which shared artist
    surfaced them."""
    by_artist: dict[str, list[Song]] = {}
    for song in songs:
        for name in _canonical_artist_names(song):
            by_artist.setdefault(name, []).append(song)

    seen_pairs: set[tuple] = set()
    candidates: list[DuplicateCandidate] = []
    for group in by_artist.values():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                pair_key = tuple(sorted((str(a.id), str(b.id))))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                ratio = difflib.SequenceMatcher(None, a.title.lower(), b.title.lower()).ratio()
                if TITLE_SIMILARITY_FLOOR <= ratio < TITLE_SIMILARITY_CEILING:
                    candidates.append(DuplicateCandidate(a, b, ratio))
    return candidates


_DUPLICATE_RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "pair_id": {"type": "STRING"},
            "same_recording": {"type": "BOOLEAN"},
        },
        "required": ["pair_id", "same_recording"],
    },
}


async def _confirm_batch(batch: list[tuple[str, DuplicateCandidate]]) -> dict[str, bool]:
    """batch: (pair_id, candidate) pairs. Returns {pair_id: same_recording} for whatever pairs
    Gemini actually answered."""
    if not batch:
        return {}

    pair_list = "\n".join(
        f'- pair_id={pid}: "{c.song_a.title}" by {c.song_a.artist}  vs.  '
        f'"{c.song_b.title}" by {c.song_b.artist}'
        for pid, c in batch
    )
    prompt = (
        "Each line names two catalog entries with similar titles by the same or related "
        "artist(s). For each pair, decide whether they're the same underlying recording (e.g. "
        "a duplicate entry, a trivial retitle, or the exact same version just credited "
        'slightly differently) — same_recording=true — versus genuinely different songs/'
        "versions (a real remix, a live version, a cover, a sequel/reprise, or simply a "
        'different song that happens to share words) — same_recording=false. When genuinely '
        "unsure, prefer false — treating two different songs as one is worse than leaving two "
        "real duplicates unmerged. Return exactly one entry per pair_id.\n\n"
        f"{pair_list}"
    )

    parsed = await _post_gemini_json(prompt, _DUPLICATE_RESPONSE_SCHEMA)
    if not isinstance(parsed, list):
        return {}

    results: dict[str, bool] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        pair_id, same = item.get("pair_id"), item.get("same_recording")
        if isinstance(pair_id, str) and isinstance(same, bool):
            results[pair_id] = same
    return results


async def confirm_duplicates(candidates: list[DuplicateCandidate]) -> list[DuplicateCandidate]:
    """Filters `candidates` down to the ones Gemini confirms are actually the same recording.
    Batches internally at LLM_BATCH_SIZE."""
    indexed = [(str(i), c) for i, c in enumerate(candidates)]
    results: dict[str, bool] = {}
    for i in range(0, len(indexed), LLM_BATCH_SIZE):
        batch = indexed[i : i + LLM_BATCH_SIZE]
        results.update(await _confirm_batch(batch))
    return [c for pid, c in indexed if results.get(pid) is True]
