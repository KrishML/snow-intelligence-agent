from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio

from app.clients.official_report import OfficialReportClient, extract_report_text, load_sources
from app.clients.rss import RSSClient
from app.db.models.resort import Resort
from app.db.models.webcam_snapshot import WebcamSnapshot
from app.db.models.weekly_run import WeeklyRun
from app.scoring.qualitative import _source_type
from app.settings import settings
from app.storage import SnapshotStorage
from app.webcam.classifier import WebcamClassifier
from app.webcam.evaluation import evaluate, load_labels
from app.webcam.retention import prune_snapshots
from scripts.seed_resorts import load_webcam_urls
from tests.factories import make_resort, make_run
from tests.orm_sqlite import orm_sqlite


class FakeS3:
    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.deleted: list[tuple[str, str]] = []

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        return {"Body": SimpleNamespace(read=lambda: self.objects[(Bucket, Key)])}

    def delete_object(self, Bucket, Key):
        self.deleted.append((Bucket, Key))


@pytest.fixture
def s3_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "webcam_storage_backend", "s3")
    monkeypatch.setattr(settings, "webcam_s3_bucket", "snow-bucket")
    monkeypatch.setattr(settings, "webcam_s3_prefix", "webcams")
    monkeypatch.setattr(settings, "webcam_snapshot_dir", str(tmp_path))


# --- storage ---------------------------------------------------------------------------


async def test_local_storage_keeps_path_and_reads_only_inside_root(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "webcam_snapshot_dir", str(tmp_path))
    inside = tmp_path / "a.png"
    inside.write_bytes(b"img")
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(b"nope")
    storage = SnapshotStorage()

    assert await storage.persist(inside, "k.png") == str(inside)
    assert await storage.read(str(inside)) == b"img"
    assert await storage.read(str(outside)) is None
    assert await storage.read(str(tmp_path / "missing.png")) is None
    await storage.delete(str(outside))
    assert outside.exists()
    await storage.delete(str(inside))
    assert not inside.exists()


async def test_s3_storage_round_trip_removes_local_scratch(s3_settings, tmp_path):
    s3 = FakeS3()
    storage = SnapshotStorage(lambda: s3)
    scratch = tmp_path / "scratch.png"
    scratch.write_bytes(b"png-bytes")

    reference = await storage.persist(scratch, "run1/resort1.png")

    assert reference == "s3://snow-bucket/webcams/run1/resort1.png"
    assert not scratch.exists()
    assert await storage.read(reference) == b"png-bytes"
    assert await storage.read("s3://other-bucket/webcams/run1/resort1.png") is None
    await storage.delete(reference)
    assert s3.deleted == [("snow-bucket", "webcams/run1/resort1.png")]


async def test_s3_requires_bucket(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "webcam_storage_backend", "s3")
    monkeypatch.setattr(settings, "webcam_s3_bucket", "")
    scratch = tmp_path / "x.png"
    scratch.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="WEBCAM_S3_BUCKET"):
        await SnapshotStorage(lambda: FakeS3()).persist(scratch, "k.png")


async def test_storage_failure_keeps_classification_and_reports_error(
    resort_factory, monkeypatch, tmp_path
):
    class FakePage:
        async def goto(self, url, **kw):
            return None

        async def screenshot(self, path, type):
            Path(path).write_bytes(b"png")

        async def close(self):
            return None

    class FakeBrowser:
        async def new_page(self):
            return FakePage()

        async def close(self):
            return None

    class FakePlaywright:
        chromium = SimpleNamespace(launch=AsyncMock(return_value=FakeBrowser()))

        async def stop(self):
            return None

    class Manager:
        async def start(self):
            return FakePlaywright()

    monkeypatch.setattr("app.webcam.classifier.async_playwright", Manager)
    monkeypatch.setattr(settings, "webcam_snapshot_dir", str(tmp_path))
    vision = SimpleNamespace(
        complete_vision=AsyncMock(return_value={"classification": "snow_good", "description": "Snowy"})
    )
    storage = SimpleNamespace(persist=AsyncMock(side_effect=RuntimeError("S3 down")))

    async with WebcamClassifier(openai_client=vision, storage=storage) as classifier:
        result = await classifier.classify(
            resort_factory.build(webcam_url="https://example.test/cam"), uuid.uuid4()
        )

    assert result.classification == "snow_good"
    assert result.file_path is None
    assert "S3 down" in result.error


# --- retention -------------------------------------------------------------------------


@pytest_asyncio.fixture
async def factory():
    async with orm_sqlite(Resort, WeeklyRun, WebcamSnapshot) as f:
        yield f


def _snapshot(run, resort, days_old, path):
    return WebcamSnapshot(
        id=uuid.uuid4(), weekly_run_id=run.id, resort_id=resort.id,
        captured_at=datetime.now(timezone.utc) - timedelta(days=days_old),
        classification="snow_good", file_path=path,
    )


async def test_retention_deletes_only_expired_rows_and_files(factory, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "webcam_snapshot_dir", str(tmp_path))
    old_file, new_file = tmp_path / "old.png", tmp_path / "new.png"
    old_file.write_bytes(b"1")
    new_file.write_bytes(b"2")
    resort, run = make_resort(), make_run()
    old = _snapshot(run, resort, 91, str(old_file))
    fresh = _snapshot(run, resort, 89, str(new_file))
    async with factory() as session:
        session.add_all([resort, run, old, fresh])
        await session.commit()

    assert await prune_snapshots(factory) == 1

    assert not old_file.exists() and new_file.exists()
    async with factory() as session:
        assert await session.get(WebcamSnapshot, old.id) is None
        assert await session.get(WebcamSnapshot, fresh.id) is not None


async def test_retention_keeps_row_when_file_delete_fails(factory):
    resort, run = make_resort(), make_run()
    snap = _snapshot(run, resort, 100, "/tmp/x.png")
    async with factory() as session:
        session.add_all([resort, run, snap])
        await session.commit()
    storage = SimpleNamespace(delete=AsyncMock(side_effect=OSError("locked")))

    assert await prune_snapshots(factory, storage) == 0
    async with factory() as session:
        assert await session.get(WebcamSnapshot, snap.id) is not None


# --- official reports and travel feeds -------------------------------------------------


def test_extract_report_text_drops_noise_and_truncates():
    html = (
        "<html><head><style>x{}</style></head><body><nav>Menu</nav>"
        "<script>evil()</script><main><h1>Snow report</h1><p>80 cm   base</p></main>"
        f"<footer>Legal</footer><p>{'z' * 3000}</p></body></html>"
    )
    text = extract_report_text(html)
    assert text.startswith("Snow report 80 cm base")
    assert "Menu" not in text and "evil" not in text and "Legal" not in text
    assert len(text) == 1500


def test_load_sources_rejects_non_http_urls(tmp_path):
    file = tmp_path / "s.json"
    file.write_text(json.dumps({"A": "https://a.test", "B": "file:///etc/passwd", "C": 5}))
    assert load_sources(file) == {"A": "https://a.test"}
    assert load_sources(tmp_path / "missing.json") == {}


async def test_official_report_client_fetch_and_error(monkeypatch):
    def handler(request):
        if "bad" in request.url.host:
            return httpx.Response(500)
        return httpx.Response(200, text="<p>Fresh powder</p>")

    real = httpx.AsyncClient
    monkeypatch.setattr(
        "app.clients.official_report.httpx.AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    client = OfficialReportClient({"Good": "https://good.test/r", "Bad": "https://bad.test/r"})

    assert await client.fetch_report("Good") == "Fresh powder"
    assert client.last_error is None
    assert await client.fetch_report("Unconfigured") is None
    assert client.last_error is None
    assert await client.fetch_report("Bad") is None
    assert client.last_error


async def test_travel_feed_entries_filtered_by_resort_name(monkeypatch):
    feed = (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
        "<item><title>Hallstatt in winter</title><description>Snowy lanes</description></item>"
        "<item><title>Other place</title><description>No match</description></item></channel></rss>"
    )
    real = httpx.AsyncClient
    monkeypatch.setattr(
        "app.clients.rss.httpx.AsyncClient",
        lambda **kw: real(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(500) if "down" in req.url.host else httpx.Response(200, text=feed)
            ),
            **kw,
        ),
    )
    rss = RSSClient()

    entries = await rss.fetch_travel_entries("Hallstatt", ["https://blog.test/feed", "https://down.test/feed"])
    assert len(entries) == 1 and "Snowy lanes" in entries[0]
    assert rss.last_error and "down.test" in rss.last_error


def test_source_type_priority():
    assert _source_type(["official", "rss"]) == "rss"
    assert _source_type(["official", "reddit"]) == "reddit"
    assert _source_type(["official"]) == "official"
    assert _source_type([]) == "rss"


# --- seed + evaluation -----------------------------------------------------------------


def test_webcam_urls_only_accept_http(tmp_path):
    file = tmp_path / "w.json"
    file.write_text(json.dumps({"A": "https://cam.test", "B": "javascript:alert(1)"}))
    assert load_webcam_urls(file) == {"A": "https://cam.test"}
    assert load_webcam_urls(tmp_path / "none.json") == {}


async def test_evaluation_reports_accuracy_and_mismatches(tmp_path):
    labels = {"a.png": "snow_good", "b.png": "no_snow", "c.png": "snow_poor", "d.png": "camera_offline"}
    answers = {"a.png": "snow_good", "b.png": "no_snow", "c.png": "snow_good", "d.png": "camera_offline"}

    async def classify(path):
        return answers[path.name]

    report = await evaluate(tmp_path, labels, classify)
    assert (report.total, report.correct, report.accuracy) == (4, 3, 0.75)
    assert report.mismatches == [("c.png", "snow_poor", "snow_good")]
    assert not report.meets_target

    answers["c.png"] = "snow_poor"
    assert (await evaluate(tmp_path, labels, classify)).meets_target


def test_load_labels_validates(tmp_path):
    good = tmp_path / "g.json"
    good.write_text(json.dumps({"a.png": "snow_good"}))
    assert load_labels(good) == {"a.png": "snow_good"}
    bad = tmp_path / "b.json"
    bad.write_text(json.dumps({"a.png": "sunny"}))
    with pytest.raises(ValueError):
        load_labels(bad)
