"""Capture and classify resort webcam images."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.openai_client import OpenAIClient, OpenAIClientError
from app.db.models.resort import Resort
from app.db.models.webcam_snapshot import WebcamSnapshot
from app.settings import settings
from app.storage import SnapshotStorage

logger = logging.getLogger(__name__)

WebcamClassification = Literal[
    "snow_good", "snow_poor", "no_snow", "camera_offline"
]
_CLASSIFICATIONS = {"snow_good", "snow_poor", "no_snow", "camera_offline"}

_SYSTEM_PROMPT = """Classify the snow conditions visible in this resort webcam image.
Return a JSON object with exactly two fields:
- "classification": exactly one of "snow_good", "snow_poor", "no_snow", "camera_offline"
- "description": one concise sentence describing what is visible
Use "camera_offline" only when the image does not show a usable resort webcam scene."""


@dataclass(frozen=True)
class WebcamResult:
    resort_id: uuid.UUID
    classification: WebcamClassification
    description: str
    file_path: str | None
    captured_at: datetime
    error: str | None = None


class WebcamClassifier:
    """Reuse one headless browser while classifying a bounded number of cameras."""

    def __init__(
        self,
        openai_client: OpenAIClient | None = None,
        concurrency: int | None = None,
        storage: SnapshotStorage | None = None,
    ) -> None:
        self._openai = openai_client or OpenAIClient()
        self._storage = storage or SnapshotStorage()
        self._sem = asyncio.Semaphore(concurrency or settings.webcam_concurrency)
        self._playwright = None
        self._browser = None

    async def __aenter__(self) -> "WebcamClassifier":
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)
        except Exception:
            logger.exception("Could not launch Playwright Chromium for webcam capture")
            await self.aclose()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                logger.exception("Could not close Playwright browser cleanly")
            finally:
                self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                logger.exception("Could not stop Playwright cleanly")
            finally:
                self._playwright = None

    async def classify(self, resort: Resort, weekly_run_id: uuid.UUID) -> WebcamResult:
        """Capture a resort screenshot and classify it, returning offline on capture errors."""
        captured_at = datetime.now(timezone.utc)
        if not resort.webcam_url:
            return WebcamResult(
                resort_id=resort.id,
                classification="camera_offline",
                description="No webcam URL configured.",
                file_path=None,
                captured_at=captured_at,
            )

        async with self._sem:
            if self._browser is None:
                return self._offline_result(
                    resort.id,
                    captured_at,
                    "Playwright Chromium is unavailable.",
                )

            page = None
            screenshot_path = (
                Path(settings.webcam_snapshot_dir)
                / str(weekly_run_id)
                / f"{resort.id}.png"
            )
            try:
                screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                page = await self._browser.new_page()
                await page.goto(
                    resort.webcam_url,
                    wait_until="domcontentloaded",
                    timeout=int(settings.webcam_timeout_seconds * 1000),
                )
                await page.screenshot(path=str(screenshot_path), type="png")
                response = await self._openai.complete_vision(
                    system_prompt=_SYSTEM_PROMPT,
                    user_text=f"Resort webcam for {resort.name}, {resort.country}.",
                    image_path=screenshot_path,
                    model=settings.openai_model_chat,
                )
                classification = response.get("classification")
                description = response.get("description")
                if classification not in _CLASSIFICATIONS or not isinstance(description, str):
                    raise OpenAIClientError("Vision response has invalid classification fields")
                stored_reference: str | None = str(screenshot_path)
                storage_error: str | None = None
                try:
                    stored_reference = await self._storage.persist(
                        screenshot_path, f"{weekly_run_id}/{resort.id}.png"
                    )
                except Exception as exc:
                    # The classification is still valid evidence; surface the storage problem.
                    logger.exception("Snapshot storage failed for %s", resort.name)
                    stored_reference = None
                    storage_error = f"Snapshot storage failed: {exc}"
                return WebcamResult(
                    resort_id=resort.id,
                    classification=classification,
                    description=description[:1000],
                    file_path=stored_reference,
                    captured_at=captured_at,
                    error=storage_error,
                )
            except (PlaywrightTimeoutError, PlaywrightError, OSError, OpenAIClientError) as exc:
                logger.warning("Webcam processing failed for %s: %s", resort.name, exc)
                return self._offline_result(
                    resort.id,
                    captured_at,
                    str(exc),
                    file_path=(
                        str(screenshot_path) if screenshot_path.is_file() else None
                    ),
                )
            except Exception as exc:
                logger.exception("Unexpected webcam processing error for %s", resort.name)
                return self._offline_result(
                    resort.id,
                    captured_at,
                    str(exc),
                    file_path=(
                        str(screenshot_path) if screenshot_path.is_file() else None
                    ),
                )
            finally:
                if page is not None:
                    try:
                        await page.close()
                    except Exception:
                        logger.exception(
                            "Could not close webcam page for resort %s", resort.name
                        )

    @staticmethod
    def _offline_result(
        resort_id: uuid.UUID,
        captured_at: datetime,
        error: str,
        file_path: str | None = None,
    ) -> WebcamResult:
        return WebcamResult(
            resort_id=resort_id,
            classification="camera_offline",
            description="Webcam capture or classification was unavailable.",
            file_path=file_path,
            captured_at=captured_at,
            error=error,
        )

    @staticmethod
    async def write_result(
        session: AsyncSession,
        weekly_run_id: uuid.UUID,
        result: WebcamResult,
    ) -> None:
        session.add(
            WebcamSnapshot(
                id=uuid.uuid4(),
                weekly_run_id=weekly_run_id,
                resort_id=result.resort_id,
                captured_at=result.captured_at,
                classification=result.classification,
                description=result.description,
                file_path=result.file_path,
            )
        )


def apply_webcam_adjustment(
    base_score: int, classification: WebcamClassification
) -> int:
    """Apply the webcam contribution while keeping qualitative scores in [0, 20]."""
    if not 0 <= base_score <= 20:
        raise ValueError("base_score must be between 0 and 20")
    adjustments = {
        "snow_good": 3,
        "snow_poor": -2,
        "no_snow": -5,
        "camera_offline": 0,
    }
    return max(0, min(20, base_score + adjustments[classification]))
