"""``/booking/*`` routes with their own authentication."""

from __future__ import annotations

import hmac
import re
import uuid
from datetime import date, datetime, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from app.booking.config import booking_settings
from app.booking.engine import BookingEngine, BookingRefused, load_plan_view
from app.booking.models import Booking, BookingEvent
from app.booking.providers import Traveller

router = APIRouter(prefix="/booking")
_bearer = HTTPBearer(auto_error=False)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def require_booking_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> uuid.UUID:
    if not booking_settings.booking_api_token:
        raise HTTPException(503, "Booking authentication is not configured.")
    token = credentials.credentials if credentials else ""
    if not hmac.compare_digest(token, booking_settings.booking_api_token):
        raise HTTPException(401, "Invalid or missing bearer token.")
    return booking_settings.default_user_id


def get_engine(request: Request) -> BookingEngine:
    return request.app.state.engine


EngineDep = Annotated[BookingEngine, Depends(get_engine)]
UserDep = Annotated[uuid.UUID, Depends(require_booking_user)]


class TravellerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    given_name: str = Field(min_length=1, max_length=60)
    family_name: str = Field(min_length=1, max_length=60)
    born_on: date
    email: str = Field(max_length=254)
    phone_number: str = Field(pattern=r"^\+\d{7,15}$")
    title: Literal["mr", "mrs", "ms", "miss", "dr"] = "mr"
    gender: Literal["m", "f"] = "m"

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        if not _EMAIL.match(value):
            raise ValueError("Invalid email address.")
        return value

    @field_validator("born_on")
    @classmethod
    def _past(cls, value: date) -> date:
        if value >= date.today():
            raise ValueError("Date of birth must be in the past.")
        return value

    def to_traveller(self) -> Traveller:
        return Traveller(**self.model_dump())


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_confirmed: StrictBool
    payment_method_id: str = Field(pattern=r"^pm_[A-Za-z0-9_]{4,100}$")
    traveller: TravellerBody


def booking_json(booking: Booking, events: list[BookingEvent] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "booking_id": str(booking.id),
        "plan_id": str(booking.plan_id),
        "status": booking.status,
        "flight_booking_reference": booking.flight_booking_reference,
        "hotel_confirmation": booking.hotel_confirmation,
        "error": booking.error,
        "needs_manual_review": bool(booking.needs_manual_review),
        "created_at": booking.created_at.isoformat() if booking.created_at else None,
    }
    if events is not None:
        data["events"] = [
            {"status": e.status, "detail": e.detail, "at": e.at.isoformat() if e.at else None}
            for e in events
        ]
    return data


def _refusal(exc: BookingRefused) -> HTTPException:
    detail: dict[str, Any] = {"message": str(exc)}
    if exc.blockers:
        detail["blockers"] = exc.blockers
    return HTTPException(exc.status_code, detail)


@router.get("/plans/{plan_id}/summary")
async def plan_summary(plan_id: uuid.UUID, user_id: UserDep, engine: EngineDep) -> dict[str, Any]:
    try:
        async with engine.session() as session:
            plan = await load_plan_view(session, plan_id, user_id)
        expires = engine.price_lock_expires_at(plan)
        remaining = (expires - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            raise BookingRefused(409, "The price lock has expired; create a new plan.")
    except BookingRefused as exc:
        raise _refusal(exc) from exc
    blockers = engine.blockers(plan)
    return jsonable_encoder(
        {
            "plan_id": plan.plan_id,
            "resort": plan.resort_name,
            "outbound_date": plan.outbound_date,
            "return_date": plan.return_date,
            "currency": "GBP",
            "items": [
                {"label": "Flight", "amount": str(plan.flight_total)},
                {"label": "Accommodation", "amount": str(plan.hotel_total)},
            ],
            "total": str(plan.total),
            "price_lock_expires_at": expires,
            "seconds_remaining": int(remaining),
            "bookable": not blockers,
            "blockers": blockers,
        }
    )


@router.post("/plans/{plan_id}/confirm")
async def confirm_booking(
    plan_id: uuid.UUID, body: ConfirmBody, user_id: UserDep, engine: EngineDep
) -> JSONResponse:
    if body.user_confirmed is not True:
        raise HTTPException(422, "user_confirmed must be the boolean true.")
    try:
        async with engine.session() as session:
            plan = await load_plan_view(session, plan_id, user_id)
        booking = await engine.book(plan, "web", body.payment_method_id, body.traveller.to_traveller())
    except BookingRefused as exc:
        raise _refusal(exc) from exc
    status_code = 201 if booking.status == "booked" else 502
    return JSONResponse(booking_json(booking), status_code=status_code)


@router.get("/history")
async def booking_history(user_id: UserDep, engine: EngineDep) -> list[dict[str, Any]]:
    return [booking_json(b) for b in await engine.history(user_id)]


@router.get("/{booking_id}/status")
async def booking_status(booking_id: uuid.UUID, user_id: UserDep, engine: EngineDep) -> dict[str, Any]:
    try:
        booking, events = await engine.get_booking(booking_id, user_id)
    except BookingRefused as exc:
        raise _refusal(exc) from exc
    return booking_json(booking, events)
