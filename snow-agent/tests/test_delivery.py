from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace

import pytest

from app.db.models.weekly_run import WeeklyRun
from app.delivery.email_sender import SendGridDelivery
from app.delivery.service import DeliveryService
from app.delivery.telegram_bot import TelegramDelivery
from app.settings import settings


class FakeResult:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def all(self):
        return self.rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self.rows)


class FakeSession:
    def __init__(self, run, scores=(), signals=()):
        self.run = run
        self.scores = list(scores)
        self.signals = list(signals)
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None

    async def get(self, model, item_id):
        return self.run

    async def execute(self, query):
        query_text = str(query)
        if "qualitative_signals" in query_text:
            return FakeResult(self.signals)
        return FakeResult(self.scores)

    async def commit(self):
        self.committed = True


@pytest.fixture
def delivery_settings(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "telegram-test-token")
    monkeypatch.setattr(settings, "telegram_chat_id", "chat-123")
    monkeypatch.setattr(settings, "sendgrid_api_key", "sendgrid-test-key")
    monkeypatch.setattr(settings, "delivery_email_address", "user@example.test")
    monkeypatch.setattr(settings, "sendgrid_from_email", "reports@example.test")


async def test_telegram_report_contains_ranked_snow_details(
    delivery_settings, monkeypatch
):
    sent = {}

    class FakeBot:
        def __init__(self, token):
            assert token == "telegram-test-token"

        async def send_message(self, **kwargs):
            sent.update(kwargs)

    delivery = TelegramDelivery(bot_factory=FakeBot)
    top5 = [
        {
            "name": "Zermatt",
            "country": "CH",
            "depth_cm": 80,
            "score_total": 92,
            "historical_reliability_pct": 85,
            "summary": "Fresh snow reported.",
        }
    ]

    assert await delivery.send_weekly_report(top5, uuid.uuid4())
    assert sent["chat_id"] == "chat-123"
    assert "Zermatt, CH" in sent["text"]
    assert "80cm" in sent["text"]
    assert "92/100" in sent["text"]
    assert "Fresh snow reported." in sent["text"]


async def test_email_report_contains_all_resorts_and_score_breakdown(
    delivery_settings, monkeypatch
):
    delivery = SendGridDelivery()
    captured = {}

    def fake_send(mail):
        captured["mail"] = mail
        return SimpleNamespace(status_code=202)

    monkeypatch.setattr(delivery, "_send", fake_send)
    resorts = [
        {
            "name": f"Resort {index}",
            "country": "AT",
            "depth_cm": 40 + index,
            "score_total": 80 + index,
            "score_depth": 30,
            "score_forecast": 25,
            "score_historical": 20,
            "score_qualitative": 10,
            "historical_reliability_pct": 80,
            "summary": "Conditions are stable.",
        }
        for index in range(5)
    ]

    assert await delivery.send_weekly_report(resorts, uuid.uuid4())
    html = captured["mail"].get()["content"][0]["value"]
    for resort in resorts:
        assert resort["name"] in html
    assert "Forecast score" in html
    assert "Qualitative score" in html


async def test_delivery_skips_email_when_telegram_fails(delivery_settings):
    run_id = uuid.uuid4()
    run = WeeklyRun(
        id=run_id,
        run_date=date.today(),
        mode="sports",
        status="complete",
        error_log=None,
    )
    resort = SimpleNamespace(id=uuid.uuid4(), name="Zermatt", country="CH")
    score = SimpleNamespace(
        resort_id=resort.id,
        depth_cm=80,
        score_total=90,
        score_depth=30,
        score_forecast=30,
        score_historical=20,
        score_qualitative=10,
        historical_reliability_pct=85,
        rank=1,
        passed_gate=True,
    )
    signal = SimpleNamespace(resort_id=resort.id, summary="Great snow.")
    session = FakeSession(run, [(score, resort)], [signal])
    events = []

    class FakeTelegram:
        last_error = "Telegram unavailable"

        async def send_weekly_report(self, top5, run_id):
            events.append(("telegram", top5[0]["summary"]))
            return False

        async def send_failure_notification(self, failed_components, run_id):
            events.append(("telegram_failure", failed_components))
            return True

    class FakeEmail:
        last_error = None

        async def send_weekly_report(self, top5, run_id):
            events.append(("email", top5[0]["summary"]))
            return True

        async def send_failure_notification(self, failed_components, run_id):
            events.append(("email_failure", failed_components))
            return True

    service = DeliveryService(
        telegram=FakeTelegram(),
        email=FakeEmail(),
        session_factory=lambda: session,
    )

    results = await service.dispatch(run_id, success=True)

    assert events == [("telegram", "Great snow.")]
    assert results == {"telegram": False}
    assert session.run.error_log["delivery"] == {
        "telegram": "Telegram unavailable"
    }
    assert session.committed


async def test_failure_notification_is_sent_to_both_channels():
    run_id = uuid.uuid4()
    run = WeeklyRun(
        id=run_id,
        run_date=date.today(),
        mode="sports",
        status="failed",
        error_log={"open_meteo": "timeout"},
    )
    session = FakeSession(run)
    delivered = []

    class FakeChannel:
        last_error = None

        async def send_failure_notification(self, failures, weekly_run_id):
            delivered.append((weekly_run_id, failures))
            return True

    service = DeliveryService(
        telegram=FakeChannel(),
        email=FakeChannel(),
        session_factory=lambda: session,
    )

    results = await service.dispatch(run_id, success=False)

    assert results == {"telegram": True, "email": True}
    assert len(delivered) == 2
    assert all(item[1] == {"open_meteo": "timeout"} for item in delivered)
