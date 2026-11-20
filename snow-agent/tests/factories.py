"""Small ORM object builders shared by tests that need a real (SQLite) database."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from app.db.models.resort import Resort
from app.db.models.snow_score import SnowScore
from app.db.models.weekly_run import WeeklyRun


def make_resort(name: str = "Kitzbuhel", mode: str = "sports", **kw) -> Resort:
    return Resort(
        id=uuid.uuid4(),
        name=name,
        country="Austria",
        region="Tyrol",
        latitude=47.45,
        longitude=12.39,
        altitude_base_m=800,
        mode=mode,
        nearest_airports=["INN", "SZG"],
        **kw,
    )


def make_run(mode: str = "sports", status: str = "complete") -> WeeklyRun:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return WeeklyRun(
        id=uuid.uuid4(),
        run_date=date.today(),
        mode=mode,
        status=status,
        started_at=now,
        completed_at=now if status == "complete" else None,
    )


def make_score(run: WeeklyRun, resort: Resort, passed: bool = True, rank: int = 1) -> SnowScore:
    return SnowScore(
        id=uuid.uuid4(),
        weekly_run_id=run.id,
        resort_id=resort.id,
        passed_gate=passed,
        depth_cm=80,
        score_total=80 if passed else None,
        rank=rank if passed else None,
    )
