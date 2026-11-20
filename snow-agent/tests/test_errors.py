from __future__ import annotations

import httpx
import pytest

from app.clients.official_report import OfficialReportClient
from app.clients.rss import RSSClient
from app.utils import describe_error
from scripts.smoke_live import _is_placeholder


@pytest.mark.parametrize(
    "exc, expected",
    [
        (httpx.ReadTimeout(""), "ReadTimeout"),
        (httpx.ConnectTimeout(""), "ConnectTimeout"),
        (ValueError("bad value"), "ValueError: bad value"),
        (KeyError(), "KeyError"),
    ],
)
def test_describe_error_is_never_empty(exc, expected):
    assert describe_error(exc) == expected
    assert describe_error(exc)


async def test_rss_timeout_is_recorded_not_silently_empty(monkeypatch):
    """An unreachable feed must surface as an error, not as 'no entries'."""
    real = httpx.AsyncClient

    def timeout(request):
        raise httpx.ReadTimeout("")

    monkeypatch.setattr(
        "app.clients.rss.httpx.AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(timeout), **kw),
    )
    client = RSSClient()

    assert await client.fetch_entries("Zermatt") == []
    assert client.last_error == "ReadTimeout"
    assert await client.fetch_travel_entries("Zermatt", ["https://blog.test/feed"]) == []
    assert "ReadTimeout" in client.last_error


async def test_official_report_timeout_is_recorded(monkeypatch):
    real = httpx.AsyncClient

    def timeout(request):
        raise httpx.ConnectTimeout("")

    monkeypatch.setattr(
        "app.clients.official_report.httpx.AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(timeout), **kw),
    )
    client = OfficialReportClient({"Zermatt": "https://resort.test/report"})

    assert await client.fetch_report("Zermatt") is None
    assert client.last_error == "ConnectTimeout"


@pytest.mark.parametrize("value", ["sk-...", "SG.", "sk-", "your-key-here"])
def test_example_env_placeholders_are_detected(value):
    assert _is_placeholder(value)


@pytest.mark.parametrize("value", ["sk-proj-abc123", "SG.abcdef.ghijkl", "123456:ABC-token"])
def test_real_looking_credentials_are_not_placeholders(value):
    assert not _is_placeholder(value)
