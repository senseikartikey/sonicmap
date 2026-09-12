import uuid
import logging
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from app.config import settings

ALGORITHM = "HS256"
TOKEN_TTL = timedelta(days=7)
ISSUER = "sonicmap-api"
AUDIENCE = "sonicmap-web"

logger = logging.getLogger(__name__)
if settings.jwt_secret == "dev-only-change-me" or len(settings.jwt_secret.encode()) < 32:
    # A public, documented fallback must never sign a usable token. Development remains
    # zero-config, but restarting the API intentionally invalidates these ephemeral sessions.
    _SIGNING_KEY = secrets.token_urlsafe(48)
    logger.warning("JWT_SECRET is not secure; using an ephemeral key for this development process")
else:
    _SIGNING_KEY = settings.jwt_secret


def create_session_token(user_id: uuid.UUID, ttl: timedelta = TOKEN_TTL) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + ttl,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, _SIGNING_KEY, algorithm=ALGORITHM)


def decode_session_token(token: str) -> uuid.UUID | None:
    try:
        payload = jwt.decode(
            token,
            _SIGNING_KEY,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["sub", "iat", "exp", "iss", "aud", "jti"]},
        )
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError, TypeError):
        return None
