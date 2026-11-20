"""User preference routes."""

from __future__ import annotations

import re
import uuid
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from redis.exceptions import RedisError

from app.auth import require_user
from app.chat.store import create_redis_client
from app.prefs.registry import list_cities
from app.prefs.service import Mode, PreferenceError, Preferences, PrefsService
from app.prefs.telegram_link import TTL_SECONDS, LinkCodeStore

router = APIRouter()


def _link_store() -> LinkCodeStore:
    return LinkCodeStore(create_redis_client())


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_IATA = re.compile(r"^[A-Z0-9]{2,3}$")
_TELEGRAM_ID = re.compile(r"^-?\d{1,20}$")


class PrefsPatch(BaseModel):
    """Partial update; an explicit null clears budget, email, and telegram_chat_id."""

    model_config = ConfigDict(extra="forbid")

    residence_city: str | None = Field(default=None, min_length=1, max_length=100)
    preferred_airports: list[str] | None = Field(default=None, max_length=10)
    excluded_airlines: list[str] | None = Field(default=None, max_length=20)
    mode: Mode | None = None
    budget_total_gbp: Decimal | None = Field(default=None, ge=0, le=1_000_000)
    email: str | None = Field(default=None, max_length=254)
    telegram_chat_id: str | None = None

    @field_validator("preferred_airports", "excluded_airlines")
    @classmethod
    def _iata_codes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        codes = [item.strip().upper() for item in value]
        if any(not _IATA.match(code) for code in codes):
            raise ValueError("Codes must be 2-3 letter IATA codes.")
        return codes

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        if value is not None and not _EMAIL.match(value):
            raise ValueError("Invalid email address.")
        return value

    @field_validator("telegram_chat_id")
    @classmethod
    def _telegram(cls, value: str | None) -> str | None:
        if value is not None and not _TELEGRAM_ID.match(value):
            raise ValueError("Telegram chat ID must be numeric.")
        return value


_NON_NULLABLE = {"residence_city", "preferred_airports", "excluded_airlines", "mode"}


def prefs_response(prefs: Preferences) -> dict[str, Any]:
    return {
        "residence_city": prefs.residence_city,
        "residence_country": prefs.residence_country,
        "preferred_airports": prefs.preferred_airports,
        "origin_airport": prefs.origin_airport,
        "excluded_airlines": prefs.excluded_airlines,
        "mode": prefs.mode,
        "budget_total_gbp": (
            str(prefs.budget_total_gbp) if prefs.budget_total_gbp is not None else None
        ),
        "email": prefs.email,
        "telegram_chat_id": prefs.telegram_chat_id,
    }


@router.get("/api/user/prefs")
async def get_prefs(
    user_id: Annotated[uuid.UUID, Depends(require_user)],
) -> dict[str, Any]:
    return prefs_response(await PrefsService().get(user_id))


@router.patch("/api/user/prefs")
async def patch_prefs(
    body: PrefsPatch,
    user_id: Annotated[uuid.UUID, Depends(require_user)],
) -> dict[str, Any]:
    changes = body.model_dump(exclude_unset=True)
    null_fields = sorted(key for key in _NON_NULLABLE if key in changes and changes[key] is None)
    if null_fields:
        raise HTTPException(
            status_code=422, detail=f"{null_fields} cannot be null."
        )
    try:
        prefs = await PrefsService().update(user_id, changes)
    except PreferenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return prefs_response(prefs)


@router.post("/api/user/telegram-link")
async def create_telegram_link(
    user_id: Annotated[uuid.UUID, Depends(require_user)],
) -> dict[str, Any]:
    """Issue a single-use code; send it to the bot as ``/start <code>`` to link a chat."""
    try:
        code = await _link_store().create(user_id)
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="Link store is unavailable.") from exc
    return {"code": code, "expires_in_seconds": TTL_SECONDS}


@router.get("/api/user/cities")
async def list_residence_cities(
    user_id: Annotated[uuid.UUID, Depends(require_user)],
) -> list[dict[str, Any]]:
    return [
        {"city": c.name, "country": c.country, "airports": list(c.airports)}
        for c in list_cities()
    ]
