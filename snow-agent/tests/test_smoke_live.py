from __future__ import annotations

import argparse

import pytest

from app.settings import Settings
from scripts import smoke_live
from scripts.smoke_live import NotConfigured, Result, exit_code, run_checks, select

ARGS = argparse.Namespace(send=False)


async def _ok(cfg, args):
    return "fine"


async def _boom(cfg, args):
    raise ConnectionError("no route")


async def _unconfigured(cfg, args):
    raise NotConfigured("KEY not set")


async def _slow(cfg, args):
    import asyncio

    await asyncio.sleep(5)


async def test_each_outcome_is_reported_separately():
    checks = {"a": _ok, "b": _boom, "c": _unconfigured}
    results = await run_checks(["a", "b", "c"], Settings(), ARGS, checks)

    assert [(r.name, r.status) for r in results] == [("a", "PASS"), ("b", "FAIL"), ("c", "SKIP")]
    assert "ConnectionError: no route" in results[1].detail
    assert results[2].detail == "KEY not set"


async def test_a_hung_check_fails_instead_of_blocking():
    results = await run_checks(["slow"], Settings(), ARGS, {"slow": _slow}, timeout=0.05)
    assert results[0].status == "FAIL"


def test_exit_code_fails_on_failure_and_only_fails_on_skip_when_strict():
    passed, failed, skipped = Result("a", "PASS", "", 0), Result("b", "FAIL", "", 0), Result("c", "SKIP", "", 0)
    assert exit_code([passed]) == 0
    assert exit_code([passed, skipped]) == 0
    assert exit_code([passed, skipped], strict=True) == 1
    assert exit_code([passed, failed]) == 1


def test_select_filters_and_rejects_unknown_names():
    assert select("open_meteo,rss", None) == ["open_meteo", "rss"]
    assert "reddit" not in select(None, "reddit")
    with pytest.raises(SystemExit):
        select("nonsense", None)


@pytest.mark.parametrize(
    "check, field",
    [
        (smoke_live.check_openai_chat, "OPENAI_API_KEY"),
        (smoke_live.check_openai_embed, "OPENAI_API_KEY"),
        (smoke_live.check_reddit, "REDDIT_CLIENT_ID"),
        (smoke_live.check_telegram, "TELEGRAM_BOT_TOKEN"),
        (smoke_live.check_sendgrid, "SENDGRID_API_KEY"),
    ],
)
async def test_missing_credentials_are_a_skip_not_a_failure(check, field):
    empty = Settings(
        openai_api_key="", reddit_client_id="", reddit_client_secret="",
        telegram_bot_token="", telegram_chat_id="", sendgrid_api_key="", _env_file=None,
    )
    with pytest.raises(NotConfigured, match=field):
        await check(empty, ARGS)


def test_every_check_has_a_registered_name():
    assert set(smoke_live.CHECKS) == {
        "database", "redis", "open_meteo", "open_meteo_archive", "openai_chat",
        "openai_embed", "reddit", "rss", "telegram", "sendgrid",
    }
