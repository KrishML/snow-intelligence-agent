"""Draft trip plan serialisation and delivery."""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.resort import Resort
from app.db.models.trip_plan import TripPlan
from app.db.session import AsyncSessionLocal
from app.delivery.email_sender import SendGridDelivery
from app.delivery.telegram_bot import TelegramDelivery
from app.prefs.service import Preferences

logger = logging.getLogger(__name__)


def offer_total_gbp(option: dict[str, Any] | None) -> Decimal | None:
    """Read an explicit GBP total from a flight or hotel option; never convert currencies."""
    if not option:
        return None
    price = option.get("price")
    if isinstance(price, dict) and price.get("currency") == "GBP":
        amount = price.get("grandTotal", price.get("total"))
    elif option.get("currency") == "GBP":
        amount = option.get("total_gbp", option.get("price_gbp"))
    else:
        return None
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value if value.is_finite() and value >= 0 else None


def plan_payload(plan: TripPlan, resort_name: str | None) -> dict[str, Any]:
    flight = offer_total_gbp(plan.flight_option)
    hotel = offer_total_gbp(plan.hotel_option)
    return {
        "id": str(plan.id),
        "session_id": plan.session_id,
        "resort_id": str(plan.resort_id),
        "resort_name": resort_name,
        "outbound_date": plan.outbound_date.isoformat(),
        "return_date": plan.return_date.isoformat(),
        "flight_option": plan.flight_option,
        "hotel_option": plan.hotel_option,
        "flight_total_gbp": str(flight) if flight is not None else None,
        "hotel_total_gbp": str(hotel) if hotel is not None else None,
        "total_estimated_gbp": (
            str(plan.total_estimated_gbp) if plan.total_estimated_gbp is not None else None
        ),
        "session_notes": plan.session_notes,
        "status": plan.status,
        "delivery_errors": plan.delivery_errors,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
        "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
    }


async def resort_names(
    session: AsyncSession, resort_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    if not resort_ids:
        return {}
    rows = await session.execute(
        select(Resort.id, Resort.name).where(Resort.id.in_(resort_ids))
    )
    return {row.id: row.name for row in rows}


async def load_plan_payload(
    plan_id: uuid.UUID, user_id: uuid.UUID
) -> dict[str, Any] | None:
    """Load one plan owned by ``user_id`` as a JSON-ready payload."""
    async with AsyncSessionLocal() as session:
        plan = await session.get(TripPlan, plan_id)
        if plan is None or plan.user_id != user_id:
            return None
        names = await resort_names(session, {plan.resort_id})
    return plan_payload(plan, names.get(plan.resort_id))


async def dispatch_plan(
    payload: dict[str, Any],
    prefs: Preferences,
    telegram: TelegramDelivery | None = None,
    email: SendGridDelivery | None = None,
) -> dict[str, str]:
    """Send the draft plan to both channels; return per-channel failure reasons."""
    telegram = telegram or TelegramDelivery()
    email = email or SendGridDelivery()
    failures: dict[str, str] = {}
    if not await telegram.send_plan(payload, prefs.telegram_chat_id):
        failures["telegram"] = telegram.last_error or "Delivery did not succeed."
    if not await email.send_plan(payload, prefs.email):
        failures["email"] = email.last_error or "Delivery did not succeed."
    return failures
