"""Traveller preference storage with London defaults when no record exists."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user_prefs import UserPrefs
from app.db.session import AsyncSessionLocal
from app.prefs.registry import DEFAULT_CITY, get_city
from app.settings import settings

Mode = Literal["sports", "tourism"]


class PreferenceError(ValueError):
    """Raised when a preference change is inconsistent with the city registry."""


@dataclass
class Preferences:
    user_id: uuid.UUID
    residence_city: str
    residence_country: str
    preferred_airports: list[str]
    excluded_airlines: list[str] = field(default_factory=list)
    mode: Mode = "sports"
    budget_total_gbp: Decimal | None = None
    email: str | None = None
    telegram_chat_id: str | None = None

    @property
    def origin_airport(self) -> str:
        return self.preferred_airports[0]


def default_preferences(user_id: uuid.UUID) -> Preferences:
    city = get_city(DEFAULT_CITY)
    assert city is not None
    return Preferences(
        user_id=user_id,
        residence_city=city.name,
        residence_country=city.country,
        preferred_airports=list(city.airports),
        mode=settings.default_mode,
    )


def _from_row(row: UserPrefs) -> Preferences:
    return Preferences(
        user_id=row.user_id,
        residence_city=row.residence_city,
        residence_country=row.residence_country,
        preferred_airports=list(row.preferred_airports),
        excluded_airlines=list(row.excluded_airlines),
        mode=row.mode,  # type: ignore[arg-type]
        budget_total_gbp=row.budget_total_gbp,
        email=row.email,
        telegram_chat_id=row.telegram_chat_id,
    )


class PrefsService:
    def __init__(
        self, session_factory: Callable[[], AsyncSession] | None = None
    ) -> None:
        self._session_factory = session_factory or AsyncSessionLocal

    async def get(self, user_id: uuid.UUID) -> Preferences:
        """Return stored preferences, or defaults without writing a record."""
        async with self._session_factory() as session:
            row = await self._load(session, user_id)
        return _from_row(row) if row else default_preferences(user_id)

    async def update(self, user_id: uuid.UUID, changes: dict[str, Any]) -> Preferences:
        """Validate and apply a partial update, creating the record on first write."""
        async with self._session_factory() as session:
            row = await self._load(session, user_id)
            current = _from_row(row) if row else default_preferences(user_id)
            updated = self._apply(current, changes)
            if row is None:
                row = UserPrefs(id=uuid.uuid4(), user_id=user_id)
                session.add(row)
            row.residence_city = updated.residence_city
            row.residence_country = updated.residence_country
            row.preferred_airports = updated.preferred_airports
            row.excluded_airlines = updated.excluded_airlines
            row.mode = updated.mode
            row.budget_total_gbp = updated.budget_total_gbp
            row.email = updated.email
            row.telegram_chat_id = updated.telegram_chat_id
            await session.commit()
        return updated

    @staticmethod
    async def _load(session: AsyncSession, user_id: uuid.UUID) -> UserPrefs | None:
        result = await session.execute(
            select(UserPrefs).where(UserPrefs.user_id == user_id)
        )
        return result.scalars().first()

    @staticmethod
    def _apply(current: Preferences, changes: dict[str, Any]) -> Preferences:
        data = dict(vars(current))
        data.update(changes)

        city = get_city(data["residence_city"])
        if city is None:
            raise PreferenceError(
                f"Unsupported residence city: {data['residence_city']!r}."
            )
        data["residence_city"] = city.name
        data["residence_country"] = city.country

        if "preferred_airports" not in changes:
            # Keep the current choice only while it is still valid for the city.
            if "residence_city" in changes and not set(
                data["preferred_airports"]
            ) <= set(city.airports):
                data["preferred_airports"] = list(city.airports)
        invalid = [code for code in data["preferred_airports"] if code not in city.airports]
        if invalid:
            raise PreferenceError(
                f"Airports {invalid} are not valid for {city.name}; "
                f"choose from {list(city.airports)}."
            )
        if not data["preferred_airports"]:
            raise PreferenceError("At least one preferred airport is required.")
        if len(set(data["preferred_airports"])) != len(data["preferred_airports"]):
            raise PreferenceError("Preferred airports must not repeat.")
        return Preferences(**data)
