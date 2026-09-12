"""Flags catalog songs whose Discogs-EffNet-derived `styles` sub-tags (see
app/services/extraction.py — real audio classification, not text-based) look implausible given
the song's evident title/artist/cultural origin — found live in this catalog while
investigating an unrelated language-detection bug: "Allah Ke Bande" (Kailash Kher, a Hindi
devotional song) came back styles="Folk, World, & Country---Laïkó, ...---Éntekhno, Pop---Ballad"
— Greek folk/urban-song sub-genres on a Hindi track. Discogs-EffNet's training data skews
Western; culturally-specific music is exactly where it's most likely to reach for the nearest
Western-taxonomy label it actually knows.

Deliberately more conservative than app/services/language.py's Gemini fallback in two ways:

1. Text alone is a *much* weaker signal for genre than for language — a title rarely hints at
   genre at all ("Angel" says nothing about whether the track is electronic or folk) — so this
   never tries to *re-derive* genre from text. It only flags an existing, real audio-derived
   tag as an obvious mismatch; it never invents a replacement genre.
2. It only ever *removes* specific mismatched style entries, never substitutes a guessed one —
   `genre` is never set to something Gemini merely proposed from text (too coarse to reliably
   judge that way, and getting it wrong costs more than the narrower `styles` field does). A
   "plausible" verdict, or an implausible one where nothing safe to remove was found, both leave
   the row unchanged. What *does* still happen to `genre`: see reconcile_genre() below — if a
   removal strips away the entry `genre` was originally derived from, `genre` is re-derived from
   whatever real (already-classified, never guessed) style now sits first, so the two fields
   never end up contradicting each other.

`styles` is display-only (see app/models.py) — nothing in SonicDistance or clustering reads it,
only the taste-map UI and cluster legend. This is a data-quality/display-correctness fix, not
something that touches ranking.
"""

from app.services.language import LLM_BATCH_SIZE, _post_gemini_json

_GENRE_RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "STRING"},
            "plausible": {"type": "BOOLEAN"},
            "mismatched_styles": {"type": "ARRAY", "items": {"type": "STRING"}},
        },
        "required": ["id", "plausible"],
    },
}


async def _check_batch(
    batch: list[tuple[str, str, str, str | None]]
) -> dict[str, list[str]]:
    """batch: (id, title, artist, current_styles) tuples. Returns {id: [mismatched style
    entries to remove]} — only for songs Gemini actually flagged with specific, confident
    mismatches; a plausible verdict or an implausible one with nothing confidently wrong to
    name is simply absent from the result. Same degrade-gracefully contract as the rest of
    this codebase's optional-LLM services."""
    if not batch:
        return {}

    song_list = "\n".join(
        f'- id={song_id}: "{title}" by {artist} — current styles: {styles or "(none)"}'
        for song_id, title, artist, styles in batch
    )
    prompt = (
        "Each song below has genre/style sub-tags assigned by an audio classifier trained "
        "mostly on Western music taxonomy. You can't hear the audio — judge only whether any "
        "individual style entry looks like an obvious mismatch given the song's title, artist, "
        "and evident cultural/linguistic origin (e.g. a Hindi devotional song tagged with a "
        "Greek folk sub-style). Most tags are correct — only flag plausible=false when there's "
        "a clear, confident mismatch, not merely \"an unusual but possible fusion.\" When you "
        "do flag a song, list the exact mismatched entries (copied verbatim from its current "
        "styles) in mismatched_styles — only entries you're confident are wrong, leaving any "
        "entry that's plausible or you're unsure about out of that list entirely. Return "
        "exactly one entry per id.\n\n"
        f"{song_list}"
    )

    parsed = await _post_gemini_json(prompt, _GENRE_RESPONSE_SCHEMA)
    if not isinstance(parsed, list):
        return {}

    results: dict[str, list[str]] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        song_id, plausible = item.get("id"), item.get("plausible")
        if not isinstance(song_id, str) or not isinstance(plausible, bool) or plausible:
            continue
        mismatched = item.get("mismatched_styles")
        if isinstance(mismatched, list):
            names = [m.strip() for m in mismatched if isinstance(m, str) and m.strip()]
            if names:
                results[song_id] = names
    return results


def remove_mismatched_entries(styles: str, mismatched: list[str]) -> str | None:
    """Exact substring removal, not split-based — the ", "-joined styles field can't be safely
    re-split on "," since the genre names themselves contain commas (e.g. "Folk, World, &
    Country---Laïkó"). Tries removing each entry together with one adjacent separator first, so
    the remaining list doesn't end up with a stray leading/trailing/doubled comma. Returns None
    if removal would leave nothing — skip rather than write an empty/worse result. Shared by
    both the one-off backfill script and the live post-ingest quality sweep (see
    app/services/quality_sweep.py) — was previously duplicated only inside
    app/scripts/backfill_genre_sanity.py."""
    remaining = styles
    for entry in mismatched:
        if f"{entry}, " in remaining:
            remaining = remaining.replace(f"{entry}, ", "", 1)
        elif f", {entry}" in remaining:
            remaining = remaining.replace(f", {entry}", "", 1)
        else:
            remaining = remaining.replace(entry, "", 1)
    remaining = remaining.strip(" ,").strip()
    return remaining or None


async def find_mismatched_styles(
    songs: list[tuple[str, str, str, str | None]]
) -> dict[str, list[str]]:
    """Public batch entry point — songs: (id, title, artist, current_styles) tuples, any size.
    Batches internally at the same size as language.py's LLM_BATCH_SIZE. Returns only songs
    with at least one confidently-flagged mismatched style entry."""
    results: dict[str, list[str]] = {}
    for i in range(0, len(songs), LLM_BATCH_SIZE):
        batch = songs[i : i + LLM_BATCH_SIZE]
        results.update(await _check_batch(batch))
    return results


# Discogs-EffNet's fixed 15-class top-level genre taxonomy (from
# genre_discogs400-discogs-effnet-1.json's `classes`, each "Genre---Style"). Needed here, not
# just a naive split, because "Folk, World, & Country" is itself a single genre name containing
# a comma — the exact same trap backfill_genre_sanity.py's _remove_entries already works around
# by never re-splitting `styles` on "," at all. Sorted longest-first so a prefix check can't
# stop at a shorter genre name that happens to be a substring of a longer one.
_DISCOGS_GENRES = sorted(
    [
        "Blues",
        "Brass & Military",
        "Children's",
        "Classical",
        "Electronic",
        "Folk, World, & Country",
        "Funk / Soul",
        "Hip Hop",
        "Jazz",
        "Latin",
        "Non-Music",
        "Pop",
        "Reggae",
        "Rock",
        "Stage & Screen",
    ],
    key=len,
    reverse=True,
)


def reconcile_genre(genre: str | None, styles: str | None) -> str | None:
    """Real bug found live in the catalog after the first backfill_genre_sanity.py run: e.g.
    "Ishq Ka Raja" ended up genre="Latin" while styles read entirely "Hip Hop---Trap, Hip
    Hop---Cloud Rap" — no Latin anywhere. Cause: extraction.py derives `genre` from the *same*
    top_labels list styles is built from (genre = styles[0]'s Genre half, by construction), but
    once find_mismatched_styles's caller removes styles[0] as an implausible entry, genre is
    left pointing at evidence that no longer exists. `genre` was deliberately left untouched by
    the removal step itself (see this module's docstring — never *invent* a replacement genre
    from text), but that's not what this does either: styles is already real, ranked,
    audio-classifier output, so re-deriving genre from whatever real prediction now sits first
    is propagating existing evidence, not guessing. Pure and deterministic — no LLM call, and a
    no-op for every row that was never touched by a removal (extraction.py already guarantees
    genre == styles[0]'s genre half there, so this only ever fires where that's since drifted).

    A first version of this naively did `styles.split(", ", 1)[0].split("---")[0]` — broke on
    exactly the "Folk, World, & Country" case (its own internal ", " looks identical to an
    entry separator), silently truncating genre to "Folk" for hundreds of rows before the bug
    was caught. Matching against the real fixed genre list instead of guessing where the first
    entry ends is what actually avoids that."""
    if not styles:
        return genre
    positions: list[tuple[int, str]] = []
    counts: dict[str, int] = {}
    for candidate in _DISCOGS_GENRES:
        start = 0
        marker = f"{candidate}---"
        while (position := styles.find(marker, start)) >= 0:
            positions.append((position, candidate))
            counts[candidate] = counts.get(candidate, 0) + 1
            start = position + len(marker)
    if not counts:
        return genre
    # Majority agreement among the retained top styles is more robust than blindly trusting
    # the first style. Ties retain classifier rank by choosing the earliest occurrence.
    first_position = {candidate: min(p for p, name in positions if name == candidate) for candidate in counts}
    return max(counts, key=lambda candidate: (counts[candidate], -first_position[candidate]))
