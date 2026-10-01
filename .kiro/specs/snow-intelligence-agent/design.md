# Design Document — Snow Intelligence Agent: Phase 1 (Snow Intelligence Core)

## Overview

Phase 1 builds the foundational data pipeline that powers everything downstream. The deliverables are:

1. A seeded `resorts` table (40 sports + 30 tourism destinations)
2. An async Open-Meteo client that fetches current snow depth, 5-day forecast, and 5-year historical weekend data
3. A Snow Score Engine that applies mode-specific gates and computes composite 0–100 scores
4. A weekly run orchestrator that is triggered by APScheduler every Friday at 17:00 UTC and writes results to `weekly_runs` and `snow_scores`

The system is Python-first with FastAPI as the web layer (serving the data layer API even in Phase 1 for health-check and readiness), SQLAlchemy 2.x ORM, Alembic migrations, and PostgreSQL 16 via Docker Compose. The qualitative signal component of the score is a neutral placeholder (10 pts) in Phase 1; it will be replaced in Phase 2.

---

## Architecture

### Component Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    APScheduler (Friday 17:00 UTC)            │
│                           │                                  │
│                           ▼                                  │
│              Weekly Run Orchestrator                         │
│              (app/runner/weekly_run.py)                      │
│                           │                                  │
│          ┌────────────────┼─────────────────┐               │
│          ▼                ▼                 ▼               │
│   Mode Resolver    Resort Selector    Run Writer             │
│   (user_prefs)     (DB query)         (weekly_runs)          │
│          │                                  │               │
│          ▼                                  │               │
│   ┌─────────────────────────────────────┐   │               │
│   │        Open-Meteo Client            │   │               │
│   │  (app/clients/open_meteo.py)        │   │               │
│   │  - get_current_conditions()         │   │               │
│   │  - get_5day_forecast()              │   │               │
│   │  - get_historical_reliability()     │   │               │
│   └──────────────┬──────────────────────┘   │               │
│                  │                          │               │
│                  ▼                          │               │
│   ┌─────────────────────────────────────┐   │               │
│   │      Snow Score Engine              │   │               │
│   │  (app/scoring/engine.py)            │   │               │
│   │  - apply_gate()                     │   │               │
│   │  - score_depth()                    │   │               │
│   │  - score_forecast()                 │   │               │
│   │  - score_historical()               │   │               │
│   │  - compute_total()                  │   │               │
│   └──────────────┬──────────────────────┘   │               │
│                  │                          │               │
│                  ▼                          ▼               │
│          ┌──────────────────────────────────────────┐       │
│          │         snow_scores table                │       │
│          │         weekly_runs table                │       │
│          └──────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────────┘
```

### Execution Flow

```
APScheduler fires (Friday 17:00 UTC)
        │
        ▼
weekly_run.run()
  1. Read mode from settings (user_prefs default: 'sports')
  2. Select resort list by mode from DB
  3. Create weekly_runs record (status='running')
        │
        ▼
  4. For each resort in list (parallel, semaphore-limited to 10):
       a. open_meteo.get_current_conditions(lat, lon)   → ResortConditions
       b. open_meteo.get_5day_forecast(lat, lon)        → ForecastData
       c. open_meteo.get_historical_reliability(lat, lon, mode) → float (pct)
        │
        ▼
  5. For each resort with fetched data:
       a. engine.apply_gate(conditions, forecast, mode) → GateResult
       b. If passed: engine.compute_score(conditions, forecast, reliability, mode) → ScoreResult
        │
        ▼
  6. Write snow_scores record per resort
  7. Rank passing resorts by score_total descending
  8. Update weekly_runs record (status='complete', resorts_checked, resorts_passed)
        │
        ▼
  9. Emit delivery event (stub in Phase 1 — Phase 4 implements)
```

---

## Project Structure

```
snow-agent/
├── app/
│   ├── __init__.py
│   ├── main.py                     # FastAPI app factory
│   ├── settings.py                 # pydantic-settings Config
│   ├── db/
│   │   ├── __init__.py
│   │   ├── session.py              # SQLAlchemy async engine + session factory
│   │   └── models/
│   │       ├── __init__.py
│   │       ├── resort.py           # Resort ORM model
│   │       ├── weekly_run.py       # WeeklyRun ORM model
│   │       └── snow_score.py       # SnowScore ORM model
│   ├── clients/
│   │   ├── __init__.py
│   │   └── open_meteo.py           # Async Open-Meteo client
│   ├── scoring/
│   │   ├── __init__.py
│   │   ├── engine.py               # Snow Score Engine (gate + scoring)
│   │   └── schemas.py              # Pydantic schemas for scoring I/O
│   ├── runner/
│   │   ├── __init__.py
│   │   └── weekly_run.py           # Weekly run orchestrator
│   └── scheduler/
│       ├── __init__.py
│       └── jobs.py                 # APScheduler job definitions
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial_schema.py  # Baseline migration
├── data/
│   └── resorts.json                # Resort seed data (70 entries)
├── scripts/
│   └── seed_resorts.py             # One-time seeding script
├── tests/
│   ├── __init__.py
│   ├── conftest.py                 # Fixtures: DB, session, test resorts
│   ├── test_gate.py                # Gate logic property tests
│   ├── test_scoring.py             # Score engine property tests
│   ├── test_weekly_run.py          # Weekly run integration + property tests
│   └── test_open_meteo.py          # Open-Meteo client tests (mocked)
├── alembic.ini
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── .env.example
```

---

## Components and Interfaces

### 1. Configuration (`app/settings.py`)

Uses `pydantic-settings` with environment variable loading. All configuration is read at startup; no runtime re-reads.

```python
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Database
    database_url: str = "postgresql+asyncpg://snow:snow@localhost:5432/snow_agent"

    # Application
    default_mode: Literal["sports", "tourism"] = "sports"

    # Open-Meteo
    open_meteo_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    open_meteo_archive_url: str = "https://archive-api.open-meteo.com/v1/archive"
    open_meteo_concurrency: int = 10          # semaphore limit for parallel fetches
    open_meteo_timeout_seconds: float = 30.0
    open_meteo_max_retries: int = 3

    # Scoring
    sports_depth_gate_cm: int = 40
    tourism_depth_gate_cm: int = 15
    forecast_rain_exclude_threshold_mm: float = 0.1
    forecast_temp_exclude_threshold_c: float = 3.0
    historical_years: int = 5

    # Scheduler
    weekly_run_day: str = "fri"
    weekly_run_hour: int = 17
    weekly_run_minute: int = 0
    weekly_run_timezone: str = "UTC"

settings = Settings()
```

### 2. Database Session (`app/db/session.py`)

```python
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from app.settings import settings

engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
```

### 3. Open-Meteo Client (`app/clients/open_meteo.py`)

The client makes three types of calls per resort. All are async using `httpx.AsyncClient`. A shared semaphore enforces concurrency limits across all resorts in a run.

**Interface:**

```python
@dataclass
class ResortConditions:
    resort_id: UUID
    snow_depth_cm: float          # current settled depth (converted from m)
    temperature_c: float          # current temperature at 2m

@dataclass
class DayForecast:
    date: date
    temp_max_c: float
    rain_sum_mm: float
    snowfall_sum_cm: float

@dataclass
class ForecastData:
    resort_id: UUID
    days: list[DayForecast]       # 5 entries

class OpenMeteoClient:
    async def get_current_conditions(
        self, resort_id: UUID, lat: float, lon: float
    ) -> ResortConditions: ...

    async def get_5day_forecast(
        self, resort_id: UUID, lat: float, lon: float
    ) -> ForecastData: ...

    async def get_historical_reliability(
        self,
        resort_id: UUID,
        lat: float,
        lon: float,
        mode: Literal["sports", "tourism"],
    ) -> float:
        """Returns percentage (0.0–100.0) of weekends in last 5 years
        where snow_depth met the mode threshold."""
        ...
```

**API mapping:**

| Method | Open-Meteo endpoint | Key parameters |
|--------|--------------------|-----------------------|
| `get_current_conditions` | `/v1/forecast` | `hourly=snow_depth,temperature_2m`, `forecast_days=1` |
| `get_5day_forecast` | `/v1/forecast` | `daily=temperature_2m_max,rain_sum,snowfall_sum`, `forecast_days=5` |
| `get_historical_reliability` | `/v1/archive` (historical) | `daily=snow_depth`, date range = last 5 years, filtered to Saturdays + Sundays |

**Retry and error handling:**

- Exponential backoff with jitter on 5xx responses and network timeouts
- Max 3 retries per request
- On final failure, raises `OpenMeteoFetchError` with resort_id, endpoint, and HTTP status
- The orchestrator catches `OpenMeteoFetchError` and records the failure in `weekly_runs.error_log`

**Parallelism:**

All per-resort fetches are dispatched as `asyncio.gather` tasks behind a shared `asyncio.Semaphore(concurrency)`. This prevents rate-limit breaches on the free Open-Meteo tier while maximising throughput.

```python
# Within the orchestrator — each resort runs all three fetches sequentially
# but all resorts run in parallel
async def fetch_resort_data(client, resort, sem):
    async with sem:
        conditions = await client.get_current_conditions(...)
        forecast   = await client.get_5day_forecast(...)
        reliability = await client.get_historical_reliability(...)
        return conditions, forecast, reliability

sem = asyncio.Semaphore(settings.open_meteo_concurrency)
results = await asyncio.gather(*[fetch_resort_data(client, r, sem) for r in resorts])
```

### 4. Snow Score Engine (`app/scoring/engine.py`)

The engine is a collection of pure functions with no I/O. All inputs are validated Pydantic models. This makes every function trivially testable.

**Input schemas (`app/scoring/schemas.py`):**

```python
from pydantic import BaseModel, Field
from typing import Literal

class GateInput(BaseModel):
    mode: Literal["sports", "tourism"]
    snow_depth_cm: float
    has_open_run: bool | None = None   # sports mode only; None means unknown
    forecast_days: list[DayForecast]   # 5 entries

class GateResult(BaseModel):
    passed: bool
    reason: str | None = None          # 'depth_below_threshold', 'no_open_run',
                                       # 'rain_in_forecast', 'warm_temperature_forecast'

class ScoreInput(BaseModel):
    mode: Literal["sports", "tourism"]
    snow_depth_cm: float
    forecast_days: list[DayForecast]
    historical_reliability_pct: float = Field(ge=0.0, le=100.0)
    qualitative_score: int = 10        # Phase 1 placeholder — always 10

class ScoreResult(BaseModel):
    score_depth: int = Field(ge=0, le=30)
    score_forecast: int = Field(ge=0, le=30)
    score_historical: int = Field(ge=0, le=25)
    score_qualitative: int = Field(ge=0, le=20)
    score_total: int = Field(ge=0, le=100)
```

**Gate logic (`apply_gate`):**

Conditions are evaluated in priority order. The first failing condition sets the reason and returns immediately.

```
Priority order (highest to lowest):
1. depth < mode_threshold       → depth_below_threshold
2. mode=sports AND no open run  → no_open_run
3. any day rain_sum > 0.1mm     → rain_in_forecast
4. any day temp_max > 3.0°C    → warm_temperature_forecast
```

If all conditions pass → `GateResult(passed=True, reason=None)`.

**Depth scoring (`score_depth`):**

```
sports:
  depth < 20cm  → 0
  20–39cm       → 10
  40–59cm       → 20
  ≥ 60cm        → 30

tourism:
  depth < 10cm  → 0
  10–19cm       → 10
  20–29cm       → 20
  ≥ 30cm        → 30
```

**Forecast stability scoring (`score_forecast`):**

The baseline is computed from mutually exclusive primary conditions, then adjusted:

```
Step 1 — Detect conditions from 5-day forecast:
  has_rain    = any(day.rain_sum_mm > 0.1 for day in forecast)
  has_warm    = any(day.temp_max_c > 3.0 for day in forecast)
  has_snow    = any(day.snowfall_sum_cm > 0 for day in forecast)
  is_stable   = not has_rain and not has_warm

Step 2 — Priority-ordered scoring (negative conditions dominate):
  IF has_rain AND has_warm: base = 0           (worst case)
  ELIF has_rain:            base = 10 - 15 = 0 (clamp to 0)
  ELIF has_warm:            base = 15 - 10 = 5
  ELIF is_stable:           base = 25
  ELSE:                     base = 0

Step 3 — Snowfall bonus (only when base > 0 and no negative conditions):
  IF is_stable AND has_snow: base = min(base + 5, 30)

Step 4 — Clamp to [0, 30]
  score_forecast = max(0, min(30, base))
```

**Historical reliability scoring (`score_historical`):**

```
score_historical = round(historical_reliability_pct * 0.25)
# Result is in [0, 25] since pct is in [0, 100]
```

**Total score (`compute_total`):**

```
score_total = min(100, score_depth + score_forecast + score_historical + score_qualitative)
```

### 5. Weekly Run Orchestrator (`app/runner/weekly_run.py`)

The orchestrator wires together the client, engine, and DB writes. It is responsible for:

- Creating and updating `weekly_runs` records
- Collecting errors without aborting the whole run (P5: fail loud — errors are logged and included in the run's error_log, but individual resort failures don't abort other resorts)
- Ranking passing resorts and marking the top 10

```python
class WeeklyRunOrchestrator:
    async def run(self, mode: Literal["sports", "tourism"]) -> UUID:
        """Execute a full weekly run. Returns the weekly_run_id."""
        ...
```

**DB write sequence:**

1. `INSERT weekly_runs(mode, status='running', started_at=now())`
2. Parallel fetch all resorts (semaphore-limited)
3. For each resort: apply gate, compute score if passed
4. `INSERT snow_scores` for every resort (passed or failed)
5. Compute ranks for passing resorts by `score_total DESC`
6. `UPDATE snow_scores SET rank=...` for top 10 passing resorts
7. `UPDATE weekly_runs SET status='complete', resorts_checked, resorts_passed, completed_at=now()`
8. If any step in 7 fails: `UPDATE weekly_runs SET status='failed'`

### 6. APScheduler Jobs (`app/scheduler/jobs.py`)

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.runner.weekly_run import WeeklyRunOrchestrator
from app.settings import settings

def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.weekly_run_timezone)
    scheduler.add_job(
        func=run_weekly_job,
        trigger=CronTrigger(
            day_of_week=settings.weekly_run_day,
            hour=settings.weekly_run_hour,
            minute=settings.weekly_run_minute,
            timezone=settings.weekly_run_timezone,
        ),
        id="weekly_snow_run",
        name="Weekly Snow Intelligence Run",
        replace_existing=True,
        misfire_grace_time=3600,    # 1 hour grace if app was down at trigger time
    )
    return scheduler

async def run_weekly_job():
    orchestrator = WeeklyRunOrchestrator()
    await orchestrator.run(mode=settings.default_mode)
```

The scheduler is started in the FastAPI `lifespan` context manager so it starts with the app and shuts down cleanly.

---

## Data Models

### SQLAlchemy ORM Models

#### `Resort` (`app/db/models/resort.py`)

```python
import uuid
from sqlalchemy import String, Integer, Boolean, ARRAY, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Resort(Base):
    __tablename__ = "resorts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    country: Mapped[str] = mapped_column(String(50), nullable=False)
    region: Mapped[str | None] = mapped_column(String(100))
    latitude: Mapped[float] = mapped_column(nullable=False)
    longitude: Mapped[float] = mapped_column(nullable=False)
    altitude_base_m: Mapped[int] = mapped_column(Integer, nullable=False)
    altitude_peak_m: Mapped[int | None] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)  # 'sports','tourism','both'
    nearest_airports: Mapped[list[str]] = mapped_column(ARRAY(String(10)), nullable=False)
    piste_count: Mapped[int | None] = mapped_column(Integer)
    lift_count: Mapped[int | None] = mapped_column(Integer)
    webcam_url: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
```

#### `WeeklyRun` (`app/db/models/weekly_run.py`)

```python
class WeeklyRun(Base):
    __tablename__ = "weekly_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_date: Mapped[date] = mapped_column(nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # running|complete|failed
    resorts_checked: Mapped[int | None] = mapped_column(Integer)
    resorts_passed: Mapped[int | None] = mapped_column(Integer)
    error_log: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column()

    scores: Mapped[list["SnowScore"]] = relationship(back_populates="run")
```

#### `SnowScore` (`app/db/models/snow_score.py`)

```python
class SnowScore(Base):
    __tablename__ = "snow_scores"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    weekly_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("weekly_runs.id"))
    resort_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resorts.id"))
    passed_gate: Mapped[bool] = mapped_column(nullable=False)
    gate_failure_reason: Mapped[str | None] = mapped_column(Text)
    depth_cm: Mapped[int | None] = mapped_column(Integer)
    temperature_c: Mapped[float | None] = mapped_column()
    forecast_stable: Mapped[bool | None] = mapped_column()
    forecast_detail: Mapped[dict | None] = mapped_column(JSONB)
    historical_reliability_pct: Mapped[float | None] = mapped_column()
    score_depth: Mapped[int | None] = mapped_column(Integer)
    score_forecast: Mapped[int | None] = mapped_column(Integer)
    score_historical: Mapped[int | None] = mapped_column(Integer)
    score_qualitative: Mapped[int | None] = mapped_column(Integer)
    score_total: Mapped[int | None] = mapped_column(Integer)
    rank: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    run: Mapped["WeeklyRun"] = relationship(back_populates="scores")
    resort: Mapped["Resort"] = relationship()
```

### Alembic Baseline Migration

The initial migration creates all three tables with constraints matching the spec schema verbatim (CHECK constraints on `mode` and `status` columns). It also creates indexes on `snow_scores(weekly_run_id)`, `snow_scores(resort_id)`, and `snow_scores(score_total DESC)` for efficient ranking queries.

---

## Docker Compose

```yaml
# docker-compose.yml
services:
  app:
    build: .
    ports:
      - "8000:8000"
    env_file: .env
    depends_on:
      db:
        condition: service_healthy
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

  db:
    image: postgres:16
    environment:
      POSTGRES_USER: snow
      POSTGRES_PASSWORD: snow
      POSTGRES_DB: snow_agent
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U snow -d snow_agent"]
      interval: 5s
      timeout: 5s
      retries: 10

volumes:
  postgres_data:
```

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml .
RUN uv pip install --system -r pyproject.toml

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

The Snow Score Engine is composed entirely of pure functions that map structured inputs to structured outputs. This makes it an excellent candidate for property-based testing. The PBT library used is [Hypothesis](https://hypothesis.readthedocs.io/en/latest/) for Python.

### Property 1: Depth Gate Exclusion (Mode-Parametric)

*For any* mode value and any snow depth strictly below the mode-appropriate gate threshold (40 cm for `sports`, 15 cm for `tourism`), the gate function SHALL return `passed=False` with `reason='depth_below_threshold'`, regardless of any other input values.

**Validates: Requirements 8.1, 8.3**

### Property 2: Open Run Gate (Sports Mode)

*For any* resort in `sports` mode where `has_open_run` is explicitly `False`, the gate function SHALL return `passed=False` with `reason='no_open_run'`, regardless of snow depth or forecast.

**Validates: Requirement 8.2**

### Property 3: Rain Forecast Gate

*For any* 5-day forecast that contains at least one day where `rain_sum_mm > 0.1`, the gate function SHALL return `passed=False` with `reason='rain_in_forecast'`, regardless of mode or depth.

**Validates: Requirement 8.4**

### Property 4: Warm Forecast Gate

*For any* 5-day forecast that contains at least one day where `temp_max_c > 3.0`, the gate function SHALL return `passed=False` with `reason='warm_temperature_forecast'`, regardless of mode or depth (provided depth gate and rain gate pass first).

**Validates: Requirement 8.5**

### Property 5: Score Total Invariant

*For any* combination of valid component scores (depth ∈ [0,30], forecast ∈ [0,30], historical ∈ [0,25], qualitative ∈ [0,20]), the computed `score_total` SHALL equal `min(100, depth + forecast + historical + qualitative)` exactly, with no rounding error, and the result SHALL always be in [0, 100].

**Validates: Requirements 9.1, 9.7, 9.8**

### Property 6: Depth Score Lookup Correctness (Mode-Parametric)

*For any* mode and *for any* snow depth value, the `score_depth` function SHALL return the score corresponding to the bracket in which that depth falls, as defined in the mode-specific lookup table, with correct boundary handling (e.g., exactly 40 cm in `sports` mode returns 20 pts, not 10 pts).

**Validates: Requirements 9.2, 9.3**

### Property 7: Forecast Score Bounds and Priority

*For any* 5-day forecast, the `score_forecast` function SHALL return a value in [0, 30]. When negative conditions (rain or warm temperature) are present, the score SHALL never exceed the score achievable without any positive conditions (stable cold, snowfall bonus). Specifically: if `has_rain=True` or `has_warm=True`, then `score_forecast ≤ 15`.

**Validates: Requirement 9.4**

### Property 8: Historical Reliability Formula

*For any* set of historical weekend records, the computed `historical_reliability_pct` SHALL equal `(count of weekends where depth ≥ threshold) / (total weekends evaluated) × 100`, and `score_historical` SHALL equal `round(historical_reliability_pct × 0.25)`, with the result in [0, 25].

**Validates: Requirement 9.5**

### Property 9: Weekly Run DB Write Completeness

*For any* resort list of size N passed to the weekly run orchestrator (with all Open-Meteo calls mocked), after the run completes there SHALL be exactly N `snow_scores` records associated with that `weekly_run_id`, and exactly one `weekly_runs` record with `resorts_checked = N`.

**Validates: Requirements 7.4, 7.5**

### Property 10: Top-10 Ranking Correctness

*For any* set of passing resorts with scores, the top-10 resorts stored in `snow_scores` with `rank IS NOT NULL` SHALL be the 10 resorts (or fewer, if fewer passed) with the highest `score_total`, and their ranks SHALL be assigned in descending score order (rank=1 = highest score).

**Validates: Requirement 7.6**

### Property 11: Mode Routing Completeness

*For any* mode value, the weekly run orchestrator SHALL select only resorts whose `mode` column equals the requested mode or `'both'`, and SHALL not select any resorts tagged exclusively for the other mode.

**Validates: Requirements 3.1, 3.2, 29.2**

---

## Error Handling

### Open-Meteo Fetch Failures

Individual resort fetch failures are non-fatal to the weekly run:

- The error is caught per-resort and stored in a `failed_resorts` list
- The resort is omitted from scoring (no `snow_scores` record is written for it in the normal path; a record with `passed_gate=False` and a special `gate_failure_reason='fetch_error'` can optionally be written)
- After all resorts complete, `weekly_runs.error_log` is populated with all failures
- The run proceeds and completes with `status='complete'` even with partial failures
- If all fetches fail, the run writes `status='failed'`

### Database Write Failures

If the final `weekly_runs` status update fails, the run is considered failed and should be re-runnable. Idempotency is ensured by the `run_date` + `mode` combination on `weekly_runs` — a second run on the same day will create a new record rather than overwriting the first.

### Scheduler Misfire

APScheduler is configured with `misfire_grace_time=3600` (1 hour). If the app was down at the trigger time, the job fires as soon as the app comes back up within the grace window.

---

## Testing Strategy

Phase 1 uses a dual testing approach:

**Property-based tests** (Hypothesis, min 100 examples each): validate the Snow Score Engine's pure functions against Properties 1–8 above. These run against pure functions with no DB or network I/O.

**Integration tests** (pytest-asyncio, SQLite in-memory + mocked HTTP): validate the weekly run orchestrator against Properties 9–11 and the Phase 1 spec acceptance criteria. Open-Meteo calls are mocked with `pytest-httpx`.

**Smoke tests**: verify scheduler cron configuration, resort seed counts, and DB schema creation.

Specific test cases from spec Section 12.1:

| Spec test case | Test type | File |
|---|---|---|
| `test_gate_excludes_shallow_sports_depth` | Property (Property 1) | `test_gate.py` |
| `test_gate_excludes_rain_forecast` | Property (Property 3) | `test_gate.py` |
| `test_score_components_sum_correctly` | Property (Property 5) | `test_scoring.py` |
| `test_historical_reliability_calculation` | Property (Property 8) | `test_scoring.py` |
| `test_mode_routing_uses_correct_resort_list` | Property (Property 11) | `test_weekly_run.py` |
| `test_weekly_run_writes_to_db` | Property (Property 9) | `test_weekly_run.py` |

Property tests use `@settings(max_examples=200)` to increase coverage of boundary conditions.
