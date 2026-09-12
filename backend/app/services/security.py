from __future__ import annotations

import hashlib
import threading
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings


# Per-process guardrail. Production should additionally rate-limit at the CDN/API gateway so
# limits are shared by every replica and enforced before traffic consumes application workers.
_windows: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def _rule(request: Request) -> tuple[str, int, int] | None:
    path, method = request.url.path, request.method
    if method == "POST" and path == "/auth/guest": return "guest", 10, 3600
    if path in {"/auth/google/login", "/auth/spotify/login"}: return "oauth-start", 30, 3600
    if method == "POST" and path == "/auth/email/request": return "email", 10, 3600
    if method == "GET" and path == "/catalog/public-status": return "public-status", 60, 60
    if method == "POST" and path.startswith("/ingest/"): return "ingest", 60, 3600
    if method == "GET" and path == "/ingest/search/suggestions": return "suggest", 120, 60
    if method == "POST" and path == "/map/rebuild": return "rebuild", 30, 3600
    if method == "GET" and path == "/recommendations": return "recommend", 120, 60
    if method == "POST" and path == "/stems/uploads": return "stem-upload", 20, 3600
    if method == "POST" and path == "/stems/jobs": return "stem-job", 10, 86400
    if method == "POST" and path in {"/studio/discover", "/studio/journey/export"}: return "studio-heavy", 60, 3600
    return None


def _identity(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if authorization:
        return "token:" + hashlib.sha256(authorization.encode()).hexdigest()[:24]
    return "ip:" + (request.client.host if request.client else "unknown")


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rule = _rule(request) if settings.rate_limit_enabled else None
        if rule:
            group, maximum, seconds = rule
            now = time.monotonic()
            key = (group, _identity(request))
            with _lock:
                window = _windows[key]
                while window and window[0] <= now - seconds:
                    window.popleft()
                if len(window) >= maximum:
                    retry_after = max(1, int(seconds - (now - window[0])))
                    return JSONResponse(
                        {"detail": "Too many requests. Try again later."},
                        status_code=429,
                        headers={"Retry-After": str(retry_after)},
                    )
                window.append(now)

        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Resource-Policy"] = "same-site"
        if request.url.path.startswith("/auth/"):
            response.headers["Cache-Control"] = "no-store"
        if settings.app_environment.casefold() in {"production", "prod"}:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
