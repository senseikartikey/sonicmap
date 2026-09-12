import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.models import Song, User
from app.schemas import CatalogStatusOut
from app.services.catalog_jobs import catalog_counts
from app.services.itunes import resolve_track

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/public-status")
def public_catalog_status(response: Response, db: Session = Depends(get_db)) -> dict[str, int | bool]:
    """Non-sensitive landing-page proof that the recommendation pool is real and growing."""
    counts = catalog_counts(db)
    response.headers["Cache-Control"] = "public, max-age=10, stale-while-revalidate=20"
    return {
        "indexed": int(counts["metadata"]),
        "analyzed": int(counts["analyzed"]),
        "processing": int(counts["queued"]),
        "live": bool(counts["enrichment_active"]),
    }


@router.get("/status", response_model=CatalogStatusOut)
def get_catalog_status(
    _user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> CatalogStatusOut:
    counts = catalog_counts(db)
    # Analyzed count is a monotonic-enough local catalog generation without adding a separate
    # write-hot singleton row; clients only use inequality to know new candidates may exist.
    return CatalogStatusOut(**counts, version=int(counts["analyzed"]))


# Hosts whose preview links are signed and time-limited. Deezer's `cdnt-preview.dzcdn.net`
# URLs return 200 while fresh and 403 forever once the signature expires, so a stored link
# silently rots — measured at 2,042 songs, ~9% of the analysable catalog. Nothing detects it,
# because the URL is still present and well-formed; it just stops serving audio.
EXPIRING_PREVIEW_HOSTS = ("dzcdn.net",)


@router.post("/songs/{song_id}/refresh-preview")
async def refresh_preview(
    song_id: uuid.UUID,
    # Authenticated: this writes to the catalog and calls an external provider, so it must not
    # be an anonymous way to drive traffic at iTunes and Deezer on our behalf.
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Re-resolve a song's preview link and hand back one that works right now.

    Called by the player when a stored link fails, rather than on a schedule: re-resolving the
    whole catalog on a timer would burn the provider's rate limit refreshing links nobody is
    about to play, and any link it refreshed would be stale again before it was used. Fetching
    on demand is the only version of this that stays correct.
    """
    song = db.get(Song, song_id)
    if song is None:
        raise HTTPException(404, "That song is not in the catalog")

    resolved = await resolve_track(f"{song.title} {song.artist}")
    if resolved is None or not resolved.preview_url:
        raise HTTPException(
            404,
            f"No preview clip is available for \"{song.title}\" any more.",
        )
    song.preview_url = resolved.preview_url
    if resolved.duration_ms and not song.duration_ms:
        song.duration_ms = resolved.duration_ms
    db.commit()
    return {"preview_url": song.preview_url, "expiring": any(h in song.preview_url for h in EXPIRING_PREVIEW_HOSTS)}
