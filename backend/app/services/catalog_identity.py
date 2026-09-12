import uuid
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import ExternalTrackRef
from app.services.resolved_track import ResolvedTrack


def record_external_ref(db: Session, song_id: uuid.UUID, resolved: ResolvedTrack, market: str = "") -> None:
    if not resolved.source or not resolved.source_track_id:
        return
    stmt = insert(ExternalTrackRef).values(
        id=uuid.uuid4(),
        song_id=song_id,
        provider=resolved.source,
        external_id=resolved.source_track_id,
        market=market,
        available=True,
        last_seen_at=datetime.now(timezone.utc),
    )
    db.execute(
        stmt.on_conflict_do_update(
            constraint="uq_external_track_ref",
            set_={"song_id": song_id, "available": True, "last_seen_at": datetime.now(timezone.utc)},
        )
    )
