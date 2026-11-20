from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

from app.webcam.classifier import WebcamClassifier, apply_webcam_adjustment
from app.settings import settings


@pytest.mark.parametrize(
    ("base_score", "classification", "expected"),
    [
        (18, "snow_good", 20),
        (4, "snow_poor", 2),
        (4, "no_snow", 0),
        (10, "camera_offline", 10),
    ],
)
def test_webcam_adjustment_examples(base_score, classification, expected):
    assert apply_webcam_adjustment(base_score, classification) == expected


@given(
    base_score=st.integers(min_value=0, max_value=20),
    classification=st.sampled_from(
        ["snow_good", "snow_poor", "no_snow", "camera_offline"]
    ),
)
@h_settings(max_examples=200, deadline=None)
def test_webcam_adjustment_is_bounded(base_score, classification):
    result = apply_webcam_adjustment(base_score, classification)
    assert 0 <= result <= 20


@pytest.mark.parametrize("classification", ["snow_good", "snow_poor", "no_snow"])
def test_webcam_adjustment_matches_formula(classification):
    for base_score in range(21):
        delta = {"snow_good": 3, "snow_poor": -2, "no_snow": -5}[classification]
        assert apply_webcam_adjustment(base_score, classification) == max(
            0, min(20, base_score + delta)
        )


async def test_null_webcam_url_returns_offline_without_browser(resort_factory):
    resort = resort_factory.build(webcam_url=None)
    async with WebcamClassifier() as classifier:
        result = await classifier.classify(resort, uuid.uuid4())

    assert result.classification == "camera_offline"
    assert result.file_path is None
    assert result.error is None


async def test_classify_captures_image_and_uses_vision(
    resort_factory, monkeypatch, tmp_path
):
    resort = resort_factory.build(webcam_url="https://example.test/camera")
    screenshot_path = tmp_path / "screenshots"

    class FakePage:
        async def goto(self, url, **kwargs):
            assert url == resort.webcam_url
            assert kwargs["timeout"] == 5000

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

    class FakePlaywrightManager:
        async def start(self):
            return FakePlaywright()

    monkeypatch.setattr(
        "app.webcam.classifier.async_playwright", FakePlaywrightManager
    )
    monkeypatch.setattr(settings, "webcam_snapshot_dir", str(screenshot_path))
    openai_client = SimpleNamespace(
        complete_vision=AsyncMock(
            return_value={
                "classification": "snow_good",
                "description": "Snow covers the slopes.",
            }
        )
    )

    async with WebcamClassifier(openai_client=openai_client) as classifier:
        result = await classifier.classify(resort, uuid.uuid4())

    assert result.classification == "snow_good"
    assert result.description == "Snow covers the slopes."
    assert Path(result.file_path).is_file()
    openai_client.complete_vision.assert_awaited_once()


async def test_playwright_error_returns_offline(resort_factory, monkeypatch, tmp_path):
    resort = resort_factory.build(webcam_url="https://example.test/camera")

    class FakePage:
        async def goto(self, url, **kwargs):
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError

            raise PlaywrightTimeoutError("timed out")

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

    class FakePlaywrightManager:
        async def start(self):
            return FakePlaywright()

    monkeypatch.setattr(
        "app.webcam.classifier.async_playwright", FakePlaywrightManager
    )
    monkeypatch.setattr(settings, "webcam_snapshot_dir", str(tmp_path))

    async with WebcamClassifier() as classifier:
        result = await classifier.classify(resort, uuid.uuid4())

    assert result.classification == "camera_offline"
    assert result.file_path is None
    assert "timed out" in result.error


async def test_write_result_creates_snapshot_record():
    from app.webcam.classifier import WebcamResult

    result = WebcamResult(
        resort_id=uuid.uuid4(),
        classification="camera_offline",
        description="No webcam URL configured.",
        file_path=None,
        captured_at=datetime.now(timezone.utc),
    )
    session = SimpleNamespace(add=lambda record: setattr(session, "record", record))
    run_id = uuid.uuid4()

    await WebcamClassifier.write_result(session, run_id, result)

    assert session.record.weekly_run_id == run_id
    assert session.record.resort_id == result.resort_id
    assert session.record.classification == "camera_offline"
