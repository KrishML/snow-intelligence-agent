"""Telegram ``/book`` and ``/confirm`` for the booking service (Phase 8).

Thin orchestration over ``BookingEngine``: no booking, payment or consent logic
lives here. ``/book`` is read-only; ``/confirm`` needs a prior ``/book`` for the
same plan inside the confirmation window and the price lock.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

from app.booking.config import BookingSettings
from app.booking.engine import BookingEngine, BookingRefused, load_plan_view
from app.booking.providers import Traveller

logger = logging.getLogger(__name__)

_HELP = "Use /book <plan_id> to see the cost, then /confirm <plan_id> to book."


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_traveller(raw: str) -> Traveller | None:
    try:
        data = json.loads(raw)
        return Traveller(**{**data, "born_on": date.fromisoformat(data["born_on"])})
    except (ValueError, TypeError, KeyError):
        return None


class TelegramBookingHandler:
    def __init__(
        self,
        engine: BookingEngine,
        settings: BookingSettings,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._now = now
        # (chat_id, plan_id) -> when the /book confirmation window closes. In memory on
        # purpose: a restart forces a fresh /book rather than resuming stale consent.
        self._pending: dict[tuple[str, uuid.UUID], datetime] = {}

    async def handle(self, chat_id: str, text: str) -> str:
        if chat_id != self._settings.telegram_chat_id or not chat_id:
            return "This chat is not authorised to book."
        parts = text.strip().split()
        if len(parts) != 2 or parts[0].lower() not in ("/book", "/confirm"):
            return _HELP
        try:
            plan_id = uuid.UUID(parts[1])
        except ValueError:
            return "That is not a valid plan ID."

        refusal = await self._not_ready()
        if refusal:
            return refusal
        try:
            if parts[0].lower() == "/book":
                return await self._book(chat_id, plan_id)
            return await self._confirm(chat_id, plan_id)
        except BookingRefused as exc:
            blockers = "".join(f"\n- {b}" for b in exc.blockers)
            return f"{exc}{blockers}"

    async def _not_ready(self) -> str | None:
        if not self._settings.telegram_booking_enabled:
            return "Telegram booking is not enabled."
        needed = self._settings.telegram_booking_min_bookings
        if await self._engine.count_booked() < needed:
            return f"Telegram booking unlocks after {needed} successful web bookings."
        if not self._settings.telegram_booking_payment_method_id or parse_traveller(
            self._settings.telegram_booking_traveller_json
        ) is None:
            return "Telegram booking needs a saved payment method and traveller profile."
        return None

    async def _load(self, plan_id: uuid.UUID):
        async with self._engine.session() as session:
            return await load_plan_view(session, plan_id, self._settings.default_user_id)

    async def _book(self, chat_id: str, plan_id: uuid.UUID) -> str:
        """Read-only: shows the itemised cost and opens the confirmation window."""
        plan = await self._load(plan_id)
        expires = self._engine.price_lock_expires_at(plan)
        if self._now() >= expires:
            raise BookingRefused(409, "The price lock has expired; create a new plan.")
        problems = self._engine.blockers(plan)
        if problems:
            raise BookingRefused(422, "This plan cannot be booked.", problems)
        window = self._now() + timedelta(minutes=self._settings.telegram_confirm_window_minutes)
        self._pending[(chat_id, plan_id)] = min(window, expires)
        return "\n".join(
            [
                f"Booking summary: {plan.resort_name or 'your trip'}",
                f"Dates: {plan.outbound_date} to {plan.return_date}",
                f"Flight: £{plan.flight_total}",
                f"Accommodation: £{plan.hotel_total}",
                f"Total: £{plan.total}",
                "",
                f"Nothing is booked or charged yet. To proceed send /confirm {plan_id}",
                f"(valid until {self._pending[(chat_id, plan_id)]:%H:%M} UTC).",
            ]
        )

    async def _confirm(self, chat_id: str, plan_id: uuid.UUID) -> str:
        # Consume first so a double-send cannot confirm twice.
        deadline = self._pending.pop((chat_id, plan_id), None)
        if deadline is None:
            return f"Send /book {plan_id} first to review the cost."
        if self._now() >= deadline:
            return f"That confirmation expired. Send /book {plan_id} again."
        plan = await self._load(plan_id)
        traveller = parse_traveller(self._settings.telegram_booking_traveller_json)
        assert traveller is not None  # guaranteed by _not_ready
        booking = await self._engine.book(
            plan, "telegram", self._settings.telegram_booking_payment_method_id, traveller
        )
        if booking.status == "booked":
            return (
                f"Booked. Flight reference {booking.flight_booking_reference}, "
                f"hotel confirmation {booking.hotel_confirmation}."
            )
        review = " Manual review is needed." if booking.needs_manual_review else ""
        return f"Booking failed: {booking.error}.{review} Nothing was retried."


def build_application(handler: TelegramBookingHandler, token: str):
    """Create a polling Telegram application wired to ``handler``."""
    from telegram import Update
    from telegram.ext import Application, ContextTypes, MessageHandler, filters

    async def on_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None or update.message is None or not update.message.text:
            return
        reply = await handler.handle(str(update.effective_chat.id), update.message.text)
        await update.message.reply_text(reply)

    application = Application.builder().token(token).build()
    application.add_handler(MessageHandler(filters.COMMAND, on_command))
    return application
