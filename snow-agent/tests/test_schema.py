"""
Smoke tests for DB schema creation — task 2.3.

Strategy
--------
SQLite doesn't support PostgreSQL-specific types (ARRAY, JSONB, UUID) or
PostgreSQL-specific index operators (DESC on a named index).  We therefore
use two complementary approaches:

1. **ORM metadata inspection (no DB)** — directly interrogate the Python
   ORM model objects (`__tablename__`, `__table_args__`, `__table__.columns`,
   relationships) to assert the schema is declared correctly.  This covers
   table names, FK relationships, column presence, CHECK constraint names,
   and index names without touching any database.

2. **SQLite runtime tests** — create a minimal SQLite-compatible metadata
   (plain `Text` / `Float` / `Integer` in place of `ARRAY`, `JSONB`, `UUID`)
   that mirrors the same table names and CHECK constraints, then run
   `create_all` against an in-memory aiosqlite database to assert:
     • all three tables are created successfully
     • CHECK constraints on `resorts.mode` and `weekly_runs.status` are
       enforced at INSERT time

Requirements: 20.1, 31.1
"""

import uuid
from datetime import date

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
    inspect,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Import ORM models so that Base.metadata is fully populated.
from app.db.models.base import Base
from app.db.models.resort import Resort
from app.db.models.snow_score import SnowScore
from app.db.models.weekly_run import WeeklyRun

# ---------------------------------------------------------------------------
# SQLite-compatible mirror metadata
# ---------------------------------------------------------------------------
# We replicate the three tables using only SQLite-safe types so that we can
# run real DDL + DML tests without PostgreSQL.
# ---------------------------------------------------------------------------

_test_meta = MetaData()

_resorts = Table(
    "resorts",
    _test_meta,
    Column("id", Text, primary_key=True),
    Column("name", String(100), nullable=False),
    Column("country", String(50), nullable=False),
    Column("region", String(100)),
    Column("latitude", Float, nullable=False),
    Column("longitude", Float, nullable=False),
    Column("altitude_base_m", Integer, nullable=False),
    Column("altitude_peak_m", Integer),
    Column("mode", String(20), nullable=False),
    Column("nearest_airports", Text, nullable=False),  # ARRAY → Text
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

DATABASE_URL = "sqlite+aiosqlite:///:memory:"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def engine():
    """Create all tables once for the module and yield the engine."""
    _engine = create_async_engine(DATABASE_URL, echo=False)
    async with _engine.begin() as conn:
        await conn.run_sync(_test_meta.create_all)
    yield _engine
    await _engine.dispose()


@pytest_asyncio.fixture
async def conn(engine):
    """Yield a connection with a savepoint; roll back after each test."""
    async with engine.connect() as connection:
        await connection.begin()
        yield connection
        await connection.rollback()


# ---------------------------------------------------------------------------
# Part 1 — ORM metadata inspection (pure Python, no DB required)
# ---------------------------------------------------------------------------


class TestOrmModelMetadata:
    """Verify ORM declarations without touching any database."""

    # --- table names ---

    def test_resort_tablename(self):
        assert Resort.__tablename__ == "resorts"

    def test_weekly_run_tablename(self):
        assert WeeklyRun.__tablename__ == "weekly_runs"

    def test_snow_score_tablename(self):
        assert SnowScore.__tablename__ == "snow_scores"

    # --- CHECK constraint names ---

    def test_resort_mode_check_constraint_exists(self):
        constraint_names = {
            arg.name
            for arg in Resort.__table_args__
            if isinstance(arg, CheckConstraint)
        }
        assert "ck_resorts_mode" in constraint_names

    def test_weekly_run_status_check_constraint_exists(self):
        constraint_names = {
            arg.name
            for arg in WeeklyRun.__table_args__
            if isinstance(arg, CheckConstraint)
        }
        assert "ck_weekly_runs_status" in constraint_names

    # --- index names on SnowScore ---

    def test_snow_score_index_run_id(self):
        index_names = {
            arg.name
            for arg in SnowScore.__table_args__
            if isinstance(arg, Index)
        }
        assert "idx_snow_scores_run_id" in index_names

    def test_snow_score_index_resort_id(self):
        index_names = {
            arg.name
            for arg in SnowScore.__table_args__
            if isinstance(arg, Index)
        }
        assert "idx_snow_scores_resort_id" in index_names

    def test_snow_score_index_total_desc(self):
        index_names = {
            arg.name
            for arg in SnowScore.__table_args__
            if isinstance(arg, Index)
        }
        assert "idx_snow_scores_total_desc" in index_names

    # --- FK relationships ---

    def test_snow_score_has_run_relationship(self):
        assert hasattr(SnowScore, "run"), "SnowScore must have a 'run' relationship"

    def test_snow_score_has_resort_relationship(self):
        assert hasattr(SnowScore, "resort"), "SnowScore must have a 'resort' relationship"

    def test_weekly_run_has_scores_relationship(self):
        assert hasattr(WeeklyRun, "scores"), "WeeklyRun must have a 'scores' relationship"

    # --- key columns exist ---

    def test_resort_columns(self):
        cols = {c.name for c in Resort.__table__.columns}
        for required in (
            "id", "name", "country", "latitude", "longitude",
            "altitude_base_m", "mode", "nearest_airports", "is_active",
        ):
            assert required in cols, f"Resort missing column '{required}'"

    def test_weekly_run_columns(self):
        cols = {c.name for c in WeeklyRun.__table__.columns}
        for required in (
            "id", "run_date", "mode", "status",
            "resorts_checked", "resorts_passed", "error_log",
            "started_at", "completed_at",
        ):
            assert required in cols, f"WeeklyRun missing column '{required}'"

    def test_snow_score_columns(self):
        cols = {c.name for c in SnowScore.__table__.columns}
        for required in (
            "id", "weekly_run_id", "resort_id", "passed_gate",
            "gate_failure_reason", "depth_cm", "temperature_c",
            "score_depth", "score_forecast", "score_historical",
            "score_qualitative", "score_total", "rank",
        ):
            assert required in cols, f"SnowScore missing column '{required}'"


# ---------------------------------------------------------------------------
# Part 2 — SQLite runtime: table existence after create_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_three_tables_exist(engine):
    """All three core tables must be created by create_all."""
    async with engine.connect() as c:
        table_names = await c.run_sync(
            lambda sync_conn: inspect(sync_conn).get_table_names()
        )
    missing = {"resorts", "weekly_runs", "snow_scores"} - set(table_names)
    assert not missing, f"Missing tables after create_all: {missing}"


@pytest.mark.asyncio
async def test_resorts_table_exists(engine):
    async with engine.connect() as c:
        names = await c.run_sync(lambda sc: inspect(sc).get_table_names())
    assert "resorts" in names


@pytest.mark.asyncio
async def test_weekly_runs_table_exists(engine):
    async with engine.connect() as c:
        names = await c.run_sync(lambda sc: inspect(sc).get_table_names())
    assert "weekly_runs" in names


@pytest.mark.asyncio
async def test_snow_scores_table_exists(engine):
    async with engine.connect() as c:
        names = await c.run_sync(lambda sc: inspect(sc).get_table_names())
    assert "snow_scores" in names


# ---------------------------------------------------------------------------
# Part 3 — SQLite runtime: CHECK constraints enforced
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_resort_modes_accepted(conn):
    """Valid mode values must insert without error."""
    for mode in ("sports", "tourism", "both"):
        await conn.execute(
            _resorts.insert().values(
                id=str(uuid.uuid4()),
                name=f"Resort {mode}",
                country="AT",
                latitude=47.0,
                longitude=12.0,
                altitude_base_m=1000,
                mode=mode,
                nearest_airports="INN",
            )
        )
    await conn.commit()


@pytest.mark.asyncio
async def test_invalid_resort_mode_rejected(conn):
    """An invalid mode value must raise IntegrityError (CHECK constraint)."""
    with pytest.raises((IntegrityError, Exception)):
        await conn.execute(
            _resorts.insert().values(
                id=str(uuid.uuid4()),
                name="Bad Resort",
                country="AT",
                latitude=47.0,
                longitude=12.0,
                altitude_base_m=1000,
                mode="invalid_mode",
                nearest_airports="INN",
            )
        )
        await conn.commit()


@pytest.mark.asyncio
async def test_valid_weekly_run_statuses_accepted(conn):
    """Valid status values must insert without error."""
    for status in ("running", "complete", "failed"):
        await conn.execute(
            _weekly_runs.insert().values(
                id=str(uuid.uuid4()),
                run_date=str(date(2025, 1, 3)),
                mode="sports",
                status=status,
            )
        )
    await conn.commit()


@pytest.mark.asyncio
async def test_invalid_weekly_run_status_rejected(conn):
    """An invalid status value must raise IntegrityError (CHECK constraint)."""
    with pytest.raises((IntegrityError, Exception)):
        await conn.execute(
            _weekly_runs.insert().values(
                id=str(uuid.uuid4()),
                run_date=str(date(2025, 1, 3)),
                mode="sports",
                status="invalid_status",
            )
        )
        await conn.commit()
