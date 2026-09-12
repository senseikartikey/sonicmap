# Sonicmap — Music Feature Recommendation Engine

Sonicmap maps relationships between songs using real audio extraction rather than relying
only on collaborative filtering. Each user gets a persistent taste “brain” built from every
song they add, with interactive 2D, list, and 3D views.

## Development

The backend runs in Docker/Linux because Essentia has no supported Windows wheels:

```bash
docker compose up -d --build
docker compose exec api alembic upgrade head
```

Run the frontend from `frontend/`:

```bash
npm run dev -- -p 3001 -H 127.0.0.1
```

- API and OpenAPI docs: `http://127.0.0.1:8000/docs`
- Frontend: `http://127.0.0.1:3001`
- Stem Studio: `http://127.0.0.1:3001/studio/stems`
- Local private object storage console: `http://127.0.0.1:9001`

Use the exact loopback host and port: Spotify OAuth, backend CORS, and browser storage are
configured for that origin.

## Stem Studio

Stem Studio keeps heavy separation isolated from the recommendation API. The API issues private
presigned uploads, PostgreSQL leases work to `stem-worker`, and MinIO holds source/derived
media temporarily. Sources are removed after processing; stems, previews, waveform peaks,
and ZIP exports expire after 24 hours. Abandoned uploads are swept on the same schedule.

Each stem also produces a compact 32-dimensional, SonicDistance-compatible fingerprint.
These derived numbers persist after the private audio expires and power Taste X-Ray plus
`Discover from this mix`: the core cluster-consensus recommender creates the shortlist, then
the user's audible mute/solo/volume balance reranks it with a 45% core-brain guardrail. Adding
the source or a discovery to the map always reuses the canonical ingest/extract/rebuild path.

Local Compose builds a CPU worker. It downloads Demucs model weights on the first real job
and caches them in the `stem_models` volume. Production GPU images use the same Dockerfile
with a CUDA PyTorch wheel index suitable for the host, for example:

```bash
docker build -f backend/Dockerfile.stems backend \
  --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128 \
  -t sonicmap-stem-worker:gpu
```

YouTube acquisition is separately feature-flagged and disabled by default. Set
`STEM_YOUTUBE_ENABLED=true` only after the deployment's product/legal review. Uploaded audio
works without enabling YouTube. See `backend/.env.example` for storage and quota settings.

## Scalable catalog

The catalog has two tiers: canonical searchable metadata and acoustically analyzed tracks.
Recommendations use pgvector HNSW to shortlist analyzed tracks, then preserve the exact
SonicDistance cluster-consensus reranker. A durable PostgreSQL queue and the independent
`catalog-worker` service progressively analyze high-priority metadata-only tracks.

To bootstrap one million local metadata records:

1. Download the latest `musicbrainz-canonical-dump-*.tar.zst` from
   `https://data.metabrainz.org/pub/musicbrainz/canonical_data/`. The archive is currently
   roughly 2 GB compressed, so startup intentionally does not download it automatically.
2. Place it in `backend/catalog-data/`.
3. Rebuild the API image after dependency changes, import, and seed analysis jobs:

```bash
docker compose build api catalog-worker
docker compose run --rm api python -m app.scripts.import_musicbrainz_catalog \
  /catalog-data/musicbrainz-canonical-dump-YYYYMMDD-HHMMSS.tar.zst --limit 1000000
docker compose run --rm api python -m app.scripts.import_musicbrainz_catalog \
  /catalog-data/musicbrainz-canonical-dump-YYYYMMDD-HHMMSS.tar.zst \
  --limit 1000000 --sample-modulus 5
docker compose run --rm api python -m app.scripts.seed_catalog_jobs --limit 25000
docker compose up -d catalog-worker
```

The first import establishes dense coverage of the archive's leading catalog region; the
deterministically sampled second pass removes physical-row ordering bias and broadens artist
coverage. Both stream directly from the compressed archive, are safe to rerun, and do not
unpack the dataset. Monitor progress through authenticated `GET /catalog/status` or the
dashboard recommendation panel.

## Set Studio

Set Studio plans a beat-matched, harmonically-ordered DJ set from a user's recommendations or
map, entirely from stored song metadata (tempo, key, energy, feature/genre vectors) - it needs
no audio rights and works on any playlist. The engine (`backend/app/services/mashup.py`) scores
every ordered pair on tempo fit, Camelot-wheel harmonic distance, SonicDistance, and an energy
arc, then sequences the set exactly (Held-Karp) for small sets or with a 2-opt heuristic above
`EXACT_SOLVE_LIMIT` tracks. Each transition picks a real DJ technique (bass swap, double drop,
rolling, long blend, echo out, or a cut when the tempos are too far apart) and a phrase length.
Finished sets export to rekordbox XML, M3U8, or a CUE sheet.

The frontend can also *audition* a planned transition using the two providers' 30-second preview
clips: `frontend/src/lib/mixPlayer.ts` runs a real two-deck Web Audio mixer (per-deck EQ shelves,
tempo-matched playback rate, phrase-aware crossfades). Alignment needs to know where the bars
are, not just the beats, so a separate `beat-worker` service (`backend/app/scripts/beat_worker.py`,
using `beat-this`, CPJKU/ISMIR 2024) analyzes each preview clip's beat and downbeat grid once and
stores it on the song (`songs.beat_grid`). It shares the same durable Postgres job queue as
catalog enrichment, under `job_type="beat_grid"`.

Spotify OAuth, search/paste/playlist ingestion, persistent maps, recommendations, catalog
enrichment, Set Studio, and all three signed-in visualizations are implemented. `PRODUCT.md`
and `DESIGN.md` describe deeper product and design decisions.

## Deploying

`deploy/DEPLOY.md` walks through a production deployment: the frontend on Vercel, everything
else (Postgres, MinIO, and the four backend services) on a single small VM behind Caddy for
automatic HTTPS. `docker-compose.prod.yml` is the hardened override used for that.
