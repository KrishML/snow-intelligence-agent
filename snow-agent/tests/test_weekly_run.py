"""
Property-based and integration tests for the weekly run orchestrator.

Architecture
------------
The ``WeeklyRunOrchestrator`` calls ``AsyncSessionLocal`` directly (not through
FastAPI DI) and constructs its own ``OpenMeteoClient`` instance internally.
Both are patched at the module level:

  - ``app.runner.weekly_run.AsyncSessionLocal`` → ``async_sessionmaker`` backed
    by an in-memory aiosqlite database using the same SQLite-compatible mirror
    schema defined in ``tests.conftest``.
  - ``app.runner.weekly_run.OpenMeteoClient`` → a factory that returns a
    pre-configured ``AsyncMock`` client so no real HTTP calls are made.

UUID storage note
-----------------
SQLAlchemy serialises ``uuid.UUID`` ORM column values to the hex string
(no dashes) when writing to SQLite TEXT columns.  All raw-SQL inserts and
query parameters in this test file therefore use ``uuid_obj.hex`` to match
the format SQLAlchemy will use when querying.

Properties covered
------------------
Property 9  — Weekly Run DB Write Completeness  Validates: Requirements 7.4, 7.5
Property 10 — Top-10 Ranking Correctness        Validates: Requirement 7.6
Property 11 — Mode Routing Completeness         Validates: Requirements 3.1, 3.2

Spec test cases
---------------
test_weekly_run_writes_to_db          (N=3 resorts → 3 snow_scores, 1 weekly_run)
test_mode_routing_uses_correct_resort_list  (sports/both vs tourism/both filtering)
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from hypothesis import assume, given
from hypothesis import settings as h_settings
from hypothesis import strategies as st
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.clients.open_meteo import ForecastData
from app.clients.open_meteo import DayForecast as ClientDayForecast
from app.clients.open_meteo import ResortConditions
from app.runner.weekly_run import WeeklyRunOrchestrator

# Import the SQLite mirror schema from conftest
from tests.conftest import _resorts, _test_meta, _weekly_runs

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SQLITE_URL = "sqlite+aiosqlite:///:memory:"

# Good snow conditions that pass all gate checks for both modes
_GOOD_SNOW_DEPTH_CM = 60.0
_GOOD_TEMP_C = -5.0
_GOOD_RAIN_MM = 0.0
_GOOD_SNOWFALL_CM = 5.0
_GOOD_RELIABILITY = 80.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _build_engine():
    """Create a fresh in-memory SQLite engine with the test schema."""
    engine = create_async_engine(_SQLITE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(_test_meta.create_all)
    return engine


async def _insert_resorts(engine, resorts_data: list[dict]) -> None:
    """Insert resort rows directly into the mirror ``resorts`` table."""
    async with engine.begin() as conn:
        for row in resorts_data:
            await conn.execute(_resorts.insert().values(**row))


async def _insert_weekly_run(engine, run_id: uuid.UUID, mode: str) -> None:
    """
    Insert a weekly_run row with status='running'.

    SQLAlchemy serialises uuid.UUID → hex string (no dashes) when reading
    via the ORM, so we insert using ``run_id.hex`` to match.
    """
    async with engine.begin() as conn:
        await conn.execute(
            _weekly_runs.insert().values(
                id=run_id.hex,
                run_date=str(date.today()),
                mode=mode,
                status="running",
                resorts_checked=None,
                resorts_passed=None,
                error_log=None,
                started_at=str(datetime.now()),
                completed_at=None,
            )
        )


def _resort_row(
    *,
    mode: str = "sports",
    name: str | None = None,
    is_active: bool = True,
) -> dict:
    """Build a minimal valid resort row dict for insertion into the mirror table."""
    rid = uuid.uuid4()
    return {
        # ORM will load this as uuid.UUID; hex-string storage matches UUID mapping
        "id": rid.hex,
        "name": name or f"Resort {rid.hex[:8]}",
        "country": "AT",
        "region": "Tyrol",
        "latitude": 47.0,
        "longitude": 11.0,
        "altitude_base_m": 1500,
        "altitude_peak_m": 2800,
        "mode": mode,
        "nearest_airports": "INN",  # ARRAY(String) → stored as Text in SQLite mirror
        "piste_count": 50,
        "lift_count": 10,
        "webcam_url": None,
        "is_active": True,
        "created_at": str(datetime.now()),
    }


def _make_mock_client(
    snow_depth_cm: float = _GOOD_SNOW_DEPTH_CM,
    temp_c: float = _GOOD_TEMP_C,
    rain_mm: float = _GOOD_RAIN_MM,
    snowfall_cm: float = _GOOD_SNOWFALL_CM,
    reliability: float = _GOOD_RELIABILITY,
) -> AsyncMock:
    """
    Build an AsyncMock that mimics ``OpenMeteoClient``.

    Supports ``async with OpenMeteoClient(...) as client:`` and returns
    pre-configured data for all three API methods.
    """
    mock_client = AsyncMock()

    async def _conditions(resort_id, lat, lon):
        return ResortConditions(
            resort_id=resort_id,
            snow_depth_cm=snow_depth_cm,
            temperature_c=temp_c,
        )

    async def _forecast(resort_id, lat, lon):
        days = [
            ClientDayForecast(
                date=date(2025, 1, i + 1),
                temp_max_c=temp_c,
                rain_sum_mm=rain_mm,
                snowfall_sum_cm=snowfall_cm,
            )
            for i in range(5)
        ]
        return ForecastData(resort_id=resort_id, days=days)

    async def _reliability(resort_id, lat, lon, mode):
        return reliability

    mock_client.get_current_conditions.side_effect = _conditions
    mock_client.get_5day_forecast.side_effect = _forecast
    mock_client.get_historical_reliability.side_effect = _reliability
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def _make_client_factory(mock_client: AsyncMock):
    """Return a class-like callable that yields the mock when constructed."""

    class _MockClientClass:
        def __new__(cls, **kwargs):
            return mock_client

    return _MockClientClass


async def _run_orchestrator(
    engine,
    run_id: uuid.UUID,
    mode: str,
    mock_client: AsyncMock,
) -> None:
    """Patch session and client, then call ``_execute_run``."""
    factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )
    with patch("app.runner.weekly_run.AsyncSessionLocal", factory):
        with patch(
            "app.runner.weekly_run.OpenMeteoClient",
            _make_client_factory(mock_client),
        ):
            orchestrator = WeeklyRunOrchestrator()
            await orchestrator._execute_run(run_id, mode)


# ---------------------------------------------------------------------------
# pytest-asyncio fixtures for concrete tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def test_engine():
    """Fresh in-memory SQLite engine per test."""
    engine = await _build_engine()
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def patched_session_factory(test_engine):
    """
    Patch ``AsyncSessionLocal`` in the orchestrator module to use the
    in-memory test engine.  Yields the session factory.
    """
    factory = async_sessionmaker(
        test_engine, expire_on_commit=False, class_=AsyncSession
    )
    with patch("app.runner.weekly_run.AsyncSessionLocal", factory):
        yield factory


# ---------------------------------------------------------------------------
# Spec test case 1: test_weekly_run_writes_to_db
# ---------------------------------------------------------------------------


async def test_weekly_run_writes_to_db(test_engine, patched_session_factory) -> None:
    """
    With 3 sports resorts, after _execute_run completes:
      - exactly 3 snow_scores records exist linked to the run
      - exactly 1 weekly_runs record exists with status='complete' and
        resorts_checked=3

    Validates: Requirements 7.4, 7.5
    """
    rows = [_resort_row(mode="sports") for _ in range(3)]
    await _insert_resorts(test_engine, rows)

    run_id = uuid.uuid4()
    await _insert_weekly_run(test_engine, run_id, "sports")

    mock_client = _make_mock_client()
    with patch(
        "app.runner.weekly_run.OpenMeteoClient",
        _make_client_factory(mock_client),
    ):
        orchestrator = WeeklyRunOrchestrator()
        await orchestrator._execute_run(run_id, "sports")

    async with test_engine.connect() as conn:
        score_result = await conn.execute(
            text("SELECT COUNT(*) FROM snow_scores WHERE weekly_run_id = :run_id"),
            {"run_id": run_id.hex},
        )
        score_count = score_result.scalar()

        run_result = await conn.execute(
            text(
                "SELECT status, resorts_checked FROM weekly_runs WHERE id = :run_id"
            ),
            {"run_id": run_id.hex},
        )
        run_row = run_result.fetchone()

    assert score_count == 3, f"Expected 3 snow_scores, got {score_count}"
    assert run_row is not None, "weekly_runs row not found"
    assert run_row[0] == "complete", f"Expected status='complete', got {run_row[0]!r}"
    assert run_row[1] == 3, f"Expected resorts_checked=3, got {run_row[1]}"


# ---------------------------------------------------------------------------
# Spec test case 2: test_mode_routing_uses_correct_resort_list
# ---------------------------------------------------------------------------


async def test_mode_routing_uses_correct_resort_list(
    test_engine, patched_session_factory
) -> None:
    """
    With 3 sports, 3 tourism, and 2 'both' resorts:
      - run with mode='sports' → 5 fetch calls (sports + both)
      - run with mode='tourism' → 5 fetch calls (tourism + both)

    Validates: Requirements 3.1, 3.2
    """
    sports_rows = [_resort_row(mode="sports") for _ in range(3)]
    tourism_rows = [_resort_row(mode="tourism") for _ in range(3)]
    both_rows = [_resort_row(mode="both") for _ in range(2)]
    await _insert_resorts(test_engine, sports_rows + tourism_rows + both_rows)

    # --- Run 1: sports mode ---
    run_id_sports = uuid.uuid4()
    await _insert_weekly_run(test_engine, run_id_sports, "sports")

    mock_client_sports = _make_mock_client()
    with patch(
        "app.runner.weekly_run.OpenMeteoClient",
        _make_client_factory(mock_client_sports),
    ):
        orchestrator = WeeklyRunOrchestrator()
        await orchestrator._execute_run(run_id_sports, "sports")

    sports_calls = mock_client_sports.get_current_conditions.call_count
    assert sports_calls == 5, (
        f"sports mode should fetch 3 sports + 2 both = 5 resorts, got {sports_calls}"
    )

    # --- Run 2: tourism mode ---
    run_id_tourism = uuid.uuid4()
    await _insert_weekly_run(test_engine, run_id_tourism, "tourism")

    mock_client_tourism = _make_mock_client()
    with patch(
        "app.runner.weekly_run.OpenMeteoClient",
        _make_client_factory(mock_client_tourism),
    ):
        await orchestrator._execute_run(run_id_tourism, "tourism")

    tourism_calls = mock_client_tourism.get_current_conditions.call_count
    assert tourism_calls == 5, (
        f"tourism mode should fetch 3 tourism + 2 both = 5 resorts, got {tourism_calls}"
    )


# ---------------------------------------------------------------------------
# Property 9 — Weekly Run DB Write Completeness
# Validates: Requirements 7.4, 7.5
# ---------------------------------------------------------------------------


async def _pbt_db_completeness(n: int) -> None:
    """Inner async implementation for Property 9."""
    engine = await _build_engine()
    try:
        rows = [_resort_row(mode="sports") for _ in range(n)]
        await _insert_resorts(engine, rows)

        run_id = uuid.uuid4()
        await _insert_weekly_run(engine, run_id, "sports")

        mock_client = _make_mock_client()
        await _run_orchestrator(engine, run_id, "sports", mock_client)

        async with engine.connect() as conn:
            score_result = await conn.execute(
                text(
                    "SELECT COUNT(*) FROM snow_scores WHERE weekly_run_id = :run_id"
                ),
                {"run_id": run_id.hex},
            )
            score_count = score_result.scalar()

            run_result = await conn.execute(
                text(
                    "SELECT status, resorts_checked FROM weekly_runs WHERE id = :run_id"
                ),
                {"run_id": run_id.hex},
            )
            run_row = run_result.fetchone()

        assert score_count == n, (
            f"N={n}: expected {n} snow_scores, got {score_count}"
        )
        assert run_row is not None
        assert run_row[0] == "complete", f"N={n}: expected status='complete'"
        assert run_row[1] == n, (
            f"N={n}: expected resorts_checked={n}, got {run_row[1]}"
        )
    finally:
        await engine.dispose()


@given(n=st.integers(min_value=1, max_value=10))
@h_settings(max_examples=50, deadline=None)
def test_weekly_run_db_completeness(n: int) -> None:
    """
    Property 9: For any N sports resorts (1–10), after _execute_run:
      - exactly N snow_scores records are written linked to the run
      - exactly 1 weekly_runs record exists with status='complete' and
        resorts_checked=N

    **Validates: Requirements 7.4, 7.5**
    """
    asyncio.run(_pbt_db_completeness(n))


# ---------------------------------------------------------------------------
# Property 10 — Top-10 Ranking Correctness
# Validates: Requirement 7.6
# ---------------------------------------------------------------------------


async def _pbt_ranking(n: int) -> None:
    """Inner async implementation for Property 10."""
    engine = await _build_engine()
    try:
        # Use depths that produce distinct score_depth values so ranks are unambiguous.
        # Depths: 60, 70, 80, ... (all ≥ 60cm, within the 30-pt bracket for sports)
        # Each resort gets depth 60 + i*10, so historical score varies by reliability.
        # To get distinct totals reliably, vary both depth and a unique reliability offset.
        depths = [60.0 + i * 10.0 for i in range(n)]

        rows = [_resort_row(mode="sports") for _ in range(n)]
        await _insert_resorts(engine, rows)

        run_id = uuid.uuid4()
        await _insert_weekly_run(engine, run_id, "sports")

        # Build a mock client that assigns different depths in insertion order
        depth_iter = iter(depths)
        base_mock = _make_mock_client()

        async def _varying_conditions(resort_id, lat, lon):
            depth = next(depth_iter, 60.0)
            return ResortConditions(
                resort_id=resort_id,
                snow_depth_cm=depth,
                temperature_c=_GOOD_TEMP_C,
            )

        base_mock.get_current_conditions.side_effect = _varying_conditions

        await _run_orchestrator(engine, run_id, "sports", base_mock)

        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT score_total, rank FROM snow_scores "
                    "WHERE weekly_run_id = :run_id "
                    "AND passed_gate = 1 "
                    "ORDER BY score_total DESC"
                ),
                {"run_id": run_id.hex},
            )
            rows_result = result.fetchall()
    finally:
        await engine.dispose()

    # All passing resorts must have a rank
    assert len(rows_result) == n, f"N={n}: expected {n} passing rows"
    ranked = [(row[0], row[1]) for row in rows_result if row[1] is not None]
    assert len(ranked) == n, f"N={n}: expected all {n} resorts ranked, got {len(ranked)}"

    # Ranks must be sequential starting from 1
    ranks = sorted(r[1] for r in ranked)
    assert ranks == list(range(1, n + 1)), (
        f"N={n}: ranks {ranks} are not sequential 1..{n}"
    )

    # The resort with the highest score_total must have rank=1
    by_score_desc = sorted(ranked, key=lambda x: x[0], reverse=True)
    assert by_score_desc[0][1] == 1, (
        f"N={n}: resort with highest score_total should have rank=1, "
        f"got rank={by_score_desc[0][1]}"
    )


@given(n=st.integers(min_value=1, max_value=15))
@h_settings(max_examples=50, deadline=None)
def test_ranking_correctness(n: int) -> None:
    """
    Property 10: After _execute_run with N passing resorts (distinct depths),
    the rank=1 resort has the highest score_total, ranks are dense and
    sequential from 1, and every passing resort receives a rank.

    **Validates: Requirement 7.6**
    """
    asyncio.run(_pbt_ranking(n))


# ---------------------------------------------------------------------------
# Property 11 — Mode Routing Completeness
# Validates: Requirements 3.1, 3.2
# ---------------------------------------------------------------------------


async def _pbt_mode_routing(
    n_sports: int,
    n_tourism: int,
    n_both: int,
    mode: str,
) -> None:
    """Inner async implementation for Property 11."""
    engine = await _build_engine()
    try:
        sports_rows = [_resort_row(mode="sports") for _ in range(n_sports)]
        tourism_rows = [_resort_row(mode="tourism") for _ in range(n_tourism)]
        both_rows = [_resort_row(mode="both") for _ in range(n_both)]
        await _insert_resorts(engine, sports_rows + tourism_rows + both_rows)

        run_id = uuid.uuid4()
        await _insert_weekly_run(engine, run_id, mode)

        mock_client = _make_mock_client()
        await _run_orchestrator(engine, run_id, mode, mock_client)

        expected_fetches = (
            n_sports + n_both if mode == "sports" else n_tourism + n_both
        )
        actual_fetches = mock_client.get_current_conditions.call_count

        async with engine.connect() as conn:
            score_result = await conn.execute(
                text(
                    "SELECT COUNT(*) FROM snow_scores WHERE weekly_run_id = :run_id"
                ),
                {"run_id": run_id.hex},
            )
            score_count = score_result.scalar()
    finally:
        await engine.dispose()

    assert actual_fetches == expected_fetches, (
        f"mode={mode}, sports={n_sports}, tourism={n_tourism}, both={n_both}: "
        f"expected {expected_fetches} fetches, got {actual_fetches}"
    )
    assert score_count == expected_fetches, (
        f"mode={mode}: expected {expected_fetches} snow_scores, got {score_count}"
    )


@given(
    n_sports=st.integers(min_value=0, max_value=5),
    n_tourism=st.integers(min_value=0, max_value=5),
    n_both=st.integers(min_value=0, max_value=3),
    mode=st.sampled_from(["sports", "tourism"]),
)
@h_settings(max_examples=50, deadline=None)
def test_mode_routing_completeness(
    n_sports: int,
    n_tourism: int,
    n_both: int,
    mode: str,
) -> None:
    """
    Property 11: For any distribution of sports/tourism/both resorts and any
    mode, the orchestrator fetches data for exactly the resorts belonging to
    that mode plus 'both' resorts, and writes exactly that many snow_scores.

    **Validates: Requirements 3.1, 3.2**
    """
    # At least one eligible resort must exist for the run to do anything
    assume((n_sports + n_both > 0) if mode == "sports" else (n_tourism + n_both > 0))
    asyncio.run(_pbt_mode_routing(n_sports, n_tourism, n_both, mode))
