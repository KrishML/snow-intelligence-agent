"""Cache-only dashboard queries; nothing here calls an external provider."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.qualitative_signal import QualitativeSignal
from app.db.models.resort import Resort
from app.db.models.snow_score import SnowScore
from app.db.models.webcam_snapshot import WebcamSnapshot
from app.db.models.weekly_run import WeeklyRun
from app.prefs.service import Preferences
from app.settings import settings

TOP_N = 10
# A "running" record older than this is treated as a crashed run, not a live one.
STALE_RUN_AFTER = timedelta(minutes=45)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _run_summary(run: WeeklyRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "status": run.status,
        "mode": run.mode,
        "run_date": run.run_date.isoformat(),
        "started_at": _iso(run.started_at),
        "completed_at": _iso(run.completed_at),
    }


async def _latest_runs(
    session: AsyncSession, mode: str
) -> tuple[WeeklyRun | None, WeeklyRun | None]:
    """Return (latest completed run, latest attempt of any status) for a mode."""
    order = (WeeklyRun.run_date.desc(), WeeklyRun.started_at.desc())
    complete = (
        await session.execute(
            select(WeeklyRun)
            .where(WeeklyRun.mode == mode, WeeklyRun.status == "complete")
            .order_by(*order)
            .limit(1)
        )
    ).scalars().first()
    attempt = (
        await session.execute(
            select(WeeklyRun).where(WeeklyRun.mode == mode).order_by(*order).limit(1)
        )
    ).scalars().first()
    return complete, attempt


def _first_per_resort(rows, attribute: str = "resort_id") -> dict[uuid.UUID, Any]:
    first: dict[uuid.UUID, Any] = {}
    for row in rows:
        first.setdefault(getattr(row, attribute), row)
    return first


async def weekly_dashboard(
    session: AsyncSession, prefs: Preferences
) -> dict[str, Any]:
    complete, attempt = await _latest_runs(session, prefs.mode)
    base: dict[str, Any] = {
        "mode": prefs.mode,
        "residence_city": prefs.residence_city,
        "origin_airports": prefs.preferred_airports,
        "budget_total_gbp": (
            str(prefs.budget_total_gbp) if prefs.budget_total_gbp is not None else None
        ),
        "run": _run_summary(complete) if complete else None,
        "last_updated": _iso(complete.completed_at) if complete else None,
        "latest_attempt": None,
        "partial_failure": False,
        "errors": {},
        "resorts": [],
    }
    # Surface a newer failed or in-progress attempt instead of hiding it behind old data.
    if attempt is not None and (complete is None or attempt.id != complete.id):
        base["latest_attempt"] = _run_summary(attempt)
    if complete is None:
        return base

    errors = complete.error_log if isinstance(complete.error_log, dict) else {}
    base["errors"] = {str(key): value for key, value in errors.items()}
    base["partial_failure"] = bool(errors)

    rows = (
        await session.execute(
            select(SnowScore, Resort)
            .join(Resort, Resort.id == SnowScore.resort_id)
            .where(
                SnowScore.weekly_run_id == complete.id,
                SnowScore.passed_gate.is_(True),
                SnowScore.rank.is_not(None),
            )
            .order_by(SnowScore.rank.asc())
            .limit(TOP_N)
        )
    ).all()
    resort_ids = [resort.id for _, resort in rows]
    if not resort_ids:
        return base

    signals = _first_per_resort(
        (
            await session.execute(
                select(QualitativeSignal)
                .where(
                    QualitativeSignal.weekly_run_id == complete.id,
                    QualitativeSignal.resort_id.in_(resort_ids),
                )
                .order_by(QualitativeSignal.created_at.desc())
            )
        ).scalars()
    )
    webcams = _first_per_resort(
        (
            await session.execute(
                select(WebcamSnapshot)
                .where(
                    WebcamSnapshot.weekly_run_id == complete.id,
                    WebcamSnapshot.resort_id.in_(resort_ids),
                )
                .order_by(WebcamSnapshot.captured_at.desc())
            )
        ).scalars()
    )

    for score, resort in rows:
        signal = signals.get(resort.id)
        webcam = webcams.get(resort.id)
        base["resorts"].append(
            {
                "rank": score.rank,
                "resort_id": str(resort.id),
                "name": resort.name,
                "country": resort.country,
                "region": resort.region,
                "altitude_base_m": resort.altitude_base_m,
                "nearest_airports": list(resort.nearest_airports),
                "depth_cm": score.depth_cm,
                "temperature_c": score.temperature_c,
                "forecast_stable": score.forecast_stable,
                "historical_reliability_pct": score.historical_reliability_pct,
                "score": {
                    "total": score.score_total,
                    "depth": score.score_depth,
                    "forecast": score.score_forecast,
                    "historical": score.score_historical,
                    "qualitative": score.score_qualitative,
                },
                "qualitative": (
                    {
                        "summary": signal.summary,
                        "sentiment": signal.sentiment,
                        "negative_keywords": list(signal.negative_keywords or []),
                    }
                    if signal
                    else None
                ),
                "webcam": (
                    {
                        "classification": webcam.classification,
                        "description": webcam.description,
                        "captured_at": _iso(webcam.captured_at),
                        "image_url": (
                            f"/api/dashboard/webcams/{webcam.id}"
                            if webcam.file_path
                            else None
                        ),
                    }
                    if webcam
                    else None
                ),
            }
        )
    return base


async def list_resorts(session: AsyncSession, mode: str | None) -> list[dict[str, Any]]:
    query = select(Resort).order_by(Resort.country, Resort.name)
    if mode in ("sports", "tourism"):
        query = query.where(Resort.mode.in_([mode, "both"]))
    resorts = (await session.execute(query)).scalars().all()
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "country": r.country,
            "region": r.region,
            "mode": r.mode,
            "altitude_base_m": r.altitude_base_m,
            "altitude_peak_m": r.altitude_peak_m,
            "piste_count": r.piste_count,
            "lift_count": r.lift_count,
            "nearest_airports": list(r.nearest_airports),
            "is_active": r.is_active,
            "has_webcam": bool(r.webcam_url),
        }
        for r in resorts
    ]


async def running_run(
    session: AsyncSession, mode: str
) -> WeeklyRun | None:
    """Return a recent in-progress run for the mode, ignoring stale records."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - STALE_RUN_AFTER
    return (
        await session.execute(
            select(WeeklyRun)
            .where(
                WeeklyRun.mode == mode,
                WeeklyRun.status == "running",
                WeeklyRun.started_at >= cutoff,
            )
            .limit(1)
        )
    ).scalars().first()


async def get_webcam_snapshot(
    session: AsyncSession, snapshot_id: uuid.UUID
) -> WebcamSnapshot | None:
    return await session.get(WebcamSnapshot, snapshot_id)


def estimated_completion() -> datetime:
    return datetime.now(timezone.utc) + timedelta(
        minutes=settings.weekly_run_estimated_minutes
    )
