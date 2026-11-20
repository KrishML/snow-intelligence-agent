"""Telegram weekly-report and failure notifications."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from telegram import Bot
from telegram.error import TelegramError

from app.settings import settings

logger = logging.getLogger(__name__)


class TelegramDelivery:
    def __init__(self, bot_factory=Bot) -> None:
        self._bot_factory = bot_factory
        self.last_error: str | None = None

    async def send_weekly_report(
        self, top5: list[dict[str, Any]], run_id: uuid.UUID
    ) -> bool:
        self.last_error = None
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            return self._failed("Telegram delivery is not configured.")
        lines = ["❄️ Snow Report", f"Weekly run: {run_id}", ""]
        for rank, resort in enumerate(top5, start=1):
            lines.extend(
                [
                    f"{rank}. {resort['name']}, {resort['country']}",
                    (
                        f"   Depth: {resort.get('depth_cm', '—')}cm | "
                        f"Score: {resort.get('score_total', '—')}/100 | "
                        f"Reliability: {resort.get('historical_reliability_pct', '—')}%"
                    ),
                    f"   {resort.get('summary') or 'No recent qualitative report.'}",
                    "",
                ]
            )
        try:
            bot = self._bot_factory(token=settings.telegram_bot_token)
            await bot.send_message(
                chat_id=settings.telegram_chat_id,
                text="\n".join(lines),
            )
            return True
        except TelegramError as exc:
            logger.error("Telegram weekly report failed: %s", exc)
            return self._failed(str(exc))
        except Exception as exc:
            logger.exception("Unexpected Telegram weekly report failure")
            return self._failed(str(exc))

    async def send_failure_notification(
        self, failed_components: dict[str, str], run_id: uuid.UUID
    ) -> bool:
        self.last_error = None
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            return self._failed("Telegram delivery is not configured.")
        lines = [f"⚠️ Weekly Snow Run {run_id} — failure or partial failure", ""]
        lines.extend(
            f"- {component}: {reason}"
            for component, reason in failed_components.items()
        )
        try:
            bot = self._bot_factory(token=settings.telegram_bot_token)
            await bot.send_message(
                chat_id=settings.telegram_chat_id,
                text="\n".join(lines),
            )
            return True
        except TelegramError as exc:
            logger.error("Telegram failure notification failed: %s", exc)
            return self._failed(str(exc))
        except Exception as exc:
            logger.exception("Unexpected Telegram failure notification error")
            return self._failed(str(exc))

    async def send_text(self, text: str, chat_id: str | None = None) -> bool:
        self.last_error = None
        chat_id = chat_id or settings.telegram_chat_id
        if not settings.telegram_bot_token or not chat_id:
            return self._failed("Telegram delivery is not configured.")
        try:
            bot = self._bot_factory(token=settings.telegram_bot_token)
            await bot.send_message(chat_id=chat_id, text=text)
            return True
        except TelegramError as exc:
            logger.error("Telegram message failed: %s", exc)
            return self._failed(str(exc))
        except Exception as exc:
            logger.exception("Unexpected Telegram message failure")
            return self._failed(str(exc))

    async def send_plan(
        self, plan: dict[str, Any], chat_id: str | None = None
    ) -> bool:
        self.last_error = None
        chat_id = chat_id or settings.telegram_chat_id
        if not settings.telegram_bot_token or not chat_id:
            return self._failed("Telegram delivery is not configured.")
        try:
            bot = self._bot_factory(token=settings.telegram_bot_token)
            await bot.send_message(chat_id=chat_id, text=format_plan_text(plan))
            return True
        except TelegramError as exc:
            logger.error("Telegram plan delivery failed: %s", exc)
            return self._failed(str(exc))
        except Exception as exc:
            logger.exception("Unexpected Telegram plan delivery failure")
            return self._failed(str(exc))

    def _failed(self, reason: str) -> bool:
        self.last_error = reason
        return False


def format_plan_text(plan: dict[str, Any]) -> str:
    total = plan.get("total_estimated_gbp")
    return "\n".join(
        [
            f"\U0001f4cb Draft trip plan: {plan.get('resort_name') or 'selected resort'}",
            f"Dates: {plan['outbound_date']} to {plan['return_date']}",
            f"Flight: {_money(plan.get('flight_total_gbp'))}",
            f"Accommodation: {_money(plan.get('hotel_total_gbp'))}",
            f"Estimated total: {_money(total)}",
            f"Plan ID: {plan['id']}",
            "",
            "This is a draft only. No booking or payment has been made.",
        ]
    )


def _money(value: Any) -> str:
    return f"\u00a3{value}" if value not in (None, "") else "unavailable"
