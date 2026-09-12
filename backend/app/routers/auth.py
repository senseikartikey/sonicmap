import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.dependencies import get_current_user
from app.models import AuthChallenge, AuthIdentity, MusicConnection, User
from app.schemas import CurrentUserOut, MagicLinkRequest
from app.services import provider_auth, spotify_auth
from app.services.jwt_auth import create_session_token

router = APIRouter(prefix="/auth", tags=["auth"])


def _callback(user: User) -> RedirectResponse:
    return RedirectResponse(f"{settings.frontend_base_url}/auth/callback#token={create_session_token(user.id)}")


def _save_spotify(db: Session, user: User, profile, tokens) -> None:
    connection = db.execute(select(MusicConnection).where(MusicConnection.user_id == user.id, MusicConnection.provider == "spotify")).scalar_one_or_none()
    if connection is None:
        connection = MusicConnection(user_id=user.id, provider="spotify"); db.add(connection)
    connection.provider_user_id = profile.spotify_id; connection.access_token = tokens.access_token
    connection.refresh_token = tokens.refresh_token; connection.token_expires_at = tokens.expires_at
    connection.scopes = spotify_auth.SCOPES; connection.updated_at = datetime.now(timezone.utc)
    user.spotify_id = user.spotify_id or profile.spotify_id
    user.spotify_access_token = tokens.access_token; user.spotify_refresh_token = tokens.refresh_token
    user.spotify_token_expires_at = tokens.expires_at
    db.commit()


@router.get("/spotify/login")
def spotify_login(db: Session = Depends(get_db)) -> RedirectResponse:
    if not settings.spotify_client_id: raise HTTPException(503, "Spotify sign-in is not configured")
    return RedirectResponse(spotify_auth.build_authorize_url(provider_auth.create_challenge(db, "spotify_login")))


@router.post("/spotify/connect")
def spotify_connect(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.is_guest: raise HTTPException(403, "Create an account before connecting Spotify")
    state = provider_auth.create_challenge(db, "spotify_connect", user_id=user.id)
    return {"url": spotify_auth.build_authorize_url(state)}


@router.get("/spotify/callback")
async def spotify_callback(code: str, state: str, db: Session = Depends(get_db)) -> RedirectResponse:
    challenge = provider_auth.consume_challenge(db, state, "spotify_connect")
    connecting = challenge is not None
    challenge = challenge or provider_auth.consume_challenge(db, state, "spotify_login")
    if challenge is None: raise HTTPException(400, "Invalid or expired OAuth state")
    tokens = await spotify_auth.exchange_code(code); profile = await spotify_auth.fetch_profile(tokens.access_token)
    if connecting and challenge.user_id:
        user = db.get(User, challenge.user_id)
        if user is None: raise HTTPException(404, "Account no longer exists")
        existing = db.execute(select(AuthIdentity).where(AuthIdentity.provider == "spotify", AuthIdentity.provider_user_id == profile.spotify_id)).scalar_one_or_none()
        if existing and existing.user_id != user.id: raise HTTPException(409, "That Spotify account is connected elsewhere")
        if not existing: db.add(AuthIdentity(user_id=user.id, provider="spotify", provider_user_id=profile.spotify_id, provider_email=profile.email, email_verified=True))
    else:
        user = provider_auth.resolve_identity(db, "spotify", profile.spotify_id, profile.email, True, profile.display_name)
    _save_spotify(db, user, profile, tokens)
    return _callback(user)


@router.get("/google/login")
def google_login(db: Session = Depends(get_db)) -> RedirectResponse:
    if not settings.google_client_id or not settings.google_client_secret: raise HTTPException(503, "Google sign-in is not configured")
    return RedirectResponse(provider_auth.google_authorize_url(provider_auth.create_challenge(db, "google_login")))


@router.get("/google/callback")
async def google_callback(code: str, state: str, db: Session = Depends(get_db)) -> RedirectResponse:
    if provider_auth.consume_challenge(db, state, "google_login") is None: raise HTTPException(400, "Invalid or expired OAuth state")
    profile = await provider_auth.google_profile(code)
    return _callback(provider_auth.resolve_identity(db, "google", profile["sub"], profile.get("email"), bool(profile.get("email_verified")), profile.get("name")))


@router.post("/email/request", status_code=202)
async def request_magic_link(payload: MagicLinkRequest, db: Session = Depends(get_db)):
    if not settings.magic_link_enabled:
        raise HTTPException(404, "Not found")
    message = "If that address can receive email, a sign-in link is on its way."
    try: email = provider_auth.normalize_email(payload.email)
    except ValueError: return {"message": message}
    recent = db.execute(select(func.count()).select_from(AuthChallenge).where(AuthChallenge.kind == "email_magic", AuthChallenge.email == email, AuthChallenge.created_at >= datetime.now(timezone.utc) - timedelta(hours=1))).scalar_one()
    if recent < 5:
        await asyncio.to_thread(provider_auth.send_magic_link, email, provider_auth.create_challenge(db, "email_magic", email=email))
    return {"message": message}


@router.get("/email/callback")
def email_callback(token: str = Query(min_length=20, max_length=200), db: Session = Depends(get_db)):
    if not settings.magic_link_enabled:
        raise HTTPException(404, "Not found")
    challenge = provider_auth.consume_challenge(db, token, "email_magic")
    if challenge is None or not challenge.email: raise HTTPException(400, "This sign-in link is invalid or expired")
    return _callback(provider_auth.resolve_identity(db, "email", challenge.email, challenge.email, True, None))


@router.get("/me", response_model=CurrentUserOut)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> CurrentUserOut:
    providers = list(db.execute(select(AuthIdentity.provider).where(AuthIdentity.user_id == user.id)).scalars())
    connected = db.execute(select(MusicConnection.id).where(MusicConnection.user_id == user.id, MusicConnection.provider == "spotify")).scalar_one_or_none() is not None
    return CurrentUserOut(id=user.id, spotify_id=user.spotify_id, display_name=user.display_name, email=user.email, auth_providers=providers, spotify_connected=connected, is_guest=user.is_guest)


@router.post("/guest", status_code=201)
def create_guest(db: Session = Depends(get_db)):
    expires = datetime.now(timezone.utc) + timedelta(hours=12)
    user = User(display_name="Guest listener", is_guest=True, guest_expires_at=expires)
    db.add(user); db.commit(); db.refresh(user)
    return {"token": create_session_token(user.id, timedelta(hours=12)), "expires_at": expires.isoformat()}
