from __future__ import annotations

import socket
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.dashboard import api as dashboard_api
from app.db.models.qualitative_signal import QualitativeSignal
from app.db.models.resort import Resort
from app.db.models.snow_score import SnowScore
from app.db.models.user_prefs import UserPrefs
from app.db.models.webcam_snapshot import WebcamSnapshot
from app.db.models.weekly_run import WeeklyRun
from app.prefs.service import PrefsService
from app.settings import settings
from tests.orm_sqlite import orm_sqlite

USER = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER = uuid.UUID("00000000-0000-0000-0000-000000000002")
HEADERS = {"Authorization": "Bearer test-token"}


@pytest_asyncio.fixture
async def factory():
    async with orm_sqlite(
        Resort, WeeklyRun, SnowScore, QualitativeSignal, WebcamSnapshot, UserPrefs
    ) as session_factory:
        yield session_factory


@pytest_asyncio.fixture
async def client(factory, monkeypatch):
    monkeypatch.setattr(settings, "api_bearer_token", "test-token")
    monkeypatch.setattr(dashboard_api, "AsyncSessionLocal", factory)
    monkeypatch.setattr(
        dashboard_api, "PrefsService", lambda: PrefsService(session_factory=factory)
    )
    app = FastAPI()
    app.include_router(dashboard_api.router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


def _resort(name: str, mode: str = "sports", **kw) -> Resort:
    return Resort(
        id=uuid.uuid4(),
        name=name,
        country="Austria",
        region="Tyrol",
        latitude=47.0,
        longitude=11.0,
        altitude_base_m=1500,
        mode=mode,
        nearest_airports=["INN"],
        webcam_url="https://example.test/cam" if mode == "sports" else None,
        **kw,
    )


def _run(mode="sports", status="complete", days_ago=0, error_log=None) -> WeeklyRun:
    started = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_ago)
    return WeeklyRun(
        id=uuid.uuid4(),
        run_date=date.today() - timedelta(days=days_ago),
        mode=mode,
        status=status,
        error_log=error_log,
        started_at=started,
        completed_at=started if status == "complete" else None,
    )


def _score(run, resort, rank, total, passed=True) -> SnowScore:
    return SnowScore(
        id=uuid.uuid4(),
        weekly_run_id=run.id,
        resort_id=resort.id,
        passed_gate=passed,
        depth_cm=80,
        temperature_c=-4.0,
        forecast_stable=True,
        historical_reliability_pct=70.0,
        score_depth=30,
        score_forecast=25,
        score_historical=17,
        score_qualitative=15,
        score_total=total,
        rank=rank if passed else None,
    )


async def _seed(factory, objects):
    async with factory() as session:
        session.add_all(objects)
        await session.commit()


async def test_endpoints_require_auth(client):
    for path in ("/api/dashboard/weekly", "/api/dashboard/resorts"):
        assert (await client.get(path)).status_code == 401
    assert (await client.post("/api/dashboard/refresh")).status_code == 401


async def test_weekly_empty_state_when_no_run(client):
    body = (await client.get("/api/dashboard/weekly", headers=HEADERS)).json()
    assert body["run"] is None
    assert body["resorts"] == []
    assert body["mode"] == "sports"
    assert body["residence_city"] == "London"


async def test_weekly_returns_ranked_top_ten_from_latest_complete_run(
    client, factory, monkeypatch
):
    def _no_network(*args, **kwargs):
        raise AssertionError("dashboard reads must not open network connections")

    resorts = [_resort(f"Resort {i}") for i in range(12)]
    old_run, run = _run(days_ago=7), _run()
    objects = [*resorts, old_run, run]
    objects += [_score(run, r, rank=i + 1, total=90 - i) for i, r in enumerate(resorts)]
    objects += [_score(old_run, resorts[0], rank=1, total=10)]
    failed = _resort("Failed Gate")
    objects += [failed, _score(run, failed, rank=None, total=0, passed=False)]
    objects.append(
        QualitativeSignal(
            id=uuid.uuid4(),
            weekly_run_id=run.id,
            resort_id=resorts[0].id,
            source_type="reddit",
            summary="Great powder",
            sentiment="positive",
            negative_keywords=["icy"],
            score=20,
        )
    )
    snapshot = WebcamSnapshot(
        id=uuid.uuid4(),
        weekly_run_id=run.id,
        resort_id=resorts[0].id,
        captured_at=datetime.now(timezone.utc),
        classification="snow_good",
        description="Snowy",
        file_path="snapshots/x.png",
    )
    objects.append(snapshot)
    await _seed(factory, objects)

    monkeypatch.setattr(socket.socket, "connect", _no_network)
    response = await client.get("/api/dashboard/weekly", headers=HEADERS)

    body = response.json()
    assert response.status_code == 200
    assert body["run"]["id"] == str(run.id)
    assert len(body["resorts"]) == 10
    assert [r["rank"] for r in body["resorts"]] == list(range(1, 11))
    top = body["resorts"][0]
    assert top["name"] == "Resort 0"
    assert top["score"]["total"] == 90
    assert top["qualitative"]["summary"] == "Great powder"
    assert top["qualitative"]["negative_keywords"] == ["icy"]
    assert top["webcam"]["classification"] == "snow_good"
    assert top["webcam"]["image_url"] == f"/api/dashboard/webcams/{snapshot.id}"
    assert "file_path" not in str(top["webcam"])
    assert body["resorts"][1]["webcam"] is None
    assert body["partial_failure"] is False
    assert body["latest_attempt"] is None


async def test_weekly_reports_partial_failure_and_newer_failed_attempt(client, factory):
    resort = _resort("A")
    run = _run(days_ago=1, error_log={"reddit:x": "rate limited"})
    failed = _run(status="failed")
    await _seed(factory, [resort, run, failed, _score(run, resort, 1, 80)])

    body = (await client.get("/api/dashboard/weekly", headers=HEADERS)).json()

    assert body["run"]["id"] == str(run.id)
    assert body["partial_failure"] is True
    assert body["errors"] == {"reddit:x": "rate limited"}
    assert body["latest_attempt"]["id"] == str(failed.id)
    assert body["latest_attempt"]["status"] == "failed"


async def test_weekly_is_mode_specific(client, factory):
    sports, tourism = _resort("Ski"), _resort("Village", mode="tourism")
    sports_run, tourism_run = _run("sports"), _run("tourism")
    await _seed(
        factory,
        [
            sports,
            tourism,
            sports_run,
            tourism_run,
            _score(sports_run, sports, 1, 80),
            _score(tourism_run, tourism, 1, 70),
        ],
    )
    names = lambda body: [r["name"] for r in body["resorts"]]  # noqa: E731

    assert names((await client.get("/api/dashboard/weekly", headers=HEADERS)).json()) == ["Ski"]
    await PrefsService(factory).update(USER, {"mode": "tourism"})
    assert names((await client.get("/api/dashboard/weekly", headers=HEADERS)).json()) == ["Village"]


async def test_resorts_list_filters_by_mode(client, factory):
    await _seed(
        factory,
        [_resort("Ski"), _resort("Village", mode="tourism"), _resort("Both", mode="both")],
    )
    everything = (await client.get("/api/dashboard/resorts", headers=HEADERS)).json()
    tourism = (
        await client.get("/api/dashboard/resorts?mode=tourism", headers=HEADERS)
    ).json()

    assert len(everything) == 3
    assert {r["name"] for r in tourism} == {"Village", "Both"}
    assert everything[0]["has_webcam"] in (True, False)
    assert (
        await client.get("/api/dashboard/resorts?mode=bogus", headers=HEADERS)
    ).status_code == 422


async def test_refresh_requires_admin(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_user_ids", [OTHER])
    response = await client.post("/api/dashboard/refresh", headers=HEADERS)
    assert response.status_code == 403


async def test_refresh_creates_run_and_rejects_duplicate(client, factory, monkeypatch):
    started = []

    async def fake_execute(mode, run_id):
        started.append((mode, run_id))

    monkeypatch.setattr(dashboard_api, "_execute_refresh", fake_execute)
    monkeypatch.setattr("app.runner.weekly_run.AsyncSessionLocal", factory)

    first = await client.post("/api/dashboard/refresh", headers=HEADERS)
    assert first.status_code == 202
    body = first.json()
    assert body["mode"] == "sports"
    assert datetime.fromisoformat(body["estimated_completion_at"]) > datetime.now(timezone.utc)
    assert started == [("sports", uuid.UUID(body["run_id"]))]

    async with factory() as session:
        run = await session.get(WeeklyRun, uuid.UUID(body["run_id"]))
    assert run.status == "running"

    second = await client.post("/api/dashboard/refresh", headers=HEADERS)
    assert second.status_code == 409
    assert len(started) == 1


async def test_refresh_ignores_stale_running_record(client, factory, monkeypatch):
    async def fake_execute(mode, run_id):
        return None

    monkeypatch.setattr(dashboard_api, "_execute_refresh", fake_execute)
    monkeypatch.setattr("app.runner.weekly_run.AsyncSessionLocal", factory)
    stale = _run(status="running")
    stale.started_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)
    await _seed(factory, [stale])

    assert (await client.post("/api/dashboard/refresh", headers=HEADERS)).status_code == 202


async def test_webcam_image_is_confined_to_snapshot_dir(
    client, factory, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "webcam_snapshot_dir", str(tmp_path))
    resort, run = _resort("A"), _run()
    inside = tmp_path / "ok.png"
    inside.write_bytes(b"\x89PNG")
    outside = tmp_path.parent / "secret.png"
    outside.write_bytes(b"secret")
    good = WebcamSnapshot(
        id=uuid.uuid4(), weekly_run_id=run.id, resort_id=resort.id,
        captured_at=datetime.now(timezone.utc), classification="snow_good",
        file_path=str(inside),
    )
    bad = WebcamSnapshot(
        id=uuid.uuid4(), weekly_run_id=run.id, resort_id=resort.id,
        captured_at=datetime.now(timezone.utc), classification="snow_good",
        file_path=str(outside),
    )
    await _seed(factory, [resort, run, good, bad])

    ok = await client.get(f"/api/dashboard/webcams/{good.id}", headers=HEADERS)
    assert ok.status_code == 200 and ok.content == b"\x89PNG"
    assert (
        await client.get(f"/api/dashboard/webcams/{bad.id}", headers=HEADERS)
    ).status_code == 404
    assert (
        await client.get(f"/api/dashboard/webcams/{uuid.uuid4()}", headers=HEADERS)
    ).status_code == 404
