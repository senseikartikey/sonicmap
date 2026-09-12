from __future__ import annotations

import hashlib
import logging
import secrets
import smtplib
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urlencode, urlsplit

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AuthChallenge, AuthIdentity, User

logger = logging.getLogger(__name__)


def normalize_email(value: str) -> str:
    email = value.strip().casefold()
    if len(email) > 320 or "@" not in email or email.startswith("@") or email.endswith("@"):
        raise ValueError("Enter a valid email address")
    return email


def create_challenge(db: Session, kind: str, *, email: str | None = None, user_id: uuid.UUID | None = None, minutes: int = 10) -> str:
    raw = secrets.token_urlsafe(32)
    db.add(AuthChallenge(kind=kind, token_hash=hashlib.sha256(raw.encode()).hexdigest(), email=email, user_id=user_id,
                         expires_at=datetime.now(timezone.utc) + timedelta(minutes=minutes)))
    db.commit()
    return raw


def consume_challenge(db: Session, raw: str, kind: str) -> AuthChallenge | None:
    challenge = db.execute(select(AuthChallenge).where(
        AuthChallenge.token_hash == hashlib.sha256(raw.encode()).hexdigest(), AuthChallenge.kind == kind
    ).with_for_update()).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if challenge is None or challenge.used_at is not None or challenge.expires_at <= now:
        return None
    challenge.used_at = now
    db.commit()
    return challenge


def resolve_identity(db: Session, provider: str, provider_user_id: str, email: str | None,
                     email_verified: bool, display_name: str | None) -> User:
    identity = db.execute(select(AuthIdentity).where(
        AuthIdentity.provider == provider, AuthIdentity.provider_user_id == provider_user_id
    )).scalar_one_or_none()
    if identity:
        user = db.get(User, identity.user_id)
        assert user is not None
    else:
        normalized = normalize_email(email) if email and email_verified else None
        user = None
        if normalized:
            linked = db.execute(select(AuthIdentity).where(
                func.lower(AuthIdentity.provider_email) == normalized, AuthIdentity.email_verified.is_(True)
            ).limit(1)).scalar_one_or_none()
            user = db.get(User, linked.user_id) if linked else db.execute(
                select(User).where(func.lower(User.email) == normalized)
            ).scalar_one_or_none()
        if user is None:
            user = User(display_name=display_name, email=normalized)
            db.add(user); db.flush()
        db.add(AuthIdentity(user_id=user.id, provider=provider, provider_user_id=provider_user_id,
                            provider_email=normalized, email_verified=email_verified))
    if display_name and not user.display_name:
        user.display_name = display_name
    if email_verified and email and not user.email:
        user.email = normalize_email(email)
    db.commit(); db.refresh(user)
    return user


def google_authorize_url(state: str) -> str:
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": settings.google_client_id, "redirect_uri": settings.google_redirect_uri,
        "response_type": "code", "scope": "openid email profile", "state": state, "prompt": "select_account",
    })


async def google_profile(code: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        token = await client.post("https://oauth2.googleapis.com/token", data={
            "code": code, "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri, "grant_type": "authorization_code",
        })
        token.raise_for_status()
        profile = await client.get("https://openidconnect.googleapis.com/v1/userinfo",
                                   headers={"Authorization": f"Bearer {token.json()['access_token']}"})
        profile.raise_for_status()
        return profile.json()


def send_magic_link(email: str, raw: str) -> None:
    api = urlsplit(settings.spotify_redirect_uri)
    url = f"{api.scheme}://{api.netloc}/auth/email/callback?token={raw}"
    if not settings.smtp_host or not settings.smtp_from_email:
        logger.warning("Development magic link for %s: %s", email, url)
        return
    message = EmailMessage()
    message["Subject"] = "Sign in to Sonicmap"; message["From"] = settings.smtp_from_email; message["To"] = email
    message.set_content(f"Open this one-time link within 10 minutes:\n\n{url}\n\nIf you did not request it, ignore this email.")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_use_tls: smtp.starttls()
        if settings.smtp_username: smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)
