"""Weekly run orchestrator.

Executes the full snow intelligence pipeline for a given mode:
  1. Create WeeklyRun record (status='running')
  2. Fetch all active resorts for the mode
  3. Parallel-fetch current conditions, 5-day forecast, and historical
     reliability for every resort (semaphore-limited, errors accumulated)
  4. Apply gate + compute score for each resort with successful data
  5. Write SnowScore records for every resort
  6. Rank passing resorts by score_total DESC
  7. Update WeeklyRun to status='complete'
  8. On any unrecoverable error: update status='failed'
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import date, datetime, timezone
from typing import Literal

from sqlalchemy import select

from app.clients.open_meteo import (
    ForecastData,
    OpenMeteoClient,
    OpenMeteoFetchError,
    ResortConditions,
)
from app.clients.openai_client import OpenAIClientError
from app.db.models.resort import Resort
from app.db.models.snow_score import SnowScore
from app.db.models.weekly_run import WeeklyRun
from app.db.session import AsyncSessionLocal
from app.delivery.service import DeliveryService
from app.scoring.qualitative import QualitativeResult, QualitativeSignalScorer
from app.scoring.engine import apply_gate, compute_score
from app.scoring.schemas import DayForecast, GateInput, ScoreInput
from app.settings import settings
from app.utils import describe_error
from app.webcam.classifier import WebcamClassifier, WebcamResult, apply_webcam_adjustment

logger = logging.getLogger(__name__)

WEBCAM_TOP_N = 10
RUN_DURATION_TARGET_SECONDS = 30 * 60


class WeeklyRunOrchestrator:
    """Orchestrates a single weekly snow intelligence run."""

    async def create_run(self, mode: Literal["sports", "tourism"]) -> uuid.UUID:
        """Insert a ``running`` WeeklyRun record and return its id."""
        async with AsyncSessionLocal() as session:
            weekly_run = WeeklyRun(
                id=uuid.uuid4(),
                run_date=date.today(),
                mode=mode,
                status="running",
            )
            session.add(weekly_run)
            await session.commit()
            return weekly_run.id

    async def run(
        self,
        mode: Literal["sports", "tourism"],
        run_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        """Execute a full weekly run.

        Pass ``run_id`` from ``create_run`` to execute a pre-created record.
        Returns the ``weekly_run_id`` regardless of whether the run succeeds.
        """
        if run_id is None:
            run_id = await self.create_run(mode)

        logger.info("Weekly run %s started (mode=%s)", run_id, mode)
        started = time.monotonic()

        try:
            await self._execute_run(run_id, mode)
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Weekly run %s failed fatally: %s", run_id, exc)
            async with AsyncSessionLocal() as session:
                run = await session.get(WeeklyRun, run_id)
                if run:
                    run.status = "failed"
                    run.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    error_log = dict(run.error_log) if isinstance(run.error_log, dict) else {}
                    error_log["fatal_error"] = describe_error(exc)
                    run.error_log = error_log
                    await session.commit()
            await self._dispatch_delivery(run_id, success=False)
            self._log_duration(run_id, mode, started, "failed")
            raise

        self._log_duration(run_id, mode, started, "complete")
        await self._dispatch_delivery(run_id, success=True)
        return run_id

    @staticmethod
    def _log_duration(run_id: uuid.UUID, mode: str, started: float, status: str) -> None:
        duration = time.monotonic() - started
        slow = duration > RUN_DURATION_TARGET_SECONDS
        logger.log(
            logging.WARNING if slow else logging.INFO,
            "Weekly run %s %s in %.0fs%s",
            run_id,
            status,
            duration,
            " (over the 30 minute target)" if slow else "",
            extra={"run_id": str(run_id), "mode": mode, "status": status, "duration_seconds": round(duration, 1)},
        )

    async def _dispatch_delivery(self, run_id: uuid.UUID, success: bool) -> None:
        try:
            await DeliveryService().dispatch(run_id, success=success)
        except Exception as exc:
            logger.exception("Delivery dispatch failed for weekly run %s", run_id)
            async with AsyncSessionLocal() as session:
                run = await session.get(WeeklyRun, run_id)
                if run is not None:
                    error_log = (
                        dict(run.error_log)
                        if isinstance(run.error_log, dict)
                        else {}
                    )
                    error_log["delivery"] = {"dispatch": describe_error(exc)}
                    run.error_log = error_log
                    await session.commit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _execute_run(
        self, run_id: uuid.UUID, mode: Literal["sports", "tourism"]
    ) -> None:
        # Step 2: fetch mode-appropriate active resorts from DB
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Resort).where(
                    Resort.is_active == True,  # noqa: E712
                    Resort.mode.in_([mode, "both"]),
                )
            )
            resorts: list[Resort] = list(result.scalars().all())

        logger.info(
            "Weekly run %s: found %d resorts for mode=%s",
            run_id,
            len(resorts),
            mode,
        )

        # Step 3: parallel-fetch all resort data
        sem = asyncio.Semaphore(settings.open_meteo_concurrency)
        error_log: dict[str, str] = {}
        fetch_results: dict[
            uuid.UUID, tuple[ResortConditions, ForecastData, float]
        ] = {}

        async with OpenMeteoClient(semaphore=sem) as client:

            async def fetch_one(
                resort: Resort,
            ) -> tuple[
                uuid.UUID,
                tuple[ResortConditions, ForecastData, float] | OpenMeteoFetchError,
            ]:
                try:
                    conditions = await client.get_current_conditions(
                        resort.id, resort.latitude, resort.longitude
                    )
                    forecast = await client.get_5day_forecast(
                        resort.id, resort.latitude, resort.longitude
                    )
                    reliability = await client.get_historical_reliability(
                        resort.id, resort.latitude, resort.longitude, mode
                    )
                    return resort.id, (conditions, forecast, reliability)
                except OpenMeteoFetchError as exc:
                    return resort.id, exc

            raw_results = await asyncio.gather(
                *[fetch_one(r) for r in resorts],
                return_exceptions=True,
            )

        for item in raw_results:
            # asyncio.gather with return_exceptions=True surfaces BaseExceptions
            # that escape the inner try/except (highly unlikely here, but safe).
            if isinstance(item, BaseException):
                message = describe_error(item)
                error_log[f"open_meteo:unexpected:{len(error_log)}"] = message
                logger.error("Unexpected Open-Meteo gather exception: %s", item)
                continue
            resort_id, payload = item
            if isinstance(payload, OpenMeteoFetchError):
                error_log[str(resort_id)] = describe_error(payload)
                logger.warning("Fetch error for resort %s: %s", resort_id, payload)
            else:
                fetch_results[resort_id] = payload

        # Step 4 & 5: gate + score + build SnowScore records
        resort_map: dict[uuid.UUID, Resort] = {r.id: r for r in resorts}
        snow_scores: list[SnowScore] = []

        for resort_id, (conditions, forecast_data, reliability) in fetch_results.items():
            resort = resort_map[resort_id]

            # Convert client DayForecast → scoring schema DayForecast
            scoring_days = [
                DayForecast(
                    date=d.date,
                    temp_max_c=d.temp_max_c,
                    rain_sum_mm=d.rain_sum_mm,
                    snowfall_sum_cm=d.snowfall_sum_cm,
                )
                for d in forecast_data.days
            ]

            gate_result = apply_gate(
                GateInput(
                    mode=mode,
                    snow_depth_cm=conditions.snow_depth_cm,
                    has_open_run=None,  # Phase 1: unknown
                    forecast_days=scoring_days,
                )
            )

            score_result = None
            if gate_result.passed:
                score_result = compute_score(
                    ScoreInput(
                        mode=mode,
                        snow_depth_cm=conditions.snow_depth_cm,
                        forecast_days=scoring_days,
                        historical_reliability_pct=reliability,
                        qualitative_score=10,  # Phase 1 neutral placeholder
                    )
                )

            forecast_stable = not any(
                d.rain_sum_mm > settings.forecast_rain_exclude_threshold_mm
                or d.temp_max_c > settings.forecast_temp_exclude_threshold_c
                for d in forecast_data.days
            )

            snow_score = SnowScore(
                id=uuid.uuid4(),
                weekly_run_id=run_id,
                resort_id=resort_id,
                passed_gate=gate_result.passed,
                gate_failure_reason=gate_result.reason,
                depth_cm=int(conditions.snow_depth_cm) if conditions.snow_depth_cm is not None else None,
                temperature_c=conditions.temperature_c,
                forecast_stable=forecast_stable,
                forecast_detail={
                    "days": [
                        {
                            "date": str(d.date),
                            "temp_max_c": d.temp_max_c,
                            "rain_sum_mm": d.rain_sum_mm,
                            "snowfall_sum_cm": d.snowfall_sum_cm,
                        }
                        for d in forecast_data.days
                    ]
                },
                historical_reliability_pct=reliability,
                score_depth=score_result.score_depth if score_result else None,
                score_forecast=score_result.score_forecast if score_result else None,
                score_historical=score_result.score_historical if score_result else None,
                score_qualitative=score_result.score_qualitative if score_result else None,
                score_total=score_result.score_total if score_result else None,
                rank=None,
            )
            snow_scores.append(snow_score)

        # Step 6: rank Phase 1 scores before persistence; Phase 2 recomputes later.
        passing = sorted(
            [s for s in snow_scores if s.passed_gate and s.score_total is not None],
            key=lambda s: s.score_total,  # type: ignore[return-value]
            reverse=True,
        )
        for rank, score_record in enumerate(passing, start=1):
            score_record.rank = rank

        # Persist base scores before running slower signal-source calls.
        async with AsyncSessionLocal() as session:
            session.add_all(snow_scores)
            await session.commit()

        # Phase 2: collect qualitative and webcam evidence for gate-passing resorts.
        if passing:
            qualitative_scorer = QualitativeSignalScorer()
            signal_deadline = (
                asyncio.get_running_loop().time()
                + settings.qualitative_timeout_minutes * 60
            )
            async with WebcamClassifier() as webcam_classifier:
                # Screenshots are slow, so only the current top-ranked resorts get one.
                webcam_ids = {s.resort_id for s in passing[:WEBCAM_TOP_N]}
                tasks = {
                    asyncio.create_task(
                        self._collect_signals(
                            resort_map[score.resort_id],
                            run_id,
                            mode,
                            qualitative_scorer,
                            webcam_classifier if score.resort_id in webcam_ids else None,
                        )
                    ): score
                    for score in passing
                }
                done, pending = await asyncio.wait(
                    tasks,
                    timeout=max(
                        0,
                        signal_deadline - asyncio.get_running_loop().time(),
                    ),
                )
                phase2_results: dict[
                    uuid.UUID,
                    tuple[QualitativeResult, WebcamResult | None, dict[str, str]],
                ] = {}
                for task in done:
                    score = tasks[task]
                    try:
                        qualitative, webcam, component_errors = task.result()
                    except Exception as exc:
                        error_log[f"phase2:{score.resort_id}"] = describe_error(exc)
                        logger.exception(
                            "Phase 2 signal processing failed for resort %s",
                            score.resort_id,
                        )
                        qualitative, webcam = self._neutral_signal_results(
                            score.resort_id, describe_error(exc)
                        )
                        component_errors = {"phase2": describe_error(exc)}
                    for component, message in component_errors.items():
                        error_log[f"{component}:{score.resort_id}"] = message
                    if webcam is not None and webcam.error:
                        error_log[f"webcam:{score.resort_id}"] = webcam.error
                    phase2_results[score.resort_id] = (
                        qualitative,
                        webcam,
                        component_errors,
                    )

                if pending:
                    for task in pending:
                        score = tasks[task]
                        error = "Signal processing exceeded the weekly-run time limit"
                        error_log[f"phase2:{score.resort_id}"] = error
                        task.cancel()
                        qualitative, webcam = self._neutral_signal_results(
                            score.resort_id, error
                        )
                        phase2_results[score.resort_id] = (
                            qualitative,
                            webcam,
                            {"phase2": error},
                        )
                    await asyncio.gather(*pending, return_exceptions=True)

            embedding_vectors: dict[uuid.UUID, list[float]] = {}
            summaries = [
                (resort_id, qualitative.summary)
                for resort_id, (qualitative, _, _) in phase2_results.items()
                if qualitative.summary
            ]
            if not settings.openai_api_key:
                embedding_error = "OPENAI_API_KEY is not configured"
                for resort_id, _ in summaries:
                    phase2_results[resort_id][0].source_errors[
                        "openai_embedding"
                    ] = embedding_error
            elif summaries:
                try:
                    vectors = await asyncio.wait_for(
                        qualitative_scorer.embed_summaries(
                            [summary for _, summary in summaries]
                        ),
                        timeout=max(
                            0,
                            signal_deadline - asyncio.get_running_loop().time(),
                        ),
                    )
                    embedding_vectors = {
                        resort_id: vector
                        for (resort_id, _), vector in zip(
                            summaries, vectors, strict=True
                        )
                    }
                except (OpenAIClientError, TimeoutError) as exc:
                    message = f"Summary embedding failed: {exc}"
                    for resort_id, _ in summaries:
                        phase2_results[resort_id][0].source_errors[
                            "openai_embedding"
                        ] = message

            async with AsyncSessionLocal() as session:
                persisted_scores = await session.execute(
                    select(SnowScore).where(SnowScore.weekly_run_id == run_id)
                )
                score_by_resort = {
                    score.resort_id: score
                    for score in persisted_scores.scalars().all()
                }
                for resort_id, (qualitative, webcam, _) in phase2_results.items():
                    score = score_by_resort[resort_id]
                    score.score_qualitative = (
                        apply_webcam_adjustment(qualitative.score, webcam.classification)
                        if webcam is not None
                        else qualitative.score
                    )
                    score.score_total = min(
                        100,
                        (score.score_depth or 0)
                        + (score.score_forecast or 0)
                        + (score.score_historical or 0)
                        + score.score_qualitative,
                    )
                    await qualitative_scorer.write_results(
                        session,
                        run_id,
                        qualitative,
                        embedding_vector=embedding_vectors.get(resort_id),
                    )
                    for component, message in qualitative.source_errors.items():
                        error_log[f"{component}:{resort_id}"] = message
                    if webcam is not None:
                        await WebcamClassifier.write_result(session, run_id, webcam)

                updated_passing = sorted(
                    (score for score in score_by_resort.values() if score.passed_gate),
                    key=lambda score: score.score_total or 0,
                    reverse=True,
                )
                for rank, score in enumerate(updated_passing, start=1):
                    score.rank = rank
                await session.commit()

        logger.info(
            "Weekly run %s: wrote %d snow_scores (%d passed gate)",
            run_id,
            len(snow_scores),
            len(passing),
        )

        # Step 7: update WeeklyRun to 'complete'
        async with AsyncSessionLocal() as session:
            run = await session.get(WeeklyRun, run_id)
            if run is not None:
                run.status = "complete"
                run.resorts_checked = len(fetch_results)
                run.resorts_passed = len(passing)
                run.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                if error_log:
                    run.error_log = error_log
                await session.commit()

        # Step 9 (Phase 1 stub): delivery event
        logger.info(
            "Weekly run %s complete",
            run_id,
        )

    async def _collect_signals(
        self,
        resort: Resort,
        run_id: uuid.UUID,
        mode: Literal["sports", "tourism"],
        qualitative_scorer: QualitativeSignalScorer,
        webcam_classifier: WebcamClassifier | None,
    ) -> tuple[QualitativeResult, WebcamResult | None, dict[str, str]]:
        async def _skip_webcam() -> None:
            return None

        qualitative, webcam = await asyncio.gather(
            qualitative_scorer.score(resort, mode),
            webcam_classifier.classify(resort, run_id)
            if webcam_classifier is not None
            else _skip_webcam(),
            return_exceptions=True,
        )
        errors: dict[str, str] = {}
        if isinstance(qualitative, BaseException):
            errors["qualitative"] = describe_error(qualitative)
            neutral, _ = self._neutral_signal_results(
                resort.id, describe_error(qualitative)
            )
            qualitative = neutral
        if isinstance(webcam, BaseException):
            errors["webcam"] = describe_error(webcam)
            _, offline = self._neutral_signal_results(resort.id, describe_error(webcam))
            webcam = offline
        errors.update(qualitative.source_errors)
        if webcam is not None and webcam.error:
            errors["webcam"] = webcam.error
        return qualitative, webcam, errors

    @staticmethod
    def _neutral_signal_results(
        resort_id: uuid.UUID, error: str
    ) -> tuple[QualitativeResult, WebcamResult]:
        from datetime import datetime, timezone

        return (
            QualitativeResult(
                resort_id=resort_id,
                score=10,
                sentiment="neutral",
                summary="Signal processing was unavailable. Conditions unknown.",
                source_text_snippet="",
            ),
            WebcamResult(
                resort_id=resort_id,
                classification="camera_offline",
                description="Webcam processing was unavailable.",
                file_path=None,
                captured_at=datetime.now(timezone.utc),
                error=error,
            ),
        )
