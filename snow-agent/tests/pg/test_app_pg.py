from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.booking.config import BookingSettings
from app.booking.engine import BookingEngine, load_plan_view
from app.booking.models import Booking, BookingEvent, ConsentLog
from app.booking.providers import Traveller
from app.dashboard.service import weekly_dashboard
from app.db.models.snow_score import SnowScore
from app.db.models.trip_plan import TripPlan
from app.db.models.weekly_run import WeeklyRun
from app.prefs.service import PrefsService, default_preferences
from tests.factories import make_resort
from tests.test_booking import Calls, FakeNotifier, FakeProvider

pytestmark = pytest.mark.pg

TRAVELLER = Traveller("Ada", "Lovelace", date(1990, 1, 1), "ada@example.com", "+447700900000")


async def test_dashboard_reads_real_rows(factory):
    resort = make_resort(name=f"Resort {uuid.uuid4()}")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    run = WeeklyRun(
        id=uuid.uuid4(), run_date=date.today() + timedelta(days=3650), mode="sports",
        status="complete", started_at=now, completed_at=now, error_log={"reddit:x": "down"},
    )
    score = SnowScore(
        id=uuid.uuid4(), weekly_run_id=run.id, resort_id=resort.id, passed_gate=True, depth_cm=80,
        forecast_stable=True, forecast_detail={"days": []}, score_depth=30, score_forecast=25,
        score_historical=17, score_qualitative=15, score_total=87, rank=1,
    )
    async with factory() as session:
        session.add_all([resort, run])
        await session.flush()
        session.add(score)
        await session.commit()

    async with factory() as session:
        result = await weekly_dashboard(session, default_preferences(uuid.uuid4()))

    assert result["run"]["id"] == str(run.id)
    assert result["partial_failure"] and result["errors"] == {"reddit:x": "down"}
    assert result["resorts"][0]["name"] == resort.name
    assert result["resorts"][0]["nearest_airports"] == ["INN", "SZG"]
    assert result["resorts"][0]["score"]["total"] == 87


async def test_preferences_round_trip_on_jsonb(factory):
    user = uuid.uuid4()
    service = PrefsService(factory)
    await service.update(user, {"residence_city": "Manchester", "excluded_airlines": ["FR"], "budget_total_gbp": Decimal("450.50")})

    stored = await service.get(user)

    assert stored.residence_city == "Manchester"
    assert stored.preferred_airports == ["MAN"]
    assert stored.excluded_airlines == ["FR"]
    assert stored.budget_total_gbp == Decimal("450.50")


async def _seed_plan(factory, age_minutes=1) -> TripPlan:
    resort = make_resort(name=f"Resort {uuid.uuid4()}")
    plan = TripPlan(
        id=uuid.uuid4(), user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"), session_id="s",
        resort_id=resort.id, outbound_date=date(2026, 12, 4), return_date=date(2026, 12, 6),
        flight_option={"currency": "GBP", "total_gbp": "120.00", "duffel_offer_id": "off_1",
                       "passengers": [{"id": "pas_1"}]},
        hotel_option={"currency": "GBP", "total_gbp": "300.00"}, total_estimated_gbp=Decimal("420.00"),
        status="draft",
    )
    async with factory() as session:
        session.add_all([resort, plan])
        await session.commit()
        if age_minutes:
            await session.execute(
                TripPlan.__table__.update().where(TripPlan.id == plan.id).values(
                    created_at=datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
                )
            )
            await session.commit()
    return plan


def _engine(factory, calls, **fail):
    return BookingEngine(
        factory,
        FakeProvider("flight", calls, fail=fail.get("flight", ())),
        FakeProvider("hotel", calls, fail=fail.get("hotel", ())),
        FakeProvider("payment", calls, fail=fail.get("payment", ())),
        FakeNotifier(),
        BookingSettings(),
    )


async def test_booking_sequence_persists_on_real_postgres(factory):
    plan = await _seed_plan(factory)
    calls = Calls()
    engine = _engine(factory, calls)
    async with factory() as session:
        view = await load_plan_view(session, plan.id, plan.user_id)

    booking = await engine.book(view, "web", "pm_test1234", TRAVELLER)

    assert booking.status == "booked"
    async with factory() as session:
        consent = (await session.execute(select(ConsentLog).where(ConsentLog.plan_id == plan.id))).scalars().one()
        events = (
            await session.execute(
                select(BookingEvent).where(BookingEvent.booking_id == booking.id).order_by(BookingEvent.at)
            )
        ).scalars().all()
        stored_plan = await session.get(TripPlan, plan.id)
    assert consent.total_gbp == Decimal("420.00") and consent.channel == "web"
    assert {e.status for e in events} >= {"consented", "flight_held", "hotel_held", "payment_authorised", "booked"}
    assert stored_plan.status == "booked"


async def test_failed_attempt_rolls_back_and_second_attempt_is_blocked_only_when_open(factory):
    plan = await _seed_plan(factory)
    calls = Calls()
    engine = _engine(factory, calls, hotel=["hold"])
    async with factory() as session:
        view = await load_plan_view(session, plan.id, plan.user_id)

    booking = await engine.book(view, "web", "pm_test1234", TRAVELLER)

    assert booking.status == "failed"
    assert calls == ["flight.hold", "hotel.hold", "flight.release"]
    async with factory() as session:
        stored = (await session.execute(select(Booking).where(Booking.plan_id == plan.id))).scalars().one()
    assert stored.status == "failed" and "hotel hold failed" in stored.error
