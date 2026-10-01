"""
Shared pytest fixtures and helpers for the Snow Intelligence Agent test suite.

Design notes
------------
The ORM models use PostgreSQL-specific types (``ARRAY(String)``, ``JSONB``) that
are not supported by SQLite.  Rather than monkey-patching the ORM, we create a
parallel *SQLite-compatible* ``MetaData`` that mirrors all three tables with
plain SQLite types (``Text``, ``Float``, ``Integer``).  This lets us spin up an
in-memory ``aiosqlite`` database quickly for any test that needs a real DB
session, while still being able to construct genuine ``Resort`` / ``WeeklyRun``
/ ``SnowScore`` ORM objects in memory when we only need Python-level data.

Two key fixtures are exported for other test modules:

``db_session``
    Yields an ``AsyncSession`` backed by an in-memory SQLite database with all
    three tables pre-created.  Rolled back after every test.

``resort_factory``
    Returns a ``ResortFactory`` instance whose ``.build(**kwargs)`` method
    creates in-memory ``Resort`` ORM objects with valid defaults.  Objects are
    **not** added to any session — callers are responsible for that.

Requirements: 29.3, 29.5
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models.resort import Resort

# ---------------------------------------------------------------------------
# SQLite-compatible mirror metadata
# ---------------------------------------------------------------------------
# We cannot use ``Base.metadata.create_all`` directly because SQLAlchemy will
# try to resolve ``ARRAY`` and ``JSONB`` column types, which aiosqlite does not
# support.  Instead we define a plain-types replica that matches the real schema
# in table names, column names, constraints, FK relationships, and indexes.
# ---------------------------------------------------------------------------

_test_meta = MetaData()

_resorts = Table(
    "resorts",
    _test_meta,
    Column("id", Text, primary_key=True, default=lambda: str(uuid.uuid4())),
    Column("name", String(100), nullable=False),
    Column("country", String(50), nullable=False),
    Column("region", String(100)),
    Column("latitude", Float, nullable=False),
    Column("longitude", Float, nullable=False),
    Column("altitude_base_m", Integer, nullable=False),
    Column("altitude_peak_m", Integer),
    Column("mode", String(20), nullable=False),
    Column("nearest_airports", Text, nullable=False),  # ARRAY(String) → Text
    Column("piste_count", Integer),
    Column("lift_count", Integer),
    Column("webcam_url", Text),
    Column("is_active", Boolean, default=True),
    Column("created_at", Text),
    CheckConstraint("mode IN ('sports','tourism','both')", name="ck_resorts_mode"),
)

_weekly_runs = Table(
    "weekly_runs",
    _test_meta,
    Column("id", Text, primary_key=True),
    Column("run_date", Text, nullable=False),
    Column("mode", String(20), nullable=False),
    Column("status", String(20), nullable=False),
    Column("resorts_checked", Integer),
    Column("resorts_passed", Integer),
    Column("error_log", Text),  # JSONB → Text
    Column("started_at", Text),
    Column("completed_at", Text),
    CheckConstraint(
        "status IN ('running','complete','failed')", name="ck_weekly_runs_status"
    ),
)

_snow_scores = Table(
    "snow_scores",
    _test_meta,
    Column("id", Text, primary_key=True),
    Column("weekly_run_id", Text, ForeignKey("weekly_runs.id"), nullable=False),
    Column("resort_id", Text, ForeignKey("resorts.id"), nullable=False),
    Column("passed_gate", Boolean, nullable=False),
    Column("gate_failure_reason", Text),
    Column("depth_cm", Integer),
    Column("temperature_c", Float),
    Column("forecast_stable", Boolean),
    Column("forecast_detail", Text),  # JSONB → Text
    Column("historical_reliability_pct", Float),
    Column("score_depth", Integer),
    Column("score_forecast", Integer),
    Column("score_historical", Integer),
    Column("score_qualitative", Integer),
    Column("score_total", Integer),
    Column("rank", Integer),
    Column("created_at", Text),
    Index("idx_snow_scores_run_id", "weekly_run_id"),
    Index("idx_snow_scores_resort_id", "resort_id"),
    Index("idx_snow_scores_total_desc", "score_total"),
)

# ---------------------------------------------------------------------------
# In-memory SQLite engine (module-scoped so all tests in a session share it)
# ---------------------------------------------------------------------------

_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="session")
async def _test_engine():
    """
    Module-scoped async SQLite engine with all three tables created once.

    Using ``scope="session"`` means the engine (and the in-memory database) is
    shared across the whole test session.  Individual tests are isolated via the
    ``db_session`` fixture, which wraps every test in a rolled-back transaction.
    """
    engine = create_async_engine(_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(_test_meta.create_all)
    yield engine
    await engine.dispose()


# ---------------------------------------------------------------------------
# Per-test AsyncSession with rollback isolation
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_session(_test_engine) -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an ``AsyncSession`` backed by the in-memory SQLite test database.

    Each test gets a fresh transaction that is rolled back on teardown, so
    tests are fully isolated from one another without recreating the schema.
    """
    session_factory = async_sessionmaker(
        _test_engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    async with session_factory() as session:
        # Begin a nested (SAVEPOINT) transaction so we can roll back after the test
        async with session.begin():
            yield session
            await session.rollback()


# ---------------------------------------------------------------------------
# ResortFactory — creates valid Resort ORM instances without hitting the DB
# ---------------------------------------------------------------------------

_resort_counter = 0


class ResortFactory:
    """
    Helper that builds minimal valid ``Resort`` ORM objects.

    Objects are **not** added to any session by default.  Callers that need a
    persisted resort should do::

        resort = resort_factory.build()
        db_session.add(resort)
        await db_session.flush()

    The factory auto-increments a counter to produce unique names, so tests
    can create multiple resorts in the same session without name collisions.
    """

    def build(self, **kwargs) -> Resort:
        """
        Return a ``Resort`` instance with sensible defaults.

        Any keyword argument accepted by the ``Resort`` constructor can be
        passed to override the defaults, e.g.::

            resort_factory.build(mode="tourism", altitude_base_m=800)
        """
        global _resort_counter
        _resort_counter += 1

        defaults: dict = {
            "id": uuid.uuid4(),
            "name": f"Test Resort {_resort_counter}",
            "country": "AT",
            "region": "Tyrol",
            "latitude": 47.0,
            "longitude": 11.0,
            "altitude_base_m": 1500,
            "altitude_peak_m": 2800,
            "mode": "sports",
            # nearest_airports is ARRAY(String) in the ORM — pass a Python list;
            # the ORM will serialise it correctly for PostgreSQL.  In SQLite tests
            # the ORM object is usually used in-memory only; when persisting via
            # the raw ``_resorts`` Table we store the value as a comma-separated
            # string instead (see helper below).
            "nearest_airports": ["INN"],
            "piste_count": 50,
            "lift_count": 10,
            "webcam_url": None,
            "is_active": True,
            "created_at": datetime.utcnow(),
        }
        defaults.update(kwargs)
        return Resort(**defaults)


@pytest.fixture
def resort_factory() -> ResortFactory:
    """Return a ``ResortFactory`` instance for use in tests."""
    return ResortFactory()
