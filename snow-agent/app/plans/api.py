"""Trip plan routes; every query is scoped to the authenticated user."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app.auth import require_user
from app.db.models.resort import Resort
from app.db.models.trip_plan import TripPlan
from app.db.session import AsyncSessionLocal
from app.plans.service import plan_payload, resort_names

router = APIRouter()


@router.get("/api/plans")
async def list_plans(
    user_id: Annotated[uuid.UUID, Depends(require_user)],
) -> list[dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        plans = (
            await session.execute(
                select(TripPlan)
                .where(TripPlan.user_id == user_id)
                .order_by(TripPlan.created_at.desc())
            )
        ).scalars().all()
        names = await resort_names(session, {plan.resort_id for plan in plans})
    return [jsonable_encoder(plan_payload(p, names.get(p.resort_id))) for p in plans]


async def _owned_plan(session, plan_id: uuid.UUID, user_id: uuid.UUID) -> TripPlan:
    plan = await session.get(TripPlan, plan_id)
    # A foreign plan is reported as missing so IDs cannot be probed.
    if plan is None or plan.user_id != user_id:
        raise HTTPException(status_code=404, detail="Plan not found.")
    return plan


@router.get("/api/plans/{plan_id}")
async def get_plan(
    plan_id: uuid.UUID,
    user_id: Annotated[uuid.UUID, Depends(require_user)],
) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        plan = await _owned_plan(session, plan_id, user_id)
        resort = await session.get(Resort, plan.resort_id)
    return jsonable_encoder(plan_payload(plan, resort.name if resort else None))


@router.delete("/api/plans/{plan_id}")
async def cancel_plan(
    plan_id: uuid.UUID,
    user_id: Annotated[uuid.UUID, Depends(require_user)],
) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        plan = await _owned_plan(session, plan_id, user_id)
        if plan.status == "booked":
            raise HTTPException(status_code=409, detail="A booked plan cannot be cancelled here.")
        plan.status = "cancelled"
        await session.commit()
        await session.refresh(plan)
        resort = await session.get(Resort, plan.resort_id)
        payload = plan_payload(plan, resort.name if resort else None)
    return jsonable_encoder(payload)
