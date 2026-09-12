# Deploying Sonicmap

Two pieces: the frontend (Vercel) and everything else (one small VM running the same
`docker-compose.yml` you already use locally, plus `docker-compose.prod.yml` on top).

Running Postgres on the VM's own disk rather than a managed free-tier database means the
existing catalog - all ~1.8M imported rows, not just the analyzed ones - fits without any
trimming: a small VM's included disk (40GB+) is enormous compared to the ~1.5GB the database
actually uses today. What follows migrates that real, already-populated database to the
server rather than starting empty and re-running weeks of backfills.

## 1. Get a VM

Any provider works; you need roughly 4-8GB RAM (measured local idle usage across all five
backend services is ~3.7GB). Ubuntu 24.04 LTS, x86_64 - not an ARM instance, Essentia has no
Linux ARM64 wheel.

On the VM:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # log out/in once for this to take effect
```

## 2. Point DNS at it

Two hostnames, both as A records to the VM's IP:

- `api.yourdomain.com` - the backend API
- `storage.yourdomain.com` - MinIO's S3 API (presigned upload/download URLs go straight
  here from the browser, so it needs its own real HTTPS hostname, same as any S3-compatible
  storage would)

No domain yet? A free DDNS host (e.g. DuckDNS) works fine with Caddy's automatic TLS - it
just needs *some* real hostname to request a Let's Encrypt certificate for, not a bare IP.

## 3. Clone the repo and configure secrets

```bash
git clone https://github.com/senseikartikey/sonicmap.git
cd sonicmap
cp .env.production.example .env            # compose-level: domains, DB/MinIO passwords
cp backend/.env.example backend/.env       # app config: OAuth creds, JWT secret, etc.
```

Fill in `.env`:
- `API_DOMAIN`, `STORAGE_DOMAIN`, `FRONTEND_DOMAIN` from step 2 / your Vercel URL
- `POSTGRES_PASSWORD`, `MINIO_ROOT_PASSWORD` - generate with
  `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`, one each

Fill in `backend/.env`:
- `JWT_SECRET` - same generation command, 32+ bytes; anything shorter is rejected at
  startup in favor of a per-process ephemeral key, which would sign every user out on
  every restart
- `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` / `SPOTIFY_REDIRECT_URI` -
  `https://api.yourdomain.com/auth/spotify/callback`. **Add that same redirect URI in the
  Spotify developer dashboard for this app** - it 400s otherwise.
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI` - same idea,
  `https://api.yourdomain.com/auth/google/callback`, added in the Google Cloud Console's
  OAuth client.
- `FRONTEND_BASE_URL` - your real Vercel URL (or custom domain). This is also the only
  origin the API's CORS middleware accepts.
- `LASTFM_API_KEY`, `GEMINI_API_KEY` - carry over from your local `.env`.
- `STEM_YOUTUBE_ENABLED` - left `false` in `docker-compose.prod.yml` regardless of what's
  in this file, on purpose (see the comment there). Flip it in the compose override, not
  here, once you've actually done that review.

## 4. Migrate the existing database

Run this from your machine, where the populated local `db` container already is - don't
start production from an empty database and re-run every backfill script from scratch:

```bash
docker compose exec db pg_dump -U sonicmap -Fc sonicmap > sonicmap.dump
scp sonicmap.dump you@your-vm-ip:~/sonicmap/
```

On the VM, start just the database, then restore into it:

```bash
docker compose up -d db
docker compose exec -T db pg_restore -U sonicmap -d sonicmap --clean --if-exists < sonicmap.dump
docker compose run --rm api alembic upgrade head
```

(The MusicBrainz archive under `backend/catalog-data/` was only ever a one-time import
*source* - the rows it produced are already in the dump above, so it does not need to be
copied to the server at all.)

## 5. Bring the rest up

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose ps
curl -sI https://api.yourdomain.com/health
```

Restarting workers after the initial build only needs `up -d`, not `--build`, unless you've
changed backend code.

## 6. Frontend on Vercel

Import the GitHub repo in Vercel, set the root directory to `frontend/`, and set one
environment variable:

```
NEXT_PUBLIC_API_BASE_URL=https://api.yourdomain.com
```

Deploy. Vercel gives you a `*.vercel.app` URL immediately; a custom domain can be attached
to it later without changing anything else.

## Updating later

```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose exec api alembic upgrade head   # only if a migration was added
```

Vercel redeploys the frontend automatically on push to `main`.
