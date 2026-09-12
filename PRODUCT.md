# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary user: a music obsessive who wants to explore how their songs actually sound alike —
tempo, key, energy, danceability, timbre — not what a collaborative-filtering algorithm thinks
people "like them" streamed next. They arrive with a backlog of songs (search results, pasted
lists, or a Spotify playlist) and want to watch their own taste take physical shape as a map,
then wander it. Discovery and exploration are the point, not just extracting a ranked list.

## Product Purpose

Sonicmap builds a persistent, per-user "brain" from every song the user ever feeds it, places
those songs on an interactive 2D map by real extracted audio features (via Essentia), and
surfaces recommendations by proximity in that feature space. Success is a user recognizing their
own taste in the shape of the map and finding something genuinely new near the edges of a
cluster.

## Positioning

Competing recommenders infer similarity from what other listeners did (collaborative filtering).
Sonicmap infers similarity from what the song actually *sounds like* — BPM, key, energy,
danceability, and other features extracted directly from audio — and shows that similarity as a
spatial map the user can see and navigate, not a hidden ranking function.

## Operating Context

- User signs in with Spotify (OAuth; session token stored client-side) to establish an account
  and, optionally, import a Spotify playlist directly.
- Three ingestion paths: search-and-add a single song, paste a raw song list (one per line), or
  import a Spotify playlist by URL/ID.
- After ingestion, the user triggers a map rebuild (async pipeline: iTunes resolution → Essentia
  audio-feature extraction → shared catalog storage → per-user clustering/placement).
- The map and recommendations are pulled and re-rendered after rebuild; songs can be
  mid-pipeline (resolution/extraction still in progress) and the UI must represent that.
- Backend runs only in Docker (Essentia has no Windows wheels) — this shapes what "live" local
  iteration looks like but not the design itself.
- The primary map (default view) renders via DOM/SVG/Canvas2D, not WebGL/Three.js — this
  remains the binding constraint for that view specifically. It's no longer a whole-app rule:
  a third "Brain" view (`TasteBrain.tsx`) renders the same songs as a WebGL/Three.js
  brain-shaped particle cloud, added deliberately alongside the map and list views, never
  replacing either — the non-spatial list view remains the accessible fallback the
  Accessibility section below commits to, since the 3D view doesn't attempt to satisfy that on
  its own.

## Capabilities and Constraints

- Spotify OAuth login and Spotify playlist import are live, working integrations for this app —
  keep them as first-class entry points, not degraded/legacy paths.
- Search/paste ingestion require no auth to call today per current backend quickstart, but the
  frontend's real flow is sign-in-gated; design for the signed-in product experience as primary.
- Song state is multi-stage: `resolution_status` and `extraction_status` are independent
  pipeline stages a song moves through before it has full audio features (bpm, key, energy,
  danceability) and a map position. The design must account for not-yet-placed songs.
- Map points carry `x`, `y` (nullable until placed) and a `cluster_label`.
- Recommendations return a `distance` and a `reason` string per song.
- Full scope/architecture is documented outside this repo in
  `../i-wanna-create-a-fluffy-dragon.md` (not present in this checkout) — treat this PRODUCT.md
  and the current backend API/README as the source of truth where they conflict with anything
  inferred elsewhere.

## Evidence on Hand

- Working backend API (FastAPI) with endpoints for auth, ingest (search/paste/spotify-playlist),
  map, map/rebuild, recommendations — see `backend/app` and `README.md` for the live contract.
- Existing frontend (`frontend/src/app/page.tsx`) is an explicitly-labeled functional
  placeholder wired to the real backend; it is evidence of the real data shapes and flows, not a
  visual authority to preserve.
- No brand assets, logo, or existing visual identity beyond the placeholder's dark/black theme
  choice — treat as unset rather than binding.

## Product Principles

1. The map is the product — every other surface (ingestion, recommendations, auth) exists to
   feed it or act on it, and should stay out of its way once it's populated.
2. Show real pipeline state, never fake it — songs mid-resolution/mid-extraction are a normal,
   frequent state, not an edge case to hide.
3. Similarity should be legible spatially, not just numerically — proximity, clustering, and
   distance in the UI should visibly correspond to the underlying feature-space math.
4. Bring-your-own-catalog first — search, paste, and Spotify import are equally primary ways in;
   no path should feel like an afterthought.

## Accessibility & Inclusion

No product-specific accessibility requirement established yet. The core map is inherently
visual/spatial; the design pass should still define a non-spatial (list/table) way to reach the
same songs, features, and recommendations for users who can't use the spatial map.
