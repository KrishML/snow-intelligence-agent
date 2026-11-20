from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.booking import api as booking_api
from app.booking.config import BookingSettings
from app.booking.engine import BookingEngine
from app.booking.main import create_app
from app.booking.models import Booking, BookingEvent, ConsentLog
from app.booking.providers import (
    DuffelFlightProvider,
    PlanView,
    ProviderError,
    StripePaymentProvider,
    Traveller,
    UnconfiguredHotelProvider,
)
from app.chat.tools import create_chat_tools
from app.db.models.resort import Resort
from app.db.models.trip_plan import TripPlan
from app.isolation import IsolationError, assert_booking_environment, assert_chat_environment
from app.prefs.service import default_preferences
from app.settings import settings as main_settings
from tests.factories import make_resort
from tests.orm_sqlite import orm_sqlite

USER = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER = uuid.UUID("00000000-0000-0000-0000-000000000002")
HEADERS = {"Authorization": "Bearer booking-token"}
OUT = date.today() + timedelta(days=10)
BACK = OUT + timedelta(days=2)
TRAVELLER = {
    "given_name": "Ada", "family_name": "Lovelace", "born_on": "1990-01-01",
    "email": "ada@example.com", "phone_number": "+447700900000",
}
CONFIRM = {"user_confirmed": True, "payment_method_id": "pm_card_visa", "traveller": TRAVELLER}


# --- fakes -----------------------------------------------------------------------------


class Calls(list):
    pass


class FakeProvider:
    """Records calls; ``fail`` names the operations that raise."""

    def __init__(self, name, calls, fail=(), blockers=(), on_call=None):
        self.name, self.calls, self.fail = name, calls, set(fail)
        self._blockers, self._on_call = list(blockers), on_call

    def blockers(self, plan):
        return list(self._blockers)

    async def _do(self, op, result):
        self.calls.append(f"{self.name}.{op}")
        if self._on_call:
            await self._on_call(f"{self.name}.{op}")
        if op in self.fail:
            raise ProviderError(f"{self.name} {op} failed")
        return result

    async def hold(self, plan, traveller):
        return await self._do("hold", f"{self.name}-ref")

    async def confirm(self, ref, amount):
        return await self._do("confirm", f"{self.name}-confirmation")

    async def release(self, ref):
        return await self._do("release", None)

    async def authorise(self, amount, payment_method_id, idempotency_key):
        return await self._do("authorise", "pi_ref")

    async def capture(self, ref):
        return await self._do("capture", None)

    async def cancel(self, ref):
        return await self._do("cancel", None)


class FakeNotifier:
    def __init__(self):
        self.events = []

    async def booking_succeeded(self, booking, plan):
        self.events.append(("ok", booking.status))

    async def booking_failed(self, booking, plan):
        self.events.append(("failed", booking.status))


@pytest_asyncio.fixture
async def factory():
    async with orm_sqlite(Resort, TripPlan, ConsentLog, Booking, BookingEvent) as f:
        yield f


@pytest.fixture
def settings():
    return BookingSettings(booking_api_token="booking-token", duffel_api_key="d", stripe_secret_key="s")


def make_engine(factory, settings, calls, *, flight=None, hotel=None, payment=None, notifier=None):
    notifier = notifier or FakeNotifier()
    engine = BookingEngine(
        factory,
        flight or FakeProvider("flight", calls),
        hotel or FakeProvider("hotel", calls),
        payment or FakeProvider("payment", calls),
        notifier,
        settings,
    )
    return engine, notifier


async def seed_plan(factory, *, age_minutes=1, user=USER, flight_total="120.00", hotel_total="300.00"):
    resort = make_resort()
    plan = TripPlan(
        id=uuid.uuid4(), user_id=user, session_id="s", resort_id=resort.id,
        outbound_date=OUT, return_date=BACK,
        flight_option={"currency": "GBP", "total_gbp": flight_total, "duffel_offer_id": "off_1", "passengers": [{"id": "pas_1"}]},
        hotel_option={"currency": "GBP", "total_gbp": hotel_total},
        total_estimated_gbp=Decimal(flight_total) + Decimal(hotel_total), status="draft",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=age_minutes),
    )
    async with factory() as session:
        session.add_all([resort, plan])
        await session.commit()
    return plan


@pytest_asyncio.fixture
async def client_for(factory, settings, monkeypatch):
    monkeypatch.setattr(booking_api, "booking_settings", settings)
    clients = []

    async def _make(engine):
        app = create_app(engine)
        app.state.engine = engine
        http = AsyncClient(transport=ASGITransport(app=app), base_url="http://booking")
        clients.append(http)
        return http

    yield _make
    for http in clients:
        await http.aclose()


async def counts(factory):
    async with factory() as session:
        consent = (await session.execute(select(func.count()).select_from(ConsentLog))).scalar()
        bookings = (await session.execute(select(func.count()).select_from(Booking))).scalar()
    return consent, bookings


# --- consent ordering ------------------------------------------------------------------


async def test_consent_logged_before_first_provider_call(factory, settings, client_for):
    plan = await seed_plan(factory)
    seen = []

    async def check(op):
        seen.append((op, (await counts(factory))[0]))

    calls = Calls()
    engine, _ = make_engine(
        factory, settings, calls,
        flight=FakeProvider("flight", calls, on_call=check),
        hotel=FakeProvider("hotel", calls, on_call=check),
        payment=FakeProvider("payment", calls, on_call=check),
    )
    client = await client_for(engine)

    response = await client.post(f"/booking/plans/{plan.id}/confirm", json=CONFIRM, headers=HEADERS)

    assert response.status_code == 201
    assert seen and all(consent_rows == 1 for _, consent_rows in seen)
    async with factory() as session:
        consent = (await session.execute(select(ConsentLog))).scalars().one()
    assert consent.channel == "web" and consent.total_gbp == Decimal("420.00")
    assert consent.plan_snapshot["flight_total_gbp"] == "120.00"


async def test_successful_sequence_order_status_history_and_plan_flag(factory, settings, client_for):
    plan = await seed_plan(factory)
    calls = Calls()
    engine, notifier = make_engine(factory, settings, calls)
    client = await client_for(engine)

    response = await client.post(f"/booking/plans/{plan.id}/confirm", json=CONFIRM, headers=HEADERS)
    body = response.json()

    assert calls == [
        "flight.hold", "hotel.hold", "payment.authorise",
        "flight.confirm", "hotel.confirm", "payment.capture",
    ]
    assert body["status"] == "booked"
    assert body["flight_booking_reference"] == "flight-confirmation"
    assert notifier.events == [("ok", "booked")]
    async with factory() as session:
        assert (await session.get(TripPlan, plan.id)).status == "booked"

    status = (await client.get(f"/booking/{body['booking_id']}/status", headers=HEADERS)).json()
    assert [e["status"] for e in status["events"]] == [
        "consented", "flight_held", "hotel_held", "payment_authorised", "booked",
    ]
    history = (await client.get("/booking/history", headers=HEADERS)).json()
    assert [h["booking_id"] for h in history] == [body["booking_id"]]


# --- failure handling ------------------------------------------------------------------


@pytest.mark.parametrize(
    "fail, expected_calls",
    [
        ({"flight": ["hold"]}, ["flight.hold"]),
        ({"hotel": ["hold"]}, ["flight.hold", "hotel.hold", "flight.release"]),
        (
            {"payment": ["authorise"]},
            ["flight.hold", "hotel.hold", "payment.authorise", "hotel.release", "flight.release"],
        ),
        (
            {"flight": ["confirm"]},
            ["flight.hold", "hotel.hold", "payment.authorise", "flight.confirm",
             "payment.cancel", "hotel.release", "flight.release"],
        ),
        (
            {"payment": ["capture"]},
            ["flight.hold", "hotel.hold", "payment.authorise", "flight.confirm", "hotel.confirm",
             "payment.capture", "payment.cancel", "hotel.release", "flight.release"],
        ),
    ],
)
async def test_failure_halts_sequence_and_releases_everything(
    factory, settings, client_for, fail, expected_calls
):
    plan = await seed_plan(factory)
    calls = Calls()
    engine, notifier = make_engine(
        factory, settings, calls,
        flight=FakeProvider("flight", calls, fail=fail.get("flight", ())),
        hotel=FakeProvider("hotel", calls, fail=fail.get("hotel", ())),
        payment=FakeProvider("payment", calls, fail=fail.get("payment", ())),
    )
    client = await client_for(engine)

    response = await client.post(f"/booking/plans/{plan.id}/confirm", json=CONFIRM, headers=HEADERS)

    assert response.status_code == 502
    assert response.json()["status"] == "failed"
    assert calls == expected_calls
    assert notifier.events == [("failed", "failed")]
    async with factory() as session:
        assert (await session.get(TripPlan, plan.id)).status == "draft"


async def test_hotel_hold_failure_never_reaches_payment(factory, settings, client_for):
    plan = await seed_plan(factory)
    calls = Calls()
    engine, _ = make_engine(factory, settings, calls, hotel=FakeProvider("hotel", calls, fail=["hold"]))
    client = await client_for(engine)
    await client.post(f"/booking/plans/{plan.id}/confirm", json=CONFIRM, headers=HEADERS)
    assert not any(c.startswith("payment.") for c in calls)


async def test_failed_rollback_is_flagged_for_manual_review(factory, settings, client_for):
    plan = await seed_plan(factory)
    calls = Calls()
    engine, notifier = make_engine(
        factory, settings, calls,
        hotel=FakeProvider("hotel", calls, fail=["hold"]),
        flight=FakeProvider("flight", calls, fail=["release"]),
    )
    client = await client_for(engine)

    body = (await client.post(f"/booking/plans/{plan.id}/confirm", json=CONFIRM, headers=HEADERS)).json()

    assert body["status"] == "failed_manual_review" and body["needs_manual_review"] is True
    assert "could not be released" in body["error"]
    assert notifier.events == [("failed", "failed_manual_review")]
    # A stuck attempt keeps the plan locked: no silent retry.
    again = await client.post(f"/booking/plans/{plan.id}/confirm", json=CONFIRM, headers=HEADERS)
    assert again.status_code == 409


async def test_notification_failure_does_not_change_outcome(factory, settings, client_for):
    class Broken(FakeNotifier):
        async def booking_succeeded(self, booking, plan):
            raise RuntimeError("smtp down")

    plan = await seed_plan(factory)
    engine, _ = make_engine(factory, settings, Calls(), notifier=Broken())
    client = await client_for(engine)
    response = await client.post(f"/booking/plans/{plan.id}/confirm", json=CONFIRM, headers=HEADERS)
    assert response.status_code == 201 and response.json()["status"] == "booked"


# --- refusals before consent -----------------------------------------------------------


async def test_expired_price_lock_blocks_confirm_without_consent_or_provider_calls(
    factory, settings, client_for
):
    plan = await seed_plan(factory, age_minutes=11)
    calls = Calls()
    engine, _ = make_engine(factory, settings, calls)
    client = await client_for(engine)

    response = await client.post(f"/booking/plans/{plan.id}/confirm", json=CONFIRM, headers=HEADERS)

    assert response.status_code == 409
    assert calls == []
    assert await counts(factory) == (0, 0)
    summary = await client.get(f"/booking/plans/{plan.id}/summary", headers=HEADERS)
    assert summary.status_code == 409


async def test_unbookable_plan_is_refused_with_blockers_before_consent(factory, settings, client_for):
    plan = await seed_plan(factory)
    calls = Calls()
    engine, _ = make_engine(
        factory, settings, calls,
        flight=FakeProvider("flight", calls, blockers=["No Duffel offer ID."]),
        hotel=UnconfiguredHotelProvider(),
    )
    client = await client_for(engine)

    response = await client.post(f"/booking/plans/{plan.id}/confirm", json=CONFIRM, headers=HEADERS)

    assert response.status_code == 422
    blockers = response.json()["detail"]["blockers"]
    assert "No Duffel offer ID." in blockers and any("hotel" in b.lower() for b in blockers)
    assert calls == [] and await counts(factory) == (0, 0)


async def test_total_over_limit_is_blocked(factory, settings, client_for):
    plan = await seed_plan(factory, flight_total="4000.00", hotel_total="1500.00")
    engine, _ = make_engine(factory, settings, Calls())
    client = await client_for(engine)
    response = await client.post(f"/booking/plans/{plan.id}/confirm", json=CONFIRM, headers=HEADERS)
    assert response.status_code == 422
    assert any("limit" in b for b in response.json()["detail"]["blockers"])


@pytest.mark.parametrize(
    "mutation",
    [
        {"user_confirmed": False},
        {"user_confirmed": "true"},
        {"user_confirmed": 1},
        {"payment_method_id": "card_number_4242"},
        {"extra": 1},
        {"traveller": {**TRAVELLER, "born_on": "2999-01-01"}},
        {"traveller": {**TRAVELLER, "phone_number": "07700900000"}},
    ],
)
async def test_confirm_requires_explicit_boolean_true_and_valid_body(factory, settings, client_for, mutation):
    plan = await seed_plan(factory)
    calls = Calls()
    engine, _ = make_engine(factory, settings, calls)
    client = await client_for(engine)

    response = await client.post(
        f"/booking/plans/{plan.id}/confirm", json={**CONFIRM, **mutation}, headers=HEADERS
    )

    assert response.status_code == 422
    assert calls == [] and await counts(factory) == (0, 0)


async def test_auth_ownership_and_double_booking(factory, settings, client_for):
    mine, theirs = await seed_plan(factory), await seed_plan(factory, user=OTHER)
    engine, _ = make_engine(factory, settings, Calls())
    client = await client_for(engine)

    assert (await client.post(f"/booking/plans/{mine.id}/confirm", json=CONFIRM)).status_code == 401
    bad = {"Authorization": "Bearer wrong"}
    assert (await client.get(f"/booking/plans/{mine.id}/summary", headers=bad)).status_code == 401
    assert (await client.post(f"/booking/plans/{theirs.id}/confirm", json=CONFIRM, headers=HEADERS)).status_code == 404
    assert (await client.post(f"/booking/plans/{mine.id}/confirm", json=CONFIRM, headers=HEADERS)).status_code == 201
    assert (await client.post(f"/booking/plans/{mine.id}/confirm", json=CONFIRM, headers=HEADERS)).status_code == 409
    assert (await client.get(f"/booking/{uuid.uuid4()}/status", headers=HEADERS)).status_code == 404


async def test_summary_itemises_cost_and_reports_blockers(factory, settings, client_for):
    plan = await seed_plan(factory)
    calls = Calls()
    engine, _ = make_engine(factory, settings, calls, hotel=UnconfiguredHotelProvider())
    client = await client_for(engine)

    body = (await client.get(f"/booking/plans/{plan.id}/summary", headers=HEADERS)).json()

    assert body["items"] == [
        {"label": "Flight", "amount": "120.00"}, {"label": "Accommodation", "amount": "300.00"},
    ]
    assert body["total"] == "420.00" and body["bookable"] is False
    assert 0 < body["seconds_remaining"] <= 600
    assert calls == [] and await counts(factory) == (0, 0)


# --- providers -------------------------------------------------------------------------


def _plan_view(**flight_overrides):
    flight = {"duffel_offer_id": "off_123", "passengers": [{"id": "pas_9"}], **flight_overrides}
    return PlanView(
        plan_id=uuid.uuid4(), user_id=USER, resort_name="X", outbound_date=OUT, return_date=BACK,
        flight_option=flight, hotel_option={}, flight_total=Decimal("120"), hotel_total=Decimal("300"),
        total=Decimal("420"), created_at=datetime.now(timezone.utc),
    )


def test_duffel_blockers_require_a_duffel_offer(settings):
    provider = DuffelFlightProvider(settings)
    assert provider.blockers(_plan_view()) == []
    assert any("Duffel offer ID" in b for b in provider.blockers(_plan_view(duffel_offer_id="amadeus-1")))
    assert any("passenger" in b for b in provider.blockers(_plan_view(passengers=[])))
    assert any("not configured" in b for b in DuffelFlightProvider(BookingSettings()).blockers(_plan_view()))


async def test_duffel_hold_pay_and_cancel_request_shapes(settings):
    seen = []

    def handler(request: httpx.Request):
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body, request.headers))
        path = request.url.path
        if path == "/air/orders":
            return httpx.Response(201, json={"data": {"id": "ord_1"}})
        if path == "/air/payments":
            return httpx.Response(201, json={"data": {"id": "pay_1"}})
        if path == "/air/orders/ord_1":
            return httpx.Response(200, json={"data": {"booking_reference": "ABC123"}})
        if path == "/air/order_cancellations":
            return httpx.Response(201, json={"data": {"id": "ore_1"}})
        return httpx.Response(200, json={"data": {}})

    provider = DuffelFlightProvider(settings, httpx.MockTransport(handler))
    traveller = Traveller("Ada", "Lovelace", date(1990, 1, 1), "ada@example.com", "+447700900000")

    ref = await provider.hold(_plan_view(), traveller)
    reference = await provider.confirm(ref, Decimal("120"))
    await provider.release(ref)

    assert (ref, reference) == ("ord_1", "ABC123")
    method, path, body, headers = seen[0]
    assert (method, path) == ("POST", "/air/orders")
    assert body["data"]["type"] == "hold" and "payments" not in body["data"]
    assert body["data"]["selected_offers"] == ["off_123"]
    assert body["data"]["passengers"][0]["id"] == "pas_9"
    assert headers["Duffel-Version"] == "v2" and headers["Authorization"] == "Bearer d"
    assert seen[1][2]["data"]["payment"] == {"type": "balance", "currency": "GBP", "amount": "120.00"}
    assert [s[1] for s in seen[-2:]] == ["/air/order_cancellations", "/air/order_cancellations/ore_1/actions/confirm"]


async def test_duffel_http_error_becomes_provider_error(settings):
    provider = DuffelFlightProvider(settings, httpx.MockTransport(lambda r: httpx.Response(422, json={})))
    with pytest.raises(ProviderError, match="422"):
        await provider.hold(_plan_view(), Traveller("A", "B", date(1990, 1, 1), "a@b.co", "+447700900000"))


async def test_stripe_authorises_with_manual_capture_and_idempotency(settings):
    seen = []

    def handler(request: httpx.Request):
        seen.append((request.url.path, dict(httpx.QueryParams(request.content.decode())), request.headers))
        return httpx.Response(200, json={"id": "pi_1", "status": "requires_capture"})

    provider = StripePaymentProvider(settings, httpx.MockTransport(handler))
    ref = await provider.authorise(Decimal("420.50"), "pm_card_visa", "booking-123")
    await provider.capture(ref)
    await provider.cancel(ref)

    path, form, headers = seen[0]
    assert ref == "pi_1" and path == "/v1/payment_intents"
    assert form["amount"] == "42050" and form["currency"] == "gbp"
    assert form["capture_method"] == "manual" and form["confirm"] == "true"
    assert form["payment_method"] == "pm_card_visa"
    assert headers["Idempotency-Key"] == "booking-123" and headers["Authorization"] == "Bearer s"
    assert [s[0] for s in seen[1:]] == ["/v1/payment_intents/pi_1/capture", "/v1/payment_intents/pi_1/cancel"]


async def test_stripe_requires_action_is_cancelled_and_refused(settings):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"id": "pi_2", "status": "requires_action"})

    provider = StripePaymentProvider(settings, httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="requires_action"):
        await provider.authorise(Decimal("10"), "pm_x1234", "k")
    assert paths == ["/v1/payment_intents", "/v1/payment_intents/pi_2/cancel"]


async def test_stripe_decline_and_missing_key(settings):
    declined = StripePaymentProvider(
        settings, httpx.MockTransport(lambda r: httpx.Response(402, json={"error": {"message": "Card declined"}}))
    )
    with pytest.raises(ProviderError, match="Card declined"):
        await declined.authorise(Decimal("10"), "pm_x1234", "k")
    with pytest.raises(ProviderError, match="not configured"):
        await StripePaymentProvider(BookingSettings()).capture("pi_1")


# --- isolation (spec 12.2 / 12.3) ------------------------------------------------------


def test_chat_environment_rejects_booking_credentials():
    assert_chat_environment({"OPENAI_API_KEY": "x"})
    for name in ("DUFFEL_API_KEY", "STRIPE_SECRET_KEY", "HOTEL_BOOKING_API_KEY", "BOOKING_API_TOKEN"):
        with pytest.raises(IsolationError, match=name):
            assert_chat_environment({name: "secret"})


def test_booking_environment_rejects_chat_credentials():
    assert_booking_environment({"STRIPE_SECRET_KEY": "x"})
    for name in ("OPENAI_API_KEY", "AMADEUS_CLIENT_ID", "AMADEUS_CLIENT_SECRET", "REDDIT_CLIENT_SECRET"):
        with pytest.raises(IsolationError, match=name):
            assert_booking_environment({name: "secret"})


def test_chat_settings_have_no_booking_credentials():
    for attr in ("duffel_api_key", "stripe_secret_key", "hotel_booking_api_key", "booking_api_token"):
        assert not hasattr(main_settings, attr)
    for attr in ("openai_api_key", "amadeus_client_id"):
        assert not hasattr(BookingSettings(), attr)


def test_booking_tools_absent_from_chat_agent():
    tools = create_chat_tools("s", USER, {"live_calls": 0, "plan_created": False}, default_preferences(USER))
    assert not any(re.search(r"book|pay|confirm|consent", t.name) for t in tools)
    assert len(tools) == 7


def test_nothing_outside_the_booking_package_imports_it():
    app_dir = Path(__file__).resolve().parent.parent / "app"
    offenders = [
        str(path.relative_to(app_dir))
        for path in app_dir.rglob("*.py")
        if "booking" not in path.relative_to(app_dir).parts[:1]
        and re.search(r"^\s*(from|import)\s+app\.booking", path.read_text(), re.MULTILINE)
    ]
    assert offenders == []


def test_chat_code_has_no_booking_route_references():
    app_dir = Path(__file__).resolve().parent.parent / "app"
    for package in ("chat", "plans", "dashboard", "prefs"):
        for path in (app_dir / package).rglob("*.py"):
            assert "/booking" not in path.read_text(), path


def test_consent_log_is_protected_by_append_only_triggers():
    migration = (Path(__file__).resolve().parent.parent / "alembic/versions/0006_booking_tables.py").read_text()
    assert "BEFORE UPDATE OR DELETE ON consent_log" in migration
    assert "BEFORE TRUNCATE ON consent_log" in migration
    assert "consent_log is append-only" in migration


def test_compose_keeps_chat_and_booking_on_disjoint_networks():
    import yaml

    compose = yaml.safe_load((Path(__file__).resolve().parent.parent / "docker-compose.yml").read_text())
    services = compose["services"]
    assert not set(services["app"]["networks"]) & set(services["booking"]["networks"])
    assert services["app"]["env_file"] != services["booking"]["env_file"]
    assert services["booking"]["build"]["dockerfile"] == "Dockerfile.booking"
    # Only the database may sit on both sides.
    shared = [n for n, s in services.items() if {"chat_net", "booking_net"} <= set(s.get("networks", []))]
    assert shared == ["db"]
