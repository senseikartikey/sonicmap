from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://sonicmap:sonicmap@localhost:5432/sonicmap"
    itunes_search_base_url: str = "https://itunes.apple.com/search"
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    # Spotify requires the literal loopback IP (not the "localhost" hostname) for HTTP
    # redirect URIs since their Nov 2025 OAuth migration - keep this and frontend_base_url
    # both on 127.0.0.1 so the browser sees one consistent origin across the whole login flow
    # (localhost vs 127.0.0.1 are different origins, which would break localStorage/CORS).
    spotify_redirect_uri: str = "http://127.0.0.1:8000/auth/spotify/callback"
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://127.0.0.1:8000/auth/google/callback"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_use_tls: bool = True
    jwt_secret: str = "dev-only-change-me"
    app_environment: str = "development"
    magic_link_enabled: bool = False
    rate_limit_enabled: bool = True
    frontend_base_url: str = "http://127.0.0.1:3001"

    def validate_security(self) -> None:
        """Refuse publicly deployed instances that still carry documented dev credentials."""
        if self.app_environment.casefold() in {"production", "prod"}:
            if self.jwt_secret == "dev-only-change-me" or len(self.jwt_secret.encode()) < 32:
                raise RuntimeError("Production requires a unique JWT_SECRET of at least 32 bytes")
            if self.stem_studio_enabled and self.stem_s3_secret_key == "sonicmap-dev-secret":
                raise RuntimeError("Production Stem Studio requires unique object-storage credentials")

    # Fixed-length feature vector produced by the extraction service.
    # See app/services/extraction.py for what fills each slot.
    feature_vector_dim: int = 32
    # Output dimensionality of the Discogs-EffNet embedding layer (fixed by that model).
    genre_vector_dim: int = 1280
    # ANN-only vector: genre (1280) + MFCC (26) + rhythm (2) + tonal (3) + energy (1).
    # Exact ranking still uses SonicDistance over the source vectors.
    retrieval_vector_dim: int = 1312

    catalog_worker_concurrency: int = 2
    catalog_job_max_attempts: int = 5
    ann_recommendations_enabled: bool = True
    ann_candidates_per_seed: int = 300
    ann_candidate_cap: int = 5000

    # Optional: enables the collaborative "similar artist" recommendation channel (see
    # app/services/lastfm.py). Free key from https://www.last.fm/api/account/create — no
    # OAuth, just a static app key. Every caller of lastfm.py degrades to a no-op without one,
    # so recommend_for_user falls back to today's pure-SonicDistance ranking until it's set.
    lastfm_api_key: str = ""

    # Optional: enables LLM-assisted catalog expansion (see app/services/llm_expansion.py) —
    # the LLM only ever proposes real song titles/artists worth looking up, never a feature
    # value; every suggestion still goes through the same iTunes/Deezer resolve + Essentia
    # extract pipeline as any other song. A no-op without a key set, same degrade-gracefully
    # contract as lastfm_api_key above. Gemini specifically for its free tier (1,000
    # requests/day on 2.5 Flash-Lite, no card required) — this task's real volume never gets
    # close to that. Get one from https://aistudio.google.com/apikey.
    gemini_api_key: str = ""
    # Optional extra keys, comma-separated, tried in order after gemini_api_key when a call
    # gets rate-limited (429) — see app/services/language.py's _post_gemini_json. Only actually
    # helps against a *per-minute* rate limit if these come from separate Google Cloud
    # projects; Google enforces quota per-project, so multiple keys under the same project
    # share one pool and rotating between them does nothing (see the same explanation given
    # in-conversation before these were added). Harmless either way — worst case a same-project
    # key just gets rate-limited too and the retry/backoff in _post_gemini_json still applies.
    gemini_api_keys_extra: str = ""

    # Stem Studio is deliberately isolated from the catalog/recommendation pipeline. Objects
    # live in a private S3-compatible bucket and are deleted after a short retention window.
    stem_studio_enabled: bool = True
    stem_youtube_enabled: bool = False
    stem_s3_endpoint_url: str = "http://localhost:9000"
    stem_s3_public_endpoint_url: str = "http://localhost:9000"
    stem_s3_region: str = "us-east-1"
    stem_s3_access_key: str = "sonicmap"
    stem_s3_secret_key: str = "sonicmap-dev-secret"
    stem_s3_bucket: str = "sonicmap-stems"
    stem_retention_hours: int = 24
    stem_daily_job_limit: int = 3
    stem_max_active_jobs: int = 1
    stem_max_upload_bytes: int = 250 * 1024 * 1024
    stem_max_duration_seconds: int = 15 * 60
    stem_worker_max_attempts: int = 3


settings = Settings()
