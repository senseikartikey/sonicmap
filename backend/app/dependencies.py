from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import MusicConnection, User
from sqlalchemy import select
from app.services.jwt_auth import decode_session_token
from app.services.spotify_auth import refresh_access_token

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")

    user_id = decode_session_token(credentials.credentials)
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired session")

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    if user.is_guest and (user.guest_expires_at is None or user.guest_expires_at <= datetime.now(timezone.utc)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This guest session has expired")
    return user


async def get_valid_spotify_token(user: User, db: Session) -> str:
    """Returns a usable Spotify access token for this user, refreshing it first if it's expired
    or about to expire."""
    if user.is_guest:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Create an account before using Spotify features")
    connection = db.execute(select(MusicConnection).where(
        MusicConnection.user_id == user.id, MusicConnection.provider == "spotify"
    )).scalar_one_or_none()
    if connection is None or not connection.refresh_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Connect Spotify to use playlist import or export")
    if connection.token_expires_at and connection.token_expires_at > datetime.now(timezone.utc) and connection.access_token:
        return connection.access_token

    tokens = await refresh_access_token(connection.refresh_token)
    connection.access_token = tokens.access_token
    connection.refresh_token = tokens.refresh_token
    connection.token_expires_at = tokens.expires_at
    user.spotify_access_token = tokens.access_token  # legacy rollback mirror
    user.spotify_refresh_token = tokens.refresh_token
    user.spotify_token_expires_at = tokens.expires_at
    db.commit()
    return user.spotify_access_token
