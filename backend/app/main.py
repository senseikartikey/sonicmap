import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.db import SessionLocal
from app.routers import auth, catalog, ingest, map, mashup, product, recommend, stems
from app.services.brain import reset_stuck_rebuilds, warm_up_clustering
from app.services.security import SecurityMiddleware

logger = logging.getLogger("sonicmap")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_security()
    logger.info("Warming up UMAP/HDBSCAN JIT compilation...")
    await asyncio.to_thread(warm_up_clustering)
    logger.info("Clustering warmup complete — map rebuilds are fast from the first real request.")

    db = SessionLocal()
    try:
        reset_count = reset_stuck_rebuilds(db)
        if reset_count:
            logger.info("Reset %d map_rebuild_status row(s) stuck 'processing' from before this restart.", reset_count)
    finally:
        db.close()

    yield


production = settings.app_environment.casefold() in {"production", "prod"}
app = FastAPI(
    title="Sonicmap API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if production else "/docs",
    redoc_url=None if production else "/redoc",
    openapi_url=None if production else "/openapi.json",
)


class CatchUnhandledExceptionsMiddleware(BaseHTTPMiddleware):
    """@app.exception_handler(Exception) does NOT fix this, despite looking like the obvious
    tool for the job — Starlette's own Starlette.__init__ wires any handler registered for the
    bare Exception class into ServerErrorMiddleware specifically, which is unconditionally the
    outermost layer of the whole ASGI stack, *outside* CORSMiddleware, regardless of what order
    things get added in. A 500 built there never passes back through CORSMiddleware's header
    injection, so the browser can't read the response body or status at all and reports a
    generic network failure (frontend/src/lib/api.ts's "Can't reach the server" message)
    instead of the real error — for every unhandled exception in the app, not just this one.

    The actual fix has to catch the exception somewhere that sits *inside* CORSMiddleware, so
    the JSONResponse this returns is a normal return value CORSMiddleware sees and attaches
    headers to, same as any other response. Starlette's add_middleware() inserts each new
    middleware at the *front* of the list, and the stack gets built by wrapping in reverse —
    net effect: whichever middleware is added first ends up outermost. So this has to be
    registered *before* CORSMiddleware below, not after (registering it after was tried first
    and empirically verified — via a live HTTP request, not just reasoning about it — to still
    produce a response with no CORS header)."""

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception:
            logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
            return JSONResponse(
                status_code=500,
                content={"detail": "Something went wrong on our end. Try again in a moment."},
            )


app.add_middleware(CatchUnhandledExceptionsMiddleware)
app.add_middleware(SecurityMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(ingest.router)
app.include_router(map.router)
app.include_router(recommend.router)
app.include_router(catalog.router)
app.include_router(product.router)
app.include_router(stems.router)
app.include_router(mashup.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
