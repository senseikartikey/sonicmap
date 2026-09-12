"""Detects a song's likely language from its title (ISO 639-1, e.g. "en", "ko", "es") for the
recommendations panel's English/mixed-language filter.

This is text-based, not audio-based: there's no lyrics or vocal-transcription pipeline here,
so it's a proxy signal, not ground truth — a song titled in English can still be sung in
another language, and some titles are inherently ambiguous regardless of method ("Espresso"
is an Italian loanword whether or not the song is in English). Good enough to drive a coarse
UI filter; not a claim about what's actually sung on the track.

Uses fastText's lid.176 model rather than a general-purpose language-detection library:
those are built and benchmarked on paragraph-length text and are unreliable on 2-4 word
inputs — Facebook trained lid.176 specifically on short, informal, single-line text (it's the
standard choice for tweet/title-length language ID) and it measurably out-performed
langdetect during evaluation for this exact use case (langdetect called "Espresso" Portuguese
and "CANZONE D'AMORE" English; fastText got both directionally right or at least more
defensible). Below LANGUAGE_CONFIDENCE_THRESHOLD the model's own top prediction is discarded
in favor of returning "unknown" (None) — a wrong guess actively misfiles a song for the
English/mixed toggle, so on short or ambiguous titles it's better to abstain than guess.

detect_language() is the free, instant, local first pass — fastText inference, no network
call, safe to run inline on every song at ingest time. It's also fundamentally blind to
Romanized non-English text it was never trained on (see _ROMANIZED_HINDI_MARKERS below), so
some titles it can't call it just abstains on (returns None) rather than guessing wrong.
classify_languages_via_llm() is the paid-but-cheap batch fallback for exactly those
None-returning cases — Gemini has broad real-world exposure to Romanized Hindi/Tamil/Telugu/
etc. that fastText's training data never had, so it can often resolve what fastText
legitimately can't. Deliberately never called inline per-song (that would put a network call
on the ingest path for every ambiguous title); it's a batch operation meant for a periodic
sweep (see app/scripts/backfill_language.py) over whatever fastText left as None.
"""

import asyncio
import json
import re
from functools import lru_cache

import fasttext
import httpx

from app.config import settings

MODEL_PATH = "/app/models/lid.176.ftz"
TOP_K = 5

# Two different bars, not one: English is the overwhelming majority language in this catalog
# and the only one the UI actually filters on, so a false "unknown"/wrong-other-language on a
# real English title (this model's biggest failure mode on short, common-word titles — see
# language.py's module docstring) directly hurts the feature by hiding songs that should be
# there. A false "en" on a real non-English title is comparatively harmless — it just leaks
# one extra song into "English" mode. So: accept "en" leniently (lower absolute bar, and also
# accept it as a close runner-up rather than requiring it to literally be the top pick), but
# require real separation from second place before asserting any other specific language.
EN_MIN_PROB = 0.20
EN_RUNNER_UP_MARGIN = 0.15
OTHER_MIN_PROB = 0.45


# fastText's lid.176 was trained on real web text in each language's native script — it has
# essentially no exposure to Romanized Hindi/Urdu, so a short transliterated title like "Aane
# Wala Star" or "Allah Ke Bande" often scores as confidently "en" (0.4-0.8) as a real English
# title does. Verified against this catalog's actual data, not assumed: raising EN_MIN_PROB
# can't fix this — several real false positives have "en" as the outright top prediction, and
# raising the bar enough to exclude them also excludes real English titles sitting in the same
# confidence range (e.g. "18 And Life" at en=0.255). A probability threshold can't separate
# two distributions that overlap; a lexical check can. This list is deliberately small and
# high-precision — common Hindi/Urdu function words that are near-certain markers of a
# Romanized Hindi title and essentially never appear in real English song titles — rather than
# a general dictionary, so it can only ever pull a false "en" back to honest "unknown", never
# wrongly flip a real English title away from "en" and never claim a covered case is any *more*
# specific than "not English" (it doesn't guess Hindi vs. Urdu vs. anything else — it abstains,
# same as the rest of this module's confidence-below-threshold cases). Doesn't cover
# Romanized Tamil/Telugu/etc. titles, which fail the same way for the same underlying reason
# but need their own word lists — not attempted here, so treat this as a partial fix, not a
# general solution to "detect language of a Romanized non-English title from text alone".
_ROMANIZED_HINDI_MARKERS = frozenset(
    {
        "mujhe", "tujhe", "usse", "unse", "humse", "raat", "ke", "ki", "ka",
        "hai", "hain", "tera", "teri", "tere", "mera", "meri",
        "mere", "uska", "uski", "unka", "dil", "dilon", "pyar", "pyaar", "ishq", "yaar",
        "zindagi", "kahan", "kyun", "kyu", "nahi", "nahin", "haan", "kaun", "kya",
        "kaise", "tumhe", "tumse", "humein", "aana", "aane", "jaana", "jana", "bande",
        "sanam", "chahiye", "chahta", "chahti", "pehli", "pehla", "bata", "batao",
        "dekho", "chalo", "kabhi", "abhi", "phir", "sirf", "sapna", "sapne", "khwab",
        "duniya", "zamana", "deewana", "deewani", "mohabbat", "ishqbaaz",
        "chaleya", "kamleya", "satranga", "qaafirana", "tenu", "rakhna", "jeene",
        "hoon", "radha", "bairiyaa", "humma", "bahara", "kabira", "samjhawan",
        "saibo", "sona", "kaale", "kaava", "adaa", "aayat", "chashma", "khairiyat",
        "judaai", "raataan", "lambiyan", "soniyo", "jugnu", "naina", "ambar", "dhun",
        "lamhe", "bolna", "janam", "dekhte", "chull", "naam", "likhna",
        # High-precision Romanized Hindi/Urdu/Punjabi markers found in live catalog failures.
        # Exact tokens only: e.g. "tum" does not match English "tumbling".
        "tum", "tujhse", "mujhse", "heer", "saiyaara", "dekha", "bekarar", "toh",
        "jaane", "aankhen", "dilruba", "khushi", "apni", "piya", "sajna", "maahi",
        "mahiya", "ishqe", "tumhein", "tumhain", "agay", "kise", "vichre", "mubarak",
        "sanwarna", "nukte", "rang", "laya", "nasheen", "dillagi", "bhool", "paray",
        "lovesexdhoka",
    }
)
# "wala"/"wali"/"wale" (Hindi "one who does X") were deliberately dropped after a live
# false positive: "Wale" is a real English-language rapper's name (e.g. "No Hands (feat.
# Roscoe Dash & Wale)" — Waka Flocka Flame, a real English song), so this exact word
# demonstrably collides with English titles rather than only Hindi ones. "din" was dropped for
# the same reason — it's a common short English/German token, not a safe marker on its own.


_AMBIGUOUS_ROMANIZED_MARKERS = frozenset({"ke", "ki", "ka", "tum", "tera", "teri", "tere", "mera", "meri", "mere"})


def _looks_romanized_hindi(text: str) -> bool:
    words = set(re.findall(r"[a-z]+", text.casefold()))
    markers = words & _ROMANIZED_HINDI_MARKERS
    # A single short function word is not evidence by itself: "ke" appears in stylized
    # English titles and "tum" appears in Cats' "Rum Tum Tugger". Two such words together,
    # or one distinctive lexical marker, is strong enough for the strict-English guard.
    return bool(markers - _AMBIGUOUS_ROMANIZED_MARKERS) or len(markers) >= 2


_INDIAN_LANGUAGE_ARTIST_MARKERS = (
    "a.r. rahman", "alka yagnik", "amaal mallik", "amitabh bhattacharya", "anirudh ravichander",
    "arijit singh", "asees kaur", "atif aslam", "badshah", "diljit dosanjh", "harshdeep kaur",
    "jubin nautiyal", "kailash kher", "kishore kumar", "kumar sanu", "lata mangeshkar",
    "mithoon", "neha kakkar", "nusrat fateh ali khan", "pritam", "sachin-jigar", "shaan",
    "shilpa rao", "shreya ghoshal", "sonu nigam", "tanishk bagchi", "udit narayan",
    "vishal & shekhar",
    "a.r. rahman", "abida parveen", "afusic", "ali raza", "ali soomro", "ali & shjr",
    "arslan nizami", "faheem abdullah", "haider ali", "hansika pareek", "irshad kamil",
    "mohit chauhan", "osho jain", "pdny", "rahat fateh ali khan", "raj shekhar",
    "sanchi", "the sabri brothers", "tulsi kumar", "vishal mishra",
    # Known failures remain as an offline safety net if the automatic metadata review service
    # is temporarily unavailable; correctness for future artists no longer depends on this.
    "chaar diwaari", "seedhe maut", "sidhu moose wala",
)


def needs_language_review(title: str, artist: str, language: str | None) -> bool:
    """Flags stored-English rows whose title or artist contradicts that classification.

    This is also the gate used to send confidently-but-wrong fastText results through the
    richer metadata-aware classifier. Previously only `None` rows were reviewed, which made a
    wrong confident `en` result permanent.
    """
    if language != "en":
        return language is None
    normalized_artist = artist.casefold()
    return _looks_romanized_hindi(title) or any(
        marker in normalized_artist for marker in _INDIAN_LANGUAGE_ARTIST_MARKERS
    )


def is_english_recommendation_eligible(title: str, artist: str, language: str | None) -> bool:
    """Conservative final guard for the explicitly strict English-only filter.

    Short-text language detection often mistakes Romanized Indian-language titles for English.
    Stored language remains the primary signal; high-confidence lexical and artist evidence
    blocks known false positives. Mixed-language mode remains completely unrestricted.
    """
    if language != "en" or needs_language_review(title, artist, language):
        return False
    return True


@lru_cache(maxsize=1)
def _model() -> "fasttext.FastText._FastText":
    return fasttext.load_model(MODEL_PATH)


def _classify(text: str) -> str | None:
    # fasttext-wheel's public predict() breaks under numpy>=2.0 (np.array(..., copy=False) on
    # a Python list raises ValueError) — calling the pybind binding directly skips that layer
    # entirely, since it returns plain (prob, label) tuples with no numpy conversion at all.
    results = _model().f.predict(text, TOP_K, 0.0, "strict")
    if not results:
        return None

    preds = {label.removeprefix("__label__"): float(prob) for prob, label in results}
    top_lang, top_prob = max(preds.items(), key=lambda kv: kv[1])
    en_prob = preds.get("en", 0.0)

    if en_prob >= EN_MIN_PROB and (top_lang == "en" or en_prob >= top_prob - EN_RUNNER_UP_MARGIN):
        return "en"
    if top_prob >= OTHER_MIN_PROB:
        return top_lang
    return None


def detect_language(title: str, artist: str) -> str | None:
    """Detects on the title alone first — mixing in the artist name systematically biases
    toward whatever script the artist's stage name happens to use (frequently Latin-script
    even for e.g. Japanese or Korean songs), which measurably hurt accuracy in testing rather
    than helped. Falls back to "title + artist" whenever the title alone doesn't classify
    (very short titles routinely produce no prediction at all, not just a low-confidence
    one)."""
    title = title.strip()
    artist = artist.strip()

    for text in filter(None, [title, f"{title} {artist}".strip()]):
        result = _classify(text)
        if result == "en" and not is_english_recommendation_eligible(title, artist, result):
            # The model said "en" with real confidence, but the title itself carries a
            # near-certain Romanized Hindi/Urdu marker word — see _ROMANIZED_HINDI_MARKERS'
            # docstring for why this is checked instead of trusted. Abstain rather than assert
            # a specific wrong language, same policy as every other low-confidence case here.
            return None
        if result is not None:
            return result
    return None


GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODEL = "gemini-3.5-flash-lite"
LLM_BATCH_SIZE = 40  # keeps each prompt/response a manageable size, not a rate-limit workaround

_LLM_RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "STRING"},
            "language": {"type": "STRING"},
        },
        "required": ["id", "language"],
    },
}


_RATE_LIMIT_ROUNDS = 3  # full passes over every key before giving up
_RATE_LIMIT_BASE_DELAY_S = 5.0  # doubles each round: 5s, 10s, 20s — only paid once every key 429s


def _available_keys() -> list[str]:
    """Primary key first, then GEMINI_API_KEYS_EXTRA in order. Only actually spreads load
    across a *per-minute* rate limit if the extra keys come from separate Google Cloud
    projects — Google enforces quota per-project, so keys sharing a project share one pool and
    rotating between them buys nothing (explained in-conversation before these were added).
    Harmless either way: a same-project key just hits the same 429, and the round-based
    backoff below still applies once every key in the list has been tried."""
    keys = [settings.gemini_api_key] if settings.gemini_api_key else []
    keys += [k.strip() for k in settings.gemini_api_keys_extra.split(",") if k.strip()]
    return keys


async def _post_gemini_json(
    prompt: str, schema: dict, model: str = GEMINI_MODEL, timeout: float = 30
) -> object | None:
    """Shared low-level Gemini call for every JSON-schema-constrained batch classifier in this
    codebase (language detection here, artist-identity resolution in
    app/services/artist_identity.py — extracted once a second real caller needed the identical
    boilerplate, not speculatively). Returns the parsed JSON body on success, or None on any
    non-retryable failure — missing API key, network error, malformed response — the same
    degrade-gracefully contract as every other optional-API-key service (see
    app/services/lastfm.py). Callers own validating the parsed shape further; `schema` is a
    request-time hint to Gemini, not a runtime guarantee.

    A 429 (per-minute rate limit, not the daily quota — confirmed live: a single call still
    succeeds immediately after a run that left most of a batch job silently unanswered) no
    longer looks identical to a genuine failure. On a 429 this rotates to the *next available
    key* immediately, rather than waiting — no reason to sleep if a fresh key might just work —
    and only falls back to a real exponential-backoff sleep once every key in the list has
    429'd in the same round, retrying the whole key list again up to _RATE_LIMIT_ROUNDS times.
    Before this, a batch script firing many sequential calls (e.g. app/scripts/backfill_*.py
    re-canonicalizing the whole catalog) would silently lose whatever fraction of the run got
    rate-limited, with no way to tell that apart from "Gemini chose not to answer this one"."""
    keys = _available_keys()
    if not keys:
        return None

    body = None
    delay = _RATE_LIMIT_BASE_DELAY_S
    for round_num in range(_RATE_LIMIT_ROUNDS):
        for key in keys:
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(
                        GEMINI_API_URL.format(model=model),
                        params={"key": key},
                        json={
                            "contents": [{"parts": [{"text": prompt}]}],
                            "generationConfig": {
                                "responseMimeType": "application/json",
                                "responseSchema": schema,
                            },
                        },
                    )
                    if resp.status_code == 429:
                        continue  # try the next key in this same round, no wait
                    resp.raise_for_status()
                    body = resp.json()
                break  # got a real response (success or a non-429, non-raising status)
            except httpx.HTTPError:
                return None  # a genuine failure, not rate-limiting — no key would fix this
        if body is not None:
            break
        # Every key 429'd this round — worth waiting before trying the whole list again,
        # rather than hammering all of them again immediately.
        if round_num < _RATE_LIMIT_ROUNDS - 1:
            await asyncio.sleep(delay)
            delay *= 2

    if body is None:
        return None  # every key stayed rate-limited for every round

    try:
        text = body["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return None


async def _classify_batch_via_llm(batch: list[tuple[str, str, str]]) -> dict[str, str | None]:
    """One Gemini call for up to LLM_BATCH_SIZE songs. `batch` is (id, title, artist) triples —
    `id` is an opaque caller-supplied key (song UUID as a string) so results map back
    unambiguously even if Gemini reorders or drops an item. Returns {} on any failure (see
    _post_gemini_json)."""
    if not batch:
        return {}

    song_list = "\n".join(f'- id={song_id}: "{title}" by {artist}' for song_id, title, artist in batch)
    prompt = (
        "For each song below, guess the language most likely sung on the actual track (not "
        "just the literal text of the title), as an ISO 639-1 code (e.g. \"en\", \"hi\", \"ta\", "
        '"te", "ko", "es"). Many of these are Romanized (Latin-script) titles of songs in '
        "Hindi, Tamil, Telugu, Punjabi, or other Indian languages — judge by the actual words, "
        "artist, and naming conventions, not just whether the letters happen to be Latin "
        'script. If you genuinely cannot tell, use "unknown" rather than guessing. Return '
        "exactly one entry per id, in any order.\n\n"
        f"{song_list}"
    )

    parsed = await _post_gemini_json(prompt, _LLM_RESPONSE_SCHEMA)
    if not isinstance(parsed, list):
        return {}

    results: dict[str, str | None] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        song_id, lang = item.get("id"), item.get("language")
        if not isinstance(song_id, str) or not isinstance(lang, str):
            continue
        results[song_id] = None if lang.strip().lower() == "unknown" else lang.strip().lower()
    return results


async def classify_languages_via_llm(songs: list[tuple[str, str, str]]) -> dict[str, str | None]:
    """The paid batch fallback — call only with songs detect_language() already returned None
    for (see module docstring for why). `songs` is (id, title, artist) triples; returns
    {id: language_or_None}, one entry per input id that Gemini actually answered (an id it
    silently dropped, or the whole call failing, is simply absent from the result — callers
    should treat a missing key the same as an unchanged None, not as an error). Batches
    internally at LLM_BATCH_SIZE per request to keep each call's prompt/response a sane size."""
    results: dict[str, str | None] = {}
    for i in range(0, len(songs), LLM_BATCH_SIZE):
        batch = songs[i : i + LLM_BATCH_SIZE]
        results.update(await _classify_batch_via_llm(batch))
    return results
