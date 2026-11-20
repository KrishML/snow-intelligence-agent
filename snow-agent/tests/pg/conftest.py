"""PostgreSQL-only tests: skipped unless TEST_DATABASE_URL points at a scratch database.

The database name must contain "test": the fixture runs ``alembic downgrade base``
followed by ``upgrade head`` on it, which destroys existing data.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    name = make_url(TEST_DATABASE_URL).database or ""
    if "test" not in name:
        pytest.fail(f"Refusing to reset database {name!r}: its name must contain 'test'.")
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    for command in (["downgrade", "base"], ["upgrade", "head"]):
        subprocess.run(
            [sys.executable, "-m", "alembic", *command],
            cwd=ROOT, env=env, check=True, capture_output=True, text=True,
        )
    return TEST_DATABASE_URL


@pytest_asyncio.fixture
async def factory(migrated_database) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    await engine.dispose()
