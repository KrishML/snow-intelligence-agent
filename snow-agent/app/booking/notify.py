"""Booking confirmation and failure notifications over email and Telegram."""

from __future__ import annotations

import html
import logging

from app.booking.models import Booking
from app.booking.providers import PlanView
from app.delivery.email_sender import SendGridDelivery
from app.delivery.telegram_bot import TelegramDelivery

logger = logging.getLogger(__name__)


def success_text(booking: Booking, plan: PlanView) -> str:
    return "\n".join(
        [
            f"Booking confirmed: {plan.resort_name or 'your trip'}",
            f"Dates: {plan.outbound_date} to {plan.return_date}",
            f"Flight reference: {booking.flight_booking_reference}",
            f"Hotel confirmation: {booking.hotel_confirmation}",
            f"Total charged: £{plan.total}",
            f"Booking ID: {booking.id}",
        ]
    )


def failure_text(booking: Booking, plan: PlanView) -> str:
    lines = [
        f"Booking FAILED for {plan.resort_name or 'your trip'} (booking {booking.id}).",
        "You have not been charged for this attempt." if not booking.needs_manual_review
        else "Some holds could not be released automatically and need manual review.",
        f"Reason: {booking.error}",
        "Nothing was retried. Create a new plan to try again.",
    ]
    return "\n".join(lines)


class ChannelNotifier:
    def __init__(
        self,
        telegram: TelegramDelivery | None = None,
        email: SendGridDelivery | None = None,
    ) -> None:
        self._telegram = telegram or TelegramDelivery()
        self._email = email or SendGridDelivery()

    async def _send(self, subject: str, text: str) -> None:
        if not await self._telegram.send_text(text):
            logger.error("Booking Telegram notification failed: %s", self._telegram.last_error)
        body = "<br>".join(html.escape(line) for line in text.splitlines())
        if not await self._email.send_html(subject, f"<p>{body}</p>"):
            logger.error("Booking email notification failed: %s", self._email.last_error)

    async def booking_succeeded(self, booking: Booking, plan: PlanView) -> None:
        await self._send("Your Snow Intelligence booking is confirmed", success_text(booking, plan))

    async def booking_failed(self, booking: Booking, plan: PlanView) -> None:
        await self._send("Your Snow Intelligence booking failed", failure_text(booking, plan))
