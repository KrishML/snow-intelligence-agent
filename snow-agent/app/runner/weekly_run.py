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
import uuid
from datetime import date, datetime
from typing import Literal

from sqlalchemy import select

from app.clients.open_meteo import (
    ForecastData,
    OpenMeteoClient,
    OpenMeteoFetchError,
    ResortConditions,
)
from app.db.models.resort import Resort
from app.db.models.snow_score import SnowScore
from app.db.models.weekly_run import WeeklyRun
from app.db.session import AsyncSessionLocal
from app.scoring.engine import apply_gate, compute_score
from app.scoring.schemas import DayForecast, GateInput, ScoreInput
from app.settings import settings

logger = logging.getLogger(__name__)


class WeeklyRunOrchestrator:
    """Orchestrates a single weekly snow intelligence run."""

    async def run(self, mode: Literal["sports", "tourism"]) -> uuid.UUID:
        """Execute a full weekly run.

        Returns the ``weekly_run_id`` of the created record regardless of
        whether the run succeeds or fails.
        """
        # Step 1: create the weekly_run record with status='running'
        async with AsyncSessionLocal() as session:
            weekly_run = WeeklyRun(
                id=uuid.uuid4(),
                run_date=date.today(),
                mode=mode,
                status="running",
            )
            session.add(weekly_run)
            await session.commit()
            run_id = weekly_run.id

        logger.info("Weekly run %s started (mode=%s)", run_id, mode)

        try:
            await self._execute_run(run_id, mode)
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Weekly run %s failed fatally: %s", run_id, exc)
            async with AsyncSessionLocal() as session:
                run = await session.get(WeeklyRun, run_id)
                if run:
                    run.status = "failed"
                    run.completed_at = datetime.utcnow()
                    run.error_log = {"fatal_error": str(exc)}
                    await session.commit()
            raise

        return run_id

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
                logger.error("Unexpected gather exception: %s", item)
                continue
            resort_id, payload = item
            if isinstance(payload, OpenMeteoFetchError):
                error_log[str(resort_id)] = str(payload)
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
                depth_cm=int(conditions.snow_depth_cm) if conditions.snow_depth_cm else None,
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

        # Step 6: rank passing resorts by score_total DESC (all passing, per spec)
        passing = sorted(
            [s for s in snow_scores if s.passed_gate and s.score_total is not None],
            key=lambda s: s.score_total,  # type: ignore[return-value]
            reverse=True,
        )
        for rank, score_record in enumerate(passing, start=1):
            score_record.rank = rank

        # Write all SnowScore records in a single transaction
        async with AsyncSessionLocal() as session:
            session.add_all(snow_scores)
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
                run.completed_at = datetime.utcnow()
                if error_log:
                    run.error_log = error_log
                await session.commit()

        # Step 9 (Phase 1 stub): delivery event
        logger.info(
            "Weekly run %s complete — delivery stub (Phase 4 implements real delivery)",
            run_id,
        )
