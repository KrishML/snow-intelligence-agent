"""Dashboard routes: cached weekly results, resort list, and manual refresh."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import Response

from app.auth import require_admin, require_user
from app.dashboard import service
from app.db.session import AsyncSessionLocal
from app.prefs.service import PrefsService
from app.runner.weekly_run import WeeklyRunOrchestrator
from app.storage import SnapshotStorage

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/dashboard/weekly")
async def dashboard_weekly(
    user_id: Annotated[uuid.UUID, Depends(require_user)],
) -> dict[str, Any]:
    prefs = await PrefsService().get(user_id)
    async with AsyncSessionLocal() as session:
        return await service.weekly_dashboard(session, prefs)


@router.get("/api/dashboard/resorts")
async def dashboard_resorts(
    user_id: Annotated[uuid.UUID, Depends(require_user)],
    mode: Annotated[Literal["sports", "tourism", "all"], Query()] = "all",
) -> list[dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        return await service.list_resorts(session, None if mode == "all" else mode)


@router.get("/api/dashboard/webcams/{snapshot_id}")
async def dashboard_webcam_image(
    snapshot_id: uuid.UUID,
    user_id: Annotated[uuid.UUID, Depends(require_user)],
) -> FileResponse:
    async with AsyncSessionLocal() as session:
        snapshot = await service.get_webcam_snapshot(session, snapshot_id)
    if snapshot is None or not snapshot.file_path:
        raise HTTPException(status_code=404, detail="Webcam image not found.")
    image = await SnapshotStorage().read(snapshot.file_path)
    if image is None:
        raise HTTPException(status_code=404, detail="Webcam image not found.")
    return Response(content=image, media_type="image/png")


async def _execute_refresh(mode: Literal["sports", "tourism"], run_id: uuid.UUID) -> None:
    try:
        await WeeklyRunOrchestrator().run(mode, run_id=run_id)
    except Exception:
        # The orchestrator has already marked the run failed and sent notifications.
        logger.exception("Manual refresh run %s failed", run_id)


@router.post("/api/dashboard/refresh", status_code=202)
async def dashboard_refresh(
    background_tasks: BackgroundTasks,
    user_id: Annotated[uuid.UUID, Depends(require_admin)],
) -> dict[str, Any]:
    prefs = await PrefsService().get(user_id)
    async with AsyncSessionLocal() as session:
        active = await service.running_run(session, prefs.mode)
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A {prefs.mode} run is already in progress (run {active.id}).",
        )
    run_id = await WeeklyRunOrchestrator().create_run(prefs.mode)
    background_tasks.add_task(_execute_refresh, prefs.mode, run_id)
    return {
        "run_id": str(run_id),
        "mode": prefs.mode,
        "estimated_completion_at": service.estimated_completion().isoformat(),
    }
