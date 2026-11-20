from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.booking.config import BookingSettings
from app.booking.engine import BookingRefused
from app.booking.providers import PlanView
from app.booking.telegram import TelegramBookingHandler, parse_traveller

CHAT = "12345"
PLAN_ID = uuid.uuid4()
PROFILE = json.dumps(
    {
        "given_name": "Ada", "family_name": "Lovelace", "born_on": "1990-01-01",
        "email": "ada@example.com", "phone_number": "+447700900000",
    }
)


def _settings(**over):
    base = dict(
        telegram_chat_id=CHAT, telegram_booking_enabled=True, telegram_booking_min_bookings=4,
        telegram_booking_payment_method_id="pm_saved1", telegram_booking_traveller_json=PROFILE,
    )
    return BookingSettings(**{**base, **over})


class Clock:
    def __init__(self):
        self.value = datetime.now(timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, minutes):
        self.value += timedelta(minutes=minutes)


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


class FakeEngine:
    def __init__(self, clock, booked=4, blockers=(), status="booked"):
        self.clock, self.booked, self._blockers, self.status = clock, booked, list(blockers), status
        self.book_calls = []

    def session(self):
        return FakeSession()

    async def count_booked(self):
        return self.booked

    def price_lock_expires_at(self, plan):
        return plan.created_at + timedelta(minutes=10)

    def blockers(self, plan):
        return self._blockers

    async def book(self, plan, channel, payment_method_id, traveller):
        self.book_calls.append((channel, payment_method_id, traveller.given_name))
        return type(
            "B", (), {
                "status": self.status, "flight_booking_reference": "ABC123",
                "hotel_confirmation": "H-1", "error": "hotel hold failed", "needs_manual_review": False,
            },
        )()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def plan(clock, monkeypatch):
    view = PlanView(
        plan_id=PLAN_ID, user_id=uuid.uuid4(), resort_name="Kitzbuhel", outbound_date=date(2026, 12, 4),
        return_date=date(2026, 12, 6), flight_option={}, hotel_option={}, flight_total=120,
        total=420, hotel_total=300, created_at=clock.value,
    )

    async def fake_load(session, plan_id, user_id):
        if plan_id != PLAN_ID:
            raise BookingRefused(404, "Plan not found.")
        return view

    monkeypatch.setattr("app.booking.telegram.load_plan_view", fake_load)
    return view


def handler(clock, engine=None, **settings):
    engine = engine or FakeEngine(clock)
    return TelegramBookingHandler(engine, _settings(**settings), now=clock), engine


async def test_book_is_read_only_and_itemises_cost(clock, plan):
    bot, engine = handler(clock)
    reply = await bot.handle(CHAT, f"/book {PLAN_ID}")

    assert "Flight: £120" in reply and "Total: £420" in reply and "Nothing is booked" in reply
    assert engine.book_calls == []


async def test_confirm_without_prior_book_is_rejected(clock, plan):
    bot, engine = handler(clock)
    reply = await bot.handle(CHAT, f"/confirm {PLAN_ID}")
    assert "/book" in reply and engine.book_calls == []


async def test_confirm_after_book_uses_the_engine_with_telegram_channel_once(clock, plan):
    bot, engine = handler(clock)
    await bot.handle(CHAT, f"/book {PLAN_ID}")

    reply = await bot.handle(CHAT, f"/confirm {PLAN_ID}")

    assert reply.startswith("Booked") and "ABC123" in reply
    assert engine.book_calls == [("telegram", "pm_saved1", "Ada")]
    # The window is consumed: a repeated /confirm cannot book twice.
    again = await bot.handle(CHAT, f"/confirm {PLAN_ID}")
    assert "/book" in again and len(engine.book_calls) == 1


async def test_confirm_for_a_different_plan_is_rejected(clock, plan):
    bot, engine = handler(clock)
    await bot.handle(CHAT, f"/book {PLAN_ID}")
    other = uuid.uuid4()
    assert "/book" in await bot.handle(CHAT, f"/confirm {other}")
    assert engine.book_calls == []


async def test_confirmation_window_expires(clock, plan):
    bot, engine = handler(clock, telegram_confirm_window_minutes=2)
    await bot.handle(CHAT, f"/book {PLAN_ID}")
    clock.advance(3)
    assert "expired" in await bot.handle(CHAT, f"/confirm {PLAN_ID}")
    assert engine.book_calls == []


async def test_window_never_outlives_the_price_lock(clock, plan):
    bot, engine = handler(clock, telegram_confirm_window_minutes=60)
    await bot.handle(CHAT, f"/book {PLAN_ID}")
    clock.advance(11)
    assert "expired" in await bot.handle(CHAT, f"/confirm {PLAN_ID}")
    assert engine.book_calls == []


async def test_book_after_lock_expiry_or_with_blockers_is_refused(clock, plan):
    bot, _ = handler(clock)
    clock.advance(11)
    assert "expired" in await bot.handle(CHAT, f"/book {PLAN_ID}")

    fresh = Clock()
    blocked, engine = handler(fresh, FakeEngine(fresh, blockers=["No hotel provider."]))
    reply = await blocked.handle(CHAT, f"/book {PLAN_ID}")
    assert "No hotel provider." in reply
    assert "/confirm" not in reply


async def test_failed_booking_is_reported_and_not_retried(clock, plan):
    bot, engine = handler(clock, FakeEngine(clock, status="failed"))
    await bot.handle(CHAT, f"/book {PLAN_ID}")
    reply = await bot.handle(CHAT, f"/confirm {PLAN_ID}")
    assert "failed" in reply and "hotel hold failed" in reply and "Nothing was retried" in reply
    assert len(engine.book_calls) == 1


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"telegram_booking_enabled": False}, "not enabled"),
        ({"telegram_booking_payment_method_id": ""}, "saved payment method"),
        ({"telegram_booking_traveller_json": "not json"}, "traveller profile"),
    ],
)
async def test_handler_refuses_when_not_configured(clock, plan, kwargs, expected):
    bot, engine = handler(clock, **kwargs)
    assert expected in await bot.handle(CHAT, f"/book {PLAN_ID}")
    assert engine.book_calls == []


async def test_telegram_booking_unlocks_only_after_enough_web_bookings(clock, plan):
    bot, _ = handler(clock, FakeEngine(clock, booked=3))
    assert "unlocks after 4" in await bot.handle(CHAT, f"/book {PLAN_ID}")


async def test_only_the_configured_chat_may_book_and_input_is_validated(clock, plan):
    bot, engine = handler(clock)
    assert "not authorised" in await bot.handle("999", f"/book {PLAN_ID}")
    assert "not authorised" in await TelegramBookingHandler(
        engine, _settings(telegram_chat_id=""), now=clock
    ).handle("", f"/book {PLAN_ID}")
    assert "/book" in await bot.handle(CHAT, "hello")
    assert "not a valid plan ID" in await bot.handle(CHAT, "/book nope")
    assert "not found" in (await bot.handle(CHAT, f"/book {uuid.uuid4()}")).lower()


def test_parse_traveller():
    assert parse_traveller(PROFILE).family_name == "Lovelace"
    assert parse_traveller("") is None
    assert parse_traveller(json.dumps({"given_name": "A"})) is None
