"""Call each real external client once and report PASS, FAIL or SKIP.

Usage:
    python -m scripts.smoke_live                  # every check
    python -m scripts.smoke_live --only open_meteo,openai_chat
    python -m scripts.smoke_live --skip reddit --strict
    python -m scripts.smoke_live --send           # also send one real Telegram message and email

By default nothing is sent to a person: Telegram is checked with ``getMe`` and SendGrid
by reading the key's scopes. OpenAI checks make two tiny paid calls.

Exit status is 1 if any check FAILS. A check whose credentials are not configured is a
SKIP, which only fails the run with ``--strict``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.settings import Settings, settings

# Zermatt: a stable test location for the weather checks.
_LAT, _LON = 46.0207, 7.7491


class NotConfigured(Exception):
    """The credentials this check needs are missing."""


@dataclass
class Result:
    name: str
    status: str  # PASS, FAIL or SKIP
    detail: str
    seconds: float


Check = Callable[[Settings, argparse.Namespace], Awaitable[str]]


def _is_placeholder(value: str) -> bool:
    """Values copied unchanged from .env.example, such as ``sk-...`` or ``SG.``."""
    return value.endswith("...") or value in {"SG.", "sk-"} or value.lower().startswith("your")


def _require(**values: str) -> None:
    for name, value in values.items():
        if not value:
            raise NotConfigured(f"{name.upper()} not set")
        if _is_placeholder(value):
            raise NotConfigured(f"{name.upper()} is still the .env.example placeholder")


async def check_database(cfg: Settings, args) -> str:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(cfg.database_url)
    try:
        async with engine.connect() as conn:
            version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
            vector = (
                await conn.execute(text("SELECT extname FROM pg_extension WHERE extname = 'vector'"))
            ).scalar()
    finally:
        await engine.dispose()
    if vector != "vector":
        raise RuntimeError("pgvector extension is not installed")
    return f"migration {version}, pgvector installed"


async def check_redis(cfg: Settings, args) -> str:
    from redis.asyncio import Redis

    client = Redis.from_url(cfg.redis_url)
    try:
        await client.ping()
    finally:
        await client.aclose()
    return "ping ok"


async def check_open_meteo(cfg: Settings, args) -> str:
    from app.clients.open_meteo import OpenMeteoClient

    async with OpenMeteoClient() as client:
        resort_id = uuid.uuid4()
        now = await client.get_current_conditions(resort_id, _LAT, _LON)
        forecast = await client.get_5day_forecast(resort_id, _LAT, _LON)
    return f"depth {now.snow_depth_cm:.0f} cm, {now.temperature_c:.1f} C, {len(forecast.days)} forecast days"


async def check_open_meteo_archive(cfg: Settings, args) -> str:
    from app.clients.open_meteo import OpenMeteoClient

    async with OpenMeteoClient() as client:
        reliability = await client.get_historical_reliability(uuid.uuid4(), _LAT, _LON, "sports")
    return f"5-year reliability {reliability:.0f}%"


async def check_openai_chat(cfg: Settings, args) -> str:
    _require(openai_api_key=cfg.openai_api_key)
    from app.clients.openai_client import OpenAIClient

    reply = await OpenAIClient().complete_json(
        system_prompt='Reply with the JSON object {"ok": true}.',
        user_prompt="ping",
        model=cfg.openai_model_qualitative,
    )
    if reply.get("ok") is not True:
        raise RuntimeError(f"unexpected reply {reply!r}")
    return f"{cfg.openai_model_qualitative} replied"


async def check_openai_embed(cfg: Settings, args) -> str:
    _require(openai_api_key=cfg.openai_api_key)
    from app.clients.openai_client import OpenAIClient

    vector = await OpenAIClient().embed("fresh powder")
    return f"{cfg.openai_embedding_model}: {len(vector)} dimensions"


async def check_reddit(cfg: Settings, args) -> str:
    _require(reddit_client_id=cfg.reddit_client_id, reddit_client_secret=cfg.reddit_client_secret)
    from app.clients.reddit import RedditClient

    client = RedditClient()
    posts = await client.fetch_posts("Zermatt", "sports")
    if client.last_error:
        raise RuntimeError(client.last_error)
    return f"{len(posts)} posts"


async def check_rss(cfg: Settings, args) -> str:
    from app.clients.rss import RSSClient

    client = RSSClient()
    entries = await client.fetch_entries("Zermatt")
    if client.last_error:
        raise RuntimeError(client.last_error)
    return f"{len(entries)} entries"


async def check_telegram(cfg: Settings, args) -> str:
    _require(telegram_bot_token=cfg.telegram_bot_token, telegram_chat_id=cfg.telegram_chat_id)
    from telegram import Bot

    bot = Bot(cfg.telegram_bot_token)
    if args.send:
        await bot.send_message(chat_id=cfg.telegram_chat_id, text="Snow Agent smoke test: Telegram works.")
        return "test message sent"
    me = await bot.get_me()
    return f"bot @{me.username} reachable (no message sent)"


async def check_sendgrid(cfg: Settings, args) -> str:
    _require(sendgrid_api_key=cfg.sendgrid_api_key)
    from sendgrid import SendGridAPIClient

    client = SendGridAPIClient(api_key=cfg.sendgrid_api_key)
    if args.send:
        _require(delivery_email_address=cfg.delivery_email_address)
        from sendgrid.helpers.mail import Mail

        response = await asyncio.to_thread(
            client.send,
            Mail(
                from_email=cfg.sendgrid_from_email,
                to_emails=cfg.delivery_email_address,
                subject="Snow Agent smoke test",
                html_content="<p>SendGrid works.</p>",
            ),
        )
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"HTTP {response.status_code}")
        return "test email sent"
    response = await asyncio.to_thread(client.client.scopes.get)
    if response.status_code != 200:
        raise RuntimeError(f"key rejected (HTTP {response.status_code})")
    return "API key accepted (no email sent)"


CHECKS: dict[str, Check] = {
    "database": check_database,
    "redis": check_redis,
    "open_meteo": check_open_meteo,
    "open_meteo_archive": check_open_meteo_archive,
    "openai_chat": check_openai_chat,
    "openai_embed": check_openai_embed,
    "reddit": check_reddit,
    "rss": check_rss,
    "telegram": check_telegram,
    "sendgrid": check_sendgrid,
}


async def run_checks(
    names: list[str],
    cfg: Settings,
    args: argparse.Namespace,
    checks: dict[str, Check] | None = None,
    timeout: float = 60.0,
) -> list[Result]:
    checks = checks or CHECKS
    results = []
    for name in names:
        started = time.perf_counter()
        try:
            detail = await asyncio.wait_for(checks[name](cfg, args), timeout)
            status = "PASS"
        except NotConfigured as exc:
            status, detail = "SKIP", str(exc)
        except Exception as exc:
            status, detail = "FAIL", f"{type(exc).__name__}: {exc}"[:300]
        results.append(Result(name, status, detail, time.perf_counter() - started))
    return results


def exit_code(results: list[Result], strict: bool = False) -> int:
    if any(r.status == "FAIL" for r in results):
        return 1
    return 1 if strict and any(r.status == "SKIP" for r in results) else 0


def select(only: str | None, skip: str | None, available=CHECKS) -> list[str]:
    names = [n.strip() for n in only.split(",")] if only else list(available)
    unknown = [n for n in names if n not in available]
    if unknown:
        raise SystemExit(f"Unknown check(s): {', '.join(unknown)}. Choose from: {', '.join(available)}")
    skipped = {n.strip() for n in skip.split(",")} if skip else set()
    return [n for n in names if n not in skipped]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="comma-separated check names")
    parser.add_argument("--skip", help="comma-separated check names to leave out")
    parser.add_argument("--send", action="store_true", help="really send a Telegram message and an email")
    parser.add_argument("--strict", action="store_true", help="treat SKIP as a failure")
    args = parser.parse_args(argv)

    results = asyncio.run(run_checks(select(args.only, args.skip), settings, args))
    for r in results:
        print(f"{r.status:4s}  {r.name:20s} {r.seconds:5.1f}s  {r.detail}")
    counts = {s: sum(r.status == s for r in results) for s in ("PASS", "FAIL", "SKIP")}
    print(f"\n{counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped")
    return exit_code(results, args.strict)


if __name__ == "__main__":
    sys.exit(main())
