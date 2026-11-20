"""Wiring of Telegram commands to the database and Redis."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.chat.store import create_redis_client
from app.dashboard.service import weekly_dashboard
from app.db.models.resort import Resort
from app.db.models.trip_plan import TripPlan
from app.db.session import AsyncSessionLocal
from app.delivery.telegram_commands import TelegramCommands
from app.plans.service import plan_payload
from app.prefs.service import Preferences, PrefsService
from app.prefs.telegram_link import LinkCodeStore


async def load_report(prefs: Preferences) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        return await weekly_dashboard(session, prefs)


async def load_latest_draft_plan(user_id: uuid.UUID) -> dict[str, Any] | None:
    async with AsyncSessionLocal() as session:
        plan = (
            await session.execute(
                select(TripPlan)
                .where(TripPlan.user_id == user_id, TripPlan.status == "draft")
                .order_by(TripPlan.created_at.desc())
                .limit(1)
            )
        ).scalars().first()
        if plan is None:
            return None
        resort = await session.get(Resort, plan.resort_id)
        return plan_payload(plan, resort.name if resort else None)


def build_commands() -> TelegramCommands:
    return TelegramCommands(
        PrefsService(),
        LinkCodeStore(create_redis_client()),
        load_report,
        load_latest_draft_plan,
    )
