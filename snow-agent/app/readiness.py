"""Readiness checks: dependencies a service needs before it should receive traffic."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

Check = Callable[[], Awaitable[None]]


def database_check(engine: AsyncEngine) -> Check:
    async def check() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    return check


async def run_checks(checks: dict[str, Check]) -> tuple[bool, dict[str, str]]:
    """Run every check; return overall readiness and a per-dependency status."""
    results: dict[str, str] = {}
    for name, check in checks.items():
        try:
            await check()
            results[name] = "ok"
        except Exception as exc:
            results[name] = f"unavailable: {type(exc).__name__}"
    return all(value == "ok" for value in results.values()), results
