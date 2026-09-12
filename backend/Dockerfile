# Essentia has no Windows wheels, so the backend always runs in Linux —
# via this image, both in dev (docker compose) and in prod.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libyaml-dev \
    libfftw3-dev \
    libavcodec-dev \
    libavformat-dev \
    libavutil-dev \
    libswresample-dev \
    libsamplerate0-dev \
    libtag1-dev \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Discogs-EffNet: a genre/style embedding model trained on 2M+ real Discogs-tagged tracks
# (CC BY-NC-SA 4.0 — non-commercial use only, fine for this portfolio project). Baked into
# the image at build time rather than fetched per-request; ~20MB combined.
RUN mkdir -p /app/models && \
    curl -fsSL -o /app/models/discogs-effnet-bs64-1.pb \
      https://essentia.upf.edu/models/feature-extractors/discogs-effnet/discogs-effnet-bs64-1.pb && \
    curl -fsSL -o /app/models/genre_discogs400-discogs-effnet-1.pb \
      https://essentia.upf.edu/models/classification-heads/genre_discogs400/genre_discogs400-discogs-effnet-1.pb && \
    curl -fsSL -o /app/models/genre_discogs400-discogs-effnet-1.json \
      https://essentia.upf.edu/models/classification-heads/genre_discogs400/genre_discogs400-discogs-effnet-1.json

# fastText lid.176: language identification tuned for short text (tweets, titles), unlike
# generic language-detection libraries built for paragraph-length input — see
# app/services/language.py. ~900KB compressed. Public domain / CC BY-SA per fastText's
# published language identification models.
RUN curl -fsSL -o /app/models/lid.176.ftz \
      https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.ftz

COPY app ./app
COPY alembic.ini .
COPY alembic ./alembic

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
