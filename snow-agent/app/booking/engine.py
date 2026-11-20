"""Booking sequence with consent-first ordering and compensating rollback.

Order of effects:
  1. validate (plan draft, price lock, bookable)      -- no side effects
  2. write consent_log + bookings row and COMMIT      -- before any provider call
  3. hold flight -> hold hotel -> authorise payment   -- all reversible
  4. confirm flight -> confirm hotel -> capture payment
Any failure halts the sequence and releases everything already obtained.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.booking.config import BookingSettings
from app.booking.models import Booking, BookingEvent, ConsentLog
from app.booking.providers import (
    FlightProvider,
    HotelProvider,
    PaymentProvider,
    PlanView,
    ProviderError,
    Traveller,
)
from app.db.models.resort import Resort
from app.db.models.trip_plan import TripPlan
from app.plans.service import offer_total_gbp

logger = logging.getLogger(__name__)


class BookingRefused(Exception):
    """The request was rejected before consent; ``status_code`` maps to HTTP."""

    def __init__(self, status_code: int, message: str, blockers: list[str] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.blockers = blockers or []


class Notifier(Protocol):
    async def booking_succeeded(self, booking: Booking, plan: PlanView) -> None: ...
    async def booking_failed(self, booking: Booking, plan: PlanView) -> None: ...


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def load_plan_view(session: AsyncSession, plan_id: uuid.UUID, user_id: uuid.UUID) -> PlanView:
    """Read a draft plan owned by the user; a foreign plan is reported as missing."""
    plan = await session.get(TripPlan, plan_id)
    if plan is None or plan.user_id != user_id:
        raise BookingRefused(404, "Plan not found.")
    flight = offer_total_gbp(plan.flight_option)
    hotel = offer_total_gbp(plan.hotel_option)
    if flight is None or hotel is None or not plan.flight_option or not plan.hotel_option:
        raise BookingRefused(422, "The plan does not carry explicit GBP prices for both flight and hotel.")
    resort = await session.get(Resort, plan.resort_id)
    view = PlanView(
        plan_id=plan.id,
        user_id=user_id,
        resort_name=resort.name if resort else None,
        outbound_date=plan.outbound_date,
        return_date=plan.return_date,
        flight_option=plan.flight_option,
        hotel_option=plan.hotel_option,
        flight_total=flight,
        hotel_total=hotel,
        total=flight + hotel,
        created_at=_aware(plan.created_at),
    )
    if plan.status != "draft":
        raise BookingRefused(409, f"Plan is {plan.status}, not draft.")
    return view


class BookingEngine:
    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
        flight: FlightProvider,
        hotel: HotelProvider,
        payment: PaymentProvider,
        notifier: Notifier,
        settings: BookingSettings,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._flight = flight
        self._hotel = hotel
        self._payment = payment
        self._notifier = notifier
        self._settings = settings
        self._now = now

    # ---- validation -------------------------------------------------------------

    def session(self) -> AsyncSession:
        return self._session_factory()

    def price_lock_expires_at(self, plan: PlanView) -> datetime:
        return plan.created_at + timedelta(minutes=self._settings.price_lock_minutes)

    def blockers(self, plan: PlanView) -> list[str]:
        problems = [*self._flight.blockers(plan), *self._hotel.blockers(plan)]
        if plan.total > self._settings.max_booking_total_gbp:
            problems.append(
                f"Total exceeds the configured booking limit of £{self._settings.max_booking_total_gbp}."
            )
        return problems

    def _validate(self, plan: PlanView) -> None:
        if self._now() >= self.price_lock_expires_at(plan):
            raise BookingRefused(409, "The price lock has expired; create a new plan.")
        problems = self.blockers(plan)
        if problems:
            raise BookingRefused(422, "This plan cannot be booked.", problems)

    # ---- sequence ---------------------------------------------------------------

    async def book(
        self,
        plan: PlanView,
        channel: str,
        payment_method_id: str,
        traveller: Traveller,
    ) -> Booking:
        self._validate(plan)
        booking = await self._record_consent(plan, channel)

        flight_ref = hotel_ref = payment_ref = None
        try:
            flight_ref = await self._flight.hold(plan, traveller)
            await self._advance(booking, "flight_held", flight_ref=flight_ref)
            hotel_ref = await self._hotel.hold(plan, traveller)
            await self._advance(booking, "hotel_held", hotel_ref=hotel_ref)
            payment_ref = await self._payment.authorise(
                plan.total, payment_method_id, f"booking-{booking.id}"
            )
            await self._advance(booking, "payment_authorised", payment_ref=payment_ref)

            flight_reference = await self._flight.confirm(flight_ref, plan.flight_total)
            hotel_confirmation = await self._hotel.confirm(hotel_ref, plan.hotel_total)
            await self._payment.capture(payment_ref)
        except Exception as exc:  # any failure, including unexpected ones, must roll back
            logger.exception("Booking %s failed", booking.id)
            return await self._roll_back(booking, plan, exc, flight_ref, hotel_ref, payment_ref)

        await self._advance(
            booking,
            "booked",
            flight_booking_reference=flight_reference,
            hotel_confirmation=hotel_confirmation,
        )
        await self._mark_plan_booked(plan)
        await self._notify(self._notifier.booking_succeeded, booking, plan)
        return booking

    async def _record_consent(self, plan: PlanView, channel: str) -> Booking:
        async with self._session_factory() as session:
            consent = ConsentLog(
                id=uuid.uuid4(),
                plan_id=plan.plan_id,
                user_id=plan.user_id,
                channel=channel,
                flight_total_gbp=plan.flight_total,
                hotel_total_gbp=plan.hotel_total,
                total_gbp=plan.total,
                price_lock_expires_at=self.price_lock_expires_at(plan),
                plan_snapshot={
                    "resort": plan.resort_name,
                    "outbound_date": plan.outbound_date.isoformat(),
                    "return_date": plan.return_date.isoformat(),
                    "flight_total_gbp": str(plan.flight_total),
                    "hotel_total_gbp": str(plan.hotel_total),
                },
            )
            booking = Booking(
                id=uuid.uuid4(),
                plan_id=plan.plan_id,
                user_id=plan.user_id,
                consent_id=consent.id,
                status="consented",
                needs_manual_review=False,
            )
            session.add_all([consent, booking])
            try:
                await session.flush()
                session.add(BookingEvent(id=uuid.uuid4(), booking_id=booking.id, status="consented"))
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise BookingRefused(409, "A booking already exists for this plan.") from exc
            return booking

    async def _advance(self, booking: Booking, status: str, detail: str | None = None, **fields: Any) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(Booking).where(Booking.id == booking.id).values(status=status, **fields)
            )
            session.add(BookingEvent(id=uuid.uuid4(), booking_id=booking.id, status=status, detail=detail))
            await session.commit()
        booking.status = status
        for key, value in fields.items():
            setattr(booking, key, value)

    async def _roll_back(
        self,
        booking: Booking,
        plan: PlanView,
        cause: Exception,
        flight_ref: str | None,
        hotel_ref: str | None,
        payment_ref: str | None,
    ) -> Booking:
        problems: list[str] = []
        # Undo in reverse order; keep going so one failed undo cannot strand the others.
        steps = [
            ("payment", payment_ref, self._payment.cancel),
            ("hotel", hotel_ref, self._hotel.release),
            ("flight", flight_ref, self._flight.release),
        ]
        for name, ref, undo in steps:
            if ref is None:
                continue
            try:
                await undo(ref)
            except Exception as exc:
                logger.exception("Rollback of %s %s failed", name, ref)
                problems.append(f"{name} {ref} could not be released ({exc})")

        message = str(cause) if isinstance(cause, ProviderError) else "An unexpected error stopped the booking."
        needs_review = bool(problems)
        status = "failed_manual_review" if needs_review else "failed"
        detail = message + ("; " + "; ".join(problems) if problems else "")
        await self._advance(booking, status, detail=detail, error=detail, needs_manual_review=needs_review)
        await self._notify(self._notifier.booking_failed, booking, plan)
        return booking

    async def _mark_plan_booked(self, plan: PlanView) -> None:
        # The single permitted write outside the booking tables: the plan lifecycle flag.
        async with self._session_factory() as session:
            await session.execute(
                update(TripPlan).where(TripPlan.id == plan.plan_id).values(status="booked")
            )
            await session.commit()

    async def _notify(self, send, booking: Booking, plan: PlanView) -> None:
        try:
            await send(booking, plan)
        except Exception:
            # Notification trouble must not undo or hide the booking outcome.
            logger.exception("Booking notification failed for %s", booking.id)

    # ---- reads ------------------------------------------------------------------

    async def get_booking(self, booking_id: uuid.UUID, user_id: uuid.UUID) -> tuple[Booking, list[BookingEvent]]:
        async with self._session_factory() as session:
            booking = await session.get(Booking, booking_id)
            if booking is None or booking.user_id != user_id:
                raise BookingRefused(404, "Booking not found.")
            events = (
                await session.execute(
                    select(BookingEvent).where(BookingEvent.booking_id == booking_id).order_by(BookingEvent.at)
                )
            ).scalars().all()
            return booking, list(events)

    async def count_booked(self) -> int:
        async with self._session_factory() as session:
            return (
                await session.execute(
                    select(func.count()).select_from(Booking).where(Booking.status == "booked")
                )
            ).scalar_one()

    async def history(self, user_id: uuid.UUID) -> list[Booking]:
        async with self._session_factory() as session:
            rows = await session.execute(
                select(Booking)
                .where(Booking.user_id == user_id, Booking.status == "booked")
                .order_by(Booking.created_at.desc())
            )
            return list(rows.scalars().all())
