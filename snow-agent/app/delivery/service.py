"""Coordinated weekly report dispatch for both delivery channels."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.qualitative_signal import QualitativeSignal
from app.db.models.resort import Resort
from app.db.models.snow_score import SnowScore
from app.db.models.weekly_run import WeeklyRun
from app.db.session import AsyncSessionLocal
from app.delivery.email_sender import SendGridDelivery
from app.delivery.telegram_bot import TelegramDelivery

logger = logging.getLogger(__name__)


class DeliveryService:
    def __init__(
        self,
        telegram: TelegramDelivery | None = None,
        email: SendGridDelivery | None = None,
        session_factory: Callable[[], AsyncSession] | None = None,
    ) -> None:
        self._telegram = telegram or TelegramDelivery()
        self._email = email or SendGridDelivery()
        self._session_factory = session_factory or AsyncSessionLocal

    async def dispatch(self, weekly_run_id: uuid.UUID, success: bool) -> dict[str, bool]:
        """Send reports or failure alerts and persist any delivery errors."""
        run, top5, partial_errors = await self._load_report_data(weekly_run_id)
        if run is None:
            logger.error("Cannot dispatch for missing weekly run %s", weekly_run_id)
            return {"telegram": False, "email": False}

        if success:
            telegram_ok = await self._telegram.send_weekly_report(top5, weekly_run_id)
            results = {"telegram": telegram_ok}
            if telegram_ok:
                results["email"] = await self._email.send_weekly_report(
                    top5, weekly_run_id
                )
            failures = self._channel_failures(results)
            if partial_errors:
                notification_results = await self._send_failure_notifications(
                    weekly_run_id, partial_errors
                )
                failures.update(
                    {
                        f"failure_notification_{channel}": reason
                        for channel, reason in self._channel_failures(
                            notification_results
                        ).items()
                    }
                )
            if failures:
                await self._record_delivery_errors(weekly_run_id, failures)
            return results

        failure_reasons = partial_errors or {"weekly_run": "The weekly run failed."}
        results = await self._send_failure_notifications(
            weekly_run_id, failure_reasons
        )
        failures = self._channel_failures(results)
        if failures:
            await self._record_delivery_errors(weekly_run_id, failures)
        return results

    async def _load_report_data(
        self, weekly_run_id: uuid.UUID
    ) -> tuple[WeeklyRun | None, list[dict[str, Any]], dict[str, str]]:
        async with self._session_factory() as session:
            run = await session.get(WeeklyRun, weekly_run_id)
            if run is None:
                return None, [], {}
            score_result = await session.execute(
                select(SnowScore, Resort)
                .join(Resort, Resort.id == SnowScore.resort_id)
                .where(
                    SnowScore.weekly_run_id == weekly_run_id,
                    SnowScore.passed_gate.is_(True),
                    SnowScore.rank.is_not(None),
                    SnowScore.rank <= 5,
                )
                .order_by(SnowScore.rank.asc())
            )
            signal_result = await session.execute(
                select(QualitativeSignal)
                .where(QualitativeSignal.weekly_run_id == weekly_run_id)
                .order_by(QualitativeSignal.created_at.desc())
            )
            summaries: dict[uuid.UUID, str] = {}
            for signal in signal_result.scalars().all():
                summaries.setdefault(signal.resort_id, signal.summary or "")
            rows = score_result.all()
            top5 = [
                {
                    "name": resort.name,
                    "country": resort.country,
                    "depth_cm": score.depth_cm,
                    "score_total": score.score_total,
                    "score_depth": score.score_depth,
                    "score_forecast": score.score_forecast,
                    "score_historical": score.score_historical,
                    "score_qualitative": score.score_qualitative,
                    "historical_reliability_pct": score.historical_reliability_pct,
                    "summary": summaries.get(resort.id, ""),
                }
                for score, resort in rows
            ]
            errors = run.error_log if isinstance(run.error_log, dict) else {}
            return run, top5, {str(key): str(value) for key, value in errors.items()}

    async def _send_failure_notifications(
        self, run_id: uuid.UUID, failures: dict[str, str]
    ) -> dict[str, bool]:
        telegram_result, email_result = await asyncio.gather(
            self._telegram.send_failure_notification(failures, run_id),
            self._email.send_failure_notification(failures, run_id),
            return_exceptions=True,
        )
        return {
            "telegram": self._result_succeeded(telegram_result),
            "email": self._result_succeeded(email_result),
        }

    async def _record_delivery_errors(
        self, run_id: uuid.UUID, failures: dict[str, str]
    ) -> None:
        async with self._session_factory() as session:
            run = await session.get(WeeklyRun, run_id)
            if run is None:
                return
            error_log = dict(run.error_log) if isinstance(run.error_log, dict) else {}
            error_log["delivery"] = failures
            run.error_log = error_log
            await session.commit()

    def _channel_failures(self, results: dict[str, bool]) -> dict[str, str]:
        failures = {}
        for channel, succeeded in results.items():
            if succeeded:
                continue
            error = (
                self._telegram.last_error
                if channel == "telegram"
                else self._email.last_error
            )
            failures[channel] = error or "Delivery did not succeed."
        return failures

    @staticmethod
    def _result_succeeded(result: Any) -> bool:
        if isinstance(result, BaseException):
            logger.error("Failure notification dispatch raised: %s", result)
            return False
        return result is True
