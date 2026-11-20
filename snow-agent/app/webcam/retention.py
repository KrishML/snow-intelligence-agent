"""Delete webcam snapshots (rows and stored images) past the retention window."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.webcam_snapshot import WebcamSnapshot
from app.db.session import AsyncSessionLocal
from app.settings import settings
from app.storage import SnapshotStorage

logger = logging.getLogger(__name__)


async def prune_snapshots(
    session_factory: Callable[[], AsyncSession] | None = None,
    storage: SnapshotStorage | None = None,
    now: datetime | None = None,
) -> int:
    """Remove snapshots older than the retention window; return how many rows were deleted."""
    session_factory = session_factory or AsyncSessionLocal
    storage = storage or SnapshotStorage()
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=settings.webcam_retention_days)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(WebcamSnapshot.id, WebcamSnapshot.file_path).where(
                    WebcamSnapshot.captured_at < cutoff
                )
            )
        ).all()
        if not rows:
            return 0
        deleted_ids = []
        for snapshot_id, reference in rows:
            if reference:
                try:
                    await storage.delete(reference)
                except Exception:
                    # Keep the row so the next run retries instead of orphaning the file.
                    logger.exception("Could not delete snapshot file %s", reference)
                    continue
            deleted_ids.append(snapshot_id)
        if deleted_ids:
            await session.execute(
                delete(WebcamSnapshot).where(WebcamSnapshot.id.in_(deleted_ids))
            )
            await session.commit()
    logger.info("Pruned %d webcam snapshots older than %s", len(deleted_ids), cutoff)
    return len(deleted_ids)
