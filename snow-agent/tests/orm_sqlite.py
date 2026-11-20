"""SQLite harness for ORM models that use PostgreSQL-only column types."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import ARRAY, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool


@compiles(JSONB, "sqlite")
def _jsonb_as_json(_type, _compiler, **_kw) -> str:
    return "JSON"


@asynccontextmanager
async def orm_sqlite(*models: type) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a session factory over in-memory SQLite with ``models`` created.

    ARRAY columns are swapped to JSON for the duration and restored afterwards.
    Models may belong to different declarative bases.
    """
    swapped = []
    for model in models:
        for column in model.__table__.columns:
            if isinstance(column.type, ARRAY):
                swapped.append((column, column.type))
                column.type = JSON()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    try:
        by_metadata: dict = {}
        for model in models:
            by_metadata.setdefault(model.metadata, []).append(model.__table__)
        async with engine.begin() as conn:
            for metadata, tables in by_metadata.items():
                await conn.run_sync(metadata.create_all, tables=tables)
        yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    finally:
        await engine.dispose()
        for column, original in swapped:
            column.type = original
