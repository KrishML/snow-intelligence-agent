# Implementation Plan: Snow Intelligence Agent — Phase 1 (Snow Intelligence Core)

## Overview

Build the foundational snow intelligence pipeline: project scaffolding, database schema, resort seed data, async Open-Meteo client, Snow Score Engine (gate + scoring), weekly run orchestrator, and APScheduler trigger. The qualitative score component is a neutral placeholder (10 pts) in this phase.

All implementation uses Python 3.11+, SQLAlchemy 2.x async, Alembic, PostgreSQL 16, APScheduler 3.x, httpx, Hypothesis (PBT), and pytest-asyncio.

---

## Tasks

- [x] 1. Initialise project structure and development environment
  - Create the directory layout from the design: `app/`, `app/db/models/`, `app/clients/`, `app/scoring/`, `app/runner/`, `app/scheduler/`, `alembic/`, `data/`, `scripts/`, `tests/`
  - Create `pyproject.toml` with all Phase 1 dependencies pinned to exact versions: `fastapi`, `uvicorn[standard]`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `httpx`, `pydantic-settings`, `apscheduler`, `hypothesis`, `pytest`, `pytest-asyncio`, `pytest-httpx`
  - Create `.env.example` with all settings keys from `app/settings.py` (no real values)
  - Create `app/settings.py` implementing the `Settings` class with `pydantic-settings` as specified in the design
  - Create `app/main.py` with a minimal FastAPI app that includes a `/health` endpoint and wires up the scheduler in the `lifespan` context manager
  - _Requirements: 29.1_

- [x] 2. Database schema and migrations
  - [x] 2.1 Create SQLAlchemy ORM models for `resorts`, `weekly_runs`, and `snow_scores`
    - Implement `app/db/models/resort.py` with the `Resort` model matching the spec schema exactly, including the `CHECK (mode IN ('sports','tourism','both'))` constraint
    - Implement `app/db/models/weekly_run.py` with the `WeeklyRun` model and `CHECK (status IN ('running','complete','failed'))` constraint
    - Implement `app/db/models/snow_score.py` with the `SnowScore` model including all nullable score columns and FK relationships
    - Implement `app/db/session.py` with `create_async_engine`, `async_sessionmaker`, and `get_db` dependency
    - _Requirements: 20.1, 31.1_

  - [x] 2.2 Create Alembic baseline migration
    - Initialise Alembic with `alembic init alembic` and configure `env.py` to use the async SQLAlchemy engine and import all ORM models
    - Generate and review the initial migration `0001_initial_schema.py` covering all three tables with correct constraints, foreign keys, and indexes: `idx_snow_scores_run_id`, `idx_snow_scores_resort_id`, `idx_snow_scores_total_desc`
    - Verify migration applies cleanly against a local PostgreSQL instance: `alembic upgrade head`
    - _Requirements: 20.1, 31.3_

  - [x] 2.3 Write smoke test for schema creation
    - Assert all three tables exist in the DB after `alembic upgrade head`
    - Assert CHECK constraints reject invalid `mode` and `status` values
    - _Requirements: 20.1, 31.1_

  - [x] 2.4 Create pytest fixtures and test configuration
    - Implement `tests/conftest.py` with async DB fixture using `pytest-asyncio` and an in-memory SQLite async engine for unit tests (note: property-based tests for pure functions need no DB fixture)
    - Create a `pytest.ini` or `[tool.pytest.ini_options]` section in `pyproject.toml` with `asyncio_mode = "auto"` and `testpaths = ["tests"]`
    - Add a `ResortFactory` helper in `conftest.py` that creates minimal valid `Resort` ORM instances for use in orchestrator tests
    - _Requirements: 29.3, 29.5_

- [x] 3. Docker Compose service definition
  - Create `docker-compose.yml` as specified in the design with `app` and `db` services
  - Create `Dockerfile` using `python:3.11-slim` with `uv` for dependency installation
  - Verify `docker compose up` starts both services and the app health endpoint responds at `http://localhost:8000/health`
  - _Requirements: 31.4_

- [x] 4. Resort seed data
  - [x] 4.1 Create resort seed data file
    - Create `data/resorts.json` containing exactly 40 sports resorts and 30 tourism destinations
    - Each entry must include: `name`, `country`, `region`, `latitude`, `longitude`, `altitude_base_m`, `altitude_peak_m` (nullable), `mode` (`sports`/`tourism`/`both`), `nearest_airports` (array of IATA codes), `piste_count` (nullable, sports only), `lift_count` (nullable, sports only)
    - Sports resorts: include major European ski resorts across Austria, France, Switzerland, Italy, Norway, Poland, Andorra (e.g., Chamonix, Zermatt, Verbier, Ischgl, Val d'Isère, Courchevel, St. Anton, Kitzbühel, Zakopane, Bansko, etc.) with altitudes ≥ 1200m
    - Tourism destinations: include lower-altitude scenic winter destinations across Germany, Austria, Czech Republic, Poland, France, Switzerland (e.g., Hallstatt, Innsbruck, Garmisch-Partenkirchen, Český Krumlov, Zakopane, etc.) with altitudes 500–1400m
    - Nearest airports must be real IATA codes for airports within reasonable transfer distance
    - _Requirements: 20.5, 29.1_

  - [x] 4.2 Implement resort seeding script
    - Create `scripts/seed_resorts.py` that reads `data/resorts.json` and inserts all records using `INSERT ... ON CONFLICT (name, country) DO NOTHING` to make it idempotent
    - The script must be runnable as `python -m scripts.seed_resorts` and exit with code 0 on success
    - _Requirements: 20.5, 29.1_

  - [x] 4.3 Write smoke test for resort seeding
    - Run the seeding script against the test DB and assert `SELECT COUNT(*) WHERE mode='sports' OR mode='both' >= 40` and `SELECT COUNT(*) WHERE mode='tourism' OR mode='both' >= 30`
    - _Requirements: 20.5, 29.1_

- [x] 5. Scoring schemas and gate logic
  - [x] 5.1 Create scoring Pydantic schemas
    - Implement `app/scoring/schemas.py` with `DayForecast`, `GateInput`, `GateResult`, `ScoreInput`, and `ScoreResult` Pydantic models as defined in the design
    - All field constraints (`ge`, `le`, `Literal`) must be present
    - _Requirements: 8.1–8.5, 9.1–9.8_

  - [x] 5.2 Implement gate logic
    - Implement `app/scoring/engine.py` with the `apply_gate(input: GateInput) -> GateResult` pure function
    - Gate conditions evaluated in priority order: depth threshold → open run (sports only) → rain forecast → warm forecast
    - First failing condition determines `reason`; function returns immediately on first failure
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [x] 5.3 Write property tests for gate logic
    - Implement `tests/test_gate.py` using Hypothesis `@given` with `@settings(max_examples=200)`
    - **Property 1: Depth Gate Exclusion** — generate any depth below mode threshold; assert `passed=False, reason='depth_below_threshold'`
      - **Validates: Requirements 8.1, 8.3**
    - **Property 2: Open Run Gate** — generate sports-mode inputs with `has_open_run=False`; assert `passed=False, reason='no_open_run'`
      - **Validates: Requirement 8.2**
    - **Property 3: Rain Forecast Gate** — generate forecasts with ≥1 rainy day; assert `passed=False, reason='rain_in_forecast'`
      - **Validates: Requirement 8.4**
    - **Property 4: Warm Forecast Gate** — generate forecasts with ≥1 warm day (depth and rain conditions satisfied); assert `passed=False, reason='warm_temperature_forecast'`
      - **Validates: Requirement 8.5**
    - Also include spec test case: `test_gate_excludes_shallow_sports_depth` (depth=35cm, mode=sports → excluded) and `test_gate_excludes_rain_forecast` (depth=60cm, 3-day rain → excluded)
    - _Requirements: 8.1–8.5_

- [x] 6. Score computation engine
  - [x] 6.1 Implement depth, forecast, historical, and total scoring functions
    - Implement `score_depth(depth_cm: float, mode: str) -> int` with the mode-specific bracket lookup
    - Implement `score_forecast(days: list[DayForecast]) -> int` with the priority-ordered baseline + snowfall bonus logic and clamp to [0, 30]
    - Implement `score_historical(reliability_pct: float) -> int` as `round(reliability_pct * 0.25)`, clamped to [0, 25]
    - Implement `compute_score(input: ScoreInput) -> ScoreResult` that composes all four components and caps total at 100
    - _Requirements: 9.1–9.8_

  - [x] 6.2 Write property tests for scoring functions
    - Implement `tests/test_scoring.py` using Hypothesis `@given` with `@settings(max_examples=200)`
    - **Property 5: Score Total Invariant** — generate random valid component scores; assert `total == min(100, sum)` exactly
      - **Validates: Requirements 9.1, 9.7, 9.8**
    - **Property 6: Depth Score Lookup** — generate random depths per mode and assert correct bracket mapping including exact boundary values
      - **Validates: Requirements 9.2, 9.3**
    - **Property 7: Forecast Score Bounds and Priority** — generate forecast combos; assert result ∈ [0, 30] and negative conditions cap score ≤ 15
      - **Validates: Requirement 9.4**
    - **Property 8: Historical Reliability Formula** — generate random weekend depth lists; assert `score_historical == round(pct * 0.25)` and result ∈ [0, 25]
      - **Validates: Requirement 9.5**
    - Also include spec test cases: `test_score_components_sum_correctly` and `test_historical_reliability_calculation`
    - _Requirements: 9.1–9.8_

- [x] 7. Open-Meteo async client
  - [x] 7.1 Implement the Open-Meteo client
    - Implement `app/clients/open_meteo.py` with `OpenMeteoClient` class using `httpx.AsyncClient`
    - Implement `get_current_conditions(resort_id, lat, lon) -> ResortConditions`: calls `/v1/forecast` with `hourly=snow_depth,temperature_2m`, `forecast_days=1`; returns the most recent non-null `snow_depth` value (in cm, converted from metres) and the corresponding `temperature_2m`
    - Implement `get_5day_forecast(resort_id, lat, lon) -> ForecastData`: calls `/v1/forecast` with `daily=temperature_2m_max,rain_sum,snowfall_sum`, `forecast_days=5`; returns 5 `DayForecast` entries
    - Implement `get_historical_reliability(resort_id, lat, lon, mode) -> float`: calls `/v1/archive` for the last 5 years; filters to Saturday and Sunday dates; counts days where `snow_depth` ≥ mode threshold; returns `(matching_days / total_weekend_days) * 100`
    - Implement shared `asyncio.Semaphore` for concurrency control and exponential backoff retry (max 3 attempts)
    - Raise `OpenMeteoFetchError(resort_id, endpoint, status_code)` on final failure
    - _Requirements: 7.2, 29.2_

  - [x] 7.2 Write integration tests for Open-Meteo client
    - Implement `tests/test_open_meteo.py` using `pytest-httpx` to mock HTTP responses
    - Test `get_current_conditions` with a mocked response containing known `snow_depth` values; assert correct cm conversion (m × 100) and correct value selection
    - Test `get_5day_forecast` with a mocked response; assert all 5 days are parsed correctly
    - Test `get_historical_reliability` with a mocked archive response spanning 1 year of weekly data; assert the reliability percentage is computed correctly
    - Test that `OpenMeteoFetchError` is raised after 3 failed retries (mock 3× 500 responses)
    - Test semaphore: assert no more than `open_meteo_concurrency` concurrent requests are in-flight simultaneously (use mock with delay)
    - _Requirements: 7.2_

- [x] 8. Weekly run orchestrator
  - [x] 8.1 Implement the weekly run orchestrator
    - Implement `app/runner/weekly_run.py` with `WeeklyRunOrchestrator.run(mode)` as designed
    - Step 1: Read mode-appropriate resort list from DB (resorts where `mode = requested_mode OR mode = 'both'` and `is_active = true`)
    - Step 2: Create `weekly_runs` record with `status='running'`
    - Step 3: Dispatch parallel Open-Meteo fetches (all three calls per resort) using `asyncio.gather` + shared semaphore; catch `OpenMeteoFetchError` per resort and accumulate in `error_log`
    - Step 4: For each resort with successful data, call `apply_gate` then `compute_score` if gate passed
    - Step 5: Write `snow_scores` records for all resorts (including gate-failed ones)
    - Step 6: Compute ranks for passing resorts by `score_total DESC`; update `rank` field for top 10
    - Step 7: Update `weekly_runs` with `status='complete'`, `resorts_checked`, `resorts_passed`, `completed_at`, `error_log`
    - On any unrecoverable error: update `weekly_runs` with `status='failed'`
    - _Requirements: 7.1–7.8, 29.3_

  - [x] 8.2 Write property and integration tests for weekly run orchestrator
    - Implement `tests/test_weekly_run.py` using `pytest-asyncio` with an in-memory test DB (SQLite async) and `pytest-httpx` for mocked Open-Meteo responses
    - **Property 9: Weekly Run DB Write Completeness** — generate resort lists of size N (1–50) with mocked API data; assert exactly N `snow_scores` records and 1 `weekly_runs` record with `resorts_checked=N`
      - **Validates: Requirements 7.4, 7.5**
    - **Property 10: Top-10 Ranking Correctness** — generate random score sets; assert the 10 stored `rank IS NOT NULL` records are the actual top 10 by score in correct order
      - **Validates: Requirement 7.6**
    - **Property 11: Mode Routing Completeness** — assert that for mode=sports, only sports/both resorts are fetched; for mode=tourism, only tourism/both resorts
      - **Validates: Requirements 3.1, 3.2, 29.2**
    - Include spec test case `test_mode_routing_uses_correct_resort_list` and `test_weekly_run_writes_to_db` as concrete examples
    - _Requirements: 7.1–7.8, 3.1, 3.2_

- [x] 9. APScheduler integration
  - [x] 9.1 Implement APScheduler job and wire into FastAPI lifespan
    - Implement `app/scheduler/jobs.py` with `create_scheduler()` returning a configured `AsyncIOScheduler` with the cron trigger for Friday 17:00 UTC using settings values
    - Configure `misfire_grace_time=3600`
    - Update `app/main.py` `lifespan` context manager to call `scheduler.start()` on startup and `scheduler.shutdown()` on teardown
    - _Requirements: 7.1, 29.4_

  - [x] 9.2 Write smoke test for scheduler configuration
    - Assert the scheduler has exactly one job registered with `id='weekly_snow_run'`
    - Assert the job trigger is a `CronTrigger` configured for `day_of_week='fri'`, `hour=17`, `minute=0`, `timezone='UTC'`
    - Assert `misfire_grace_time=3600`
    - _Requirements: 7.1, 29.4_

---

## Notes

- The qualitative signal score is hardcoded to 10 (neutral) in Phase 1 — `ScoreInput.qualitative_score` defaults to 10 and `score_qualitative` is always written as 10 in `snow_scores`
- The delivery event at the end of the weekly run (Step 9 in the orchestrator flow) is a no-op stub in Phase 1 — a log message is sufficient; Phase 4 implements actual delivery
- `has_open_run` is `None` / unknown in Phase 1 (no official report scraper yet); the gate treats `None` as "unknown" and does not apply the `no_open_run` exclusion until Phase 2 provides this signal
- All PBT tests use `@settings(max_examples=200)` and include a `deadline=None` to prevent flaky timeouts on slower CI machines
- The Open-Meteo historical archive call covers the last 5 years: `start_date = today - 5*365 days`, `end_date = yesterday`; the response is filtered to weekend dates (Python `weekday() in (5, 6)`) before computing the reliability percentage

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1", "3"] },
    { "id": 2, "tasks": ["2.2"] },
    { "id": 3, "tasks": ["2.3", "4.1"] },
    { "id": 4, "tasks": ["2.4", "4.2"] },
    { "id": 5, "tasks": ["4.3", "5.1"] },
    { "id": 6, "tasks": ["5.2"] },
    { "id": 7, "tasks": ["5.3", "6.1"] },
    { "id": 8, "tasks": ["6.2", "7.1"] },
    { "id": 9, "tasks": ["7.2", "8.1"] },
    { "id": 10, "tasks": ["8.2", "9.1"] },
    { "id": 11, "tasks": ["9.2"] }
  ]
}
```
