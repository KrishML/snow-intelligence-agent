# Design Document — Snow Intelligence Agent: Phase 2

## Overview

Phase 2 extends the Phase 1 snow intelligence pipeline with five new subsystems:

1. **Qualitative Signal Scorer** — replaces the hardcoded `score_qualitative=10` placeholder with live Reddit + RSS sentiment scoring via GPT-4o-mini
2. **Webcam Classifier** — adds Playwright screenshot capture and GPT-4o vision classification of resort webcams
3. **LangGraph Chat Agent** — a stateful, memory-aware conversational agent for trip planning, strictly isolated from all booking credentials and endpoints
4. **Memory System** — PostgreSQL + pgvector semantic memory for user preferences, trip feedback, resort knowledge, and session summaries
5. **Delivery System** — Telegram bot and SendGrid email dispatch after each Friday weekly run

All five subsystems integrate with the Phase 1 foundations: FastAPI app, SQLAlchemy ORM, Alembic migrations, weekly run orchestrator, and Snow Score Engine. The existing `WeeklyRunOrchestrator` is extended with two new pipeline steps (qualitative scoring + webcam classification) before the final ranking and delivery trigger.

---

## Architecture

### High-Level Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        APScheduler (Friday 17:00 UTC)                        │
│                                    │                                         │
│                                    ▼                                         │
│                     Weekly Run Orchestrator (extended)                       │
│                                    │                                         │
│        ┌───────────────────────────┼──────────────────────────┐              │
│        ▼                           ▼                          ▼              │
│  Open-Meteo Client           Snow Score Engine         Qualitative           │
│  (Phase 1 — unchanged)       (Phase 1, extended)       Signal Scorer  ◄──┐  │
│                                    │                          │          │  │
│                                    │                          ▼          │  │
│                                    │                   Reddit (praw)     │  │
│                                    │                   RSS (feedparser)  │  │
│                                    │                   GPT-4o-mini       │  │
│                                    │                          │          │  │
│        ┌───────────────────────────┘          ┌───────────────┘          │  │
│        ▼                                      ▼                          │  │
│  Webcam Classifier ──────────────────► qualitative_signals DB            │  │
│  (Playwright + GPT-4o vision)          webcam_snapshots DB               │  │
│        │                                      │                          │  │
│        └──────────────► snow_scores (score_qualitative updated) ─────────┘  │
│                                    │                                         │
│                                    ▼                                         │
│                          Delivery Trigger Event                              │
│                                    │                                         │
│                   ┌────────────────┴──────────────────┐                     │
│                   ▼                                   ▼                     │
│             Telegram Bot                        SendGrid Email               │
│         (python-telegram-bot)                  (sendgrid SDK)                │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                        Chat Agent Service (separate container)               │
│                                                                              │
│   WS /api/chat/session/{id}/messages                                         │
│           │                                                                  │
│           ▼                                                                  │
│   LangGraph StateGraph ──────────────► Tools:                               │
│   (GPT-4o backbone)                     filter_results                       │
│           │                             fetch_live_snow (rate-limited)       │
│           │                             fetch_webcam                         │
│           │                             search_flights_readonly (Amadeus)    │
│           │                             search_hotels_readonly               │
│           │                             get_historical_reliability           │
│           │                             create_plan_artifact                 │
│           │                                                                  │
│           ▼                                                                  │
│   Memory System ──────────────────────► pgvector tables:                    │
│   (retrieval on session start)           preference_embeddings               │
│   (write on session end)                 trip_feedback_embeddings            │
│                                          resort_knowledge_embeddings         │
│                                          session_summary_embeddings          │
│                                                                              │
│   ⛔ NO Duffel key  ⛔ NO Stripe key  ⛔ NO route to /booking/*             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Extended Weekly Run Execution Flow

```
APScheduler fires (Friday 17:00 UTC)
        │
        ▼
WeeklyRunOrchestrator.run()
  Phase 1 steps (unchanged):
  1. Create weekly_runs record (status='running')
  2. Fetch Open-Meteo data in parallel (conditions + forecast + historical)
  3. Apply gate logic → snow_scores records (passed_gate T/F)
  4. Compute Phase 1 score components (depth, forecast, historical)

  Phase 2 additions:
  5. For each passing resort (parallel, semaphore-limited to 5):
       a. QualitativeSignalScorer.score(resort) → qualitative_score, summary, sentiment
       b. WebcamClassifier.classify(resort) → classification, description, file_path
       c. Apply webcam bonus/penalty to qualitative_score
       d. Write qualitative_signals record
       e. Write webcam_snapshots record
       f. Embed summary → write resort_knowledge_embeddings
  6. Update snow_scores.score_qualitative for each passing resort
  7. Re-rank passing resorts by updated score_total
  8. Update weekly_runs status='complete'
        │
        ▼
  9. DeliveryService.dispatch(weekly_run_id)
       a. Build top-5 report (Telegram format)
       b. Send Telegram message
       c. Build HTML email
       d. Send SendGrid email
```

---

## Project Structure (Phase 2 Additions)

```
snow-agent/
├── app/
│   ├── clients/
│   │   ├── open_meteo.py          # Phase 1 — unchanged
│   │   ├── reddit.py              # NEW: praw wrapper for subreddit posts
│   │   ├── rss.py                 # NEW: feedparser RSS client
│   │   ├── openai_client.py       # NEW: shared OpenAI async client (GPT-4o, GPT-4o-mini, embeddings)
│   │   └── amadeus.py             # NEW: Amadeus SDK wrapper (read-only, chat agent only)
│   ├── scoring/
│   │   ├── engine.py              # Phase 1 — minor extension: replace placeholder with live score
│   │   ├── schemas.py             # Phase 1 — unchanged
│   │   └── qualitative.py         # NEW: QualitativeSignalScorer
│   ├── webcam/
│   │   ├── __init__.py
│   │   └── classifier.py          # NEW: WebcamClassifier (Playwright + GPT-4o vision)
│   ├── chat/
│   │   ├── __init__.py
│   │   ├── agent.py               # NEW: LangGraph StateGraph definition
│   │   ├── tools.py               # NEW: all 7 Chat_Agent tools
│   │   ├── memory.py              # NEW: MemorySystem (pgvector read + write)
│   │   └── session.py             # NEW: ChatSession lifecycle manager
│   ├── delivery/
│   │   ├── __init__.py
│   │   ├── telegram_bot.py        # NEW: python-telegram-bot dispatch
│   │   └── email_sender.py        # NEW: SendGrid HTML email dispatch
│   ├── db/
│   │   └── models/
│   │       ├── qualitative_signal.py  # NEW
│   │       ├── webcam_snapshot.py     # NEW
│   │       ├── trip_plan.py           # NEW
│   │       └── embeddings.py          # NEW: all four pgvector ORM models
│   ├── runner/
│   │   └── weekly_run.py          # Phase 1 — extended with steps 5–9
│   └── settings.py                # Phase 1 — extended with Phase 2 settings keys
├── alembic/versions/
│   └── 0002_phase2_schema.py      # NEW: qualitative_signals, webcam_snapshots,
│                                  #      trip_plans, pgvector tables + indexes
├── tests/
│   ├── test_qualitative.py        # NEW: property + integration tests
│   ├── test_webcam.py             # NEW: property + integration tests
│   ├── test_chat_agent.py         # NEW: Chat_Agent unit + integration tests
│   ├── test_memory.py             # NEW: Memory_System property + integration tests
│   └── test_delivery.py           # NEW: Delivery_System integration tests
└── snapshots/                     # NEW: local disk storage for webcam PNGs
```

---

## Components and Interfaces

### 1. Settings Extensions (`app/settings.py`)

Phase 2 adds the following settings keys to the existing `Settings` class:

```python
# OpenAI
openai_api_key: str
openai_model_chat: str = "gpt-4o"
openai_model_qualitative: str = "gpt-4o-mini"
openai_embedding_model: str = "text-embedding-3-small"
openai_vision_timeout_seconds: float = 30.0

# Reddit (praw)
reddit_client_id: str
reddit_client_secret: str
reddit_user_agent: str = "snow-agent/2.0"
reddit_post_limit: int = 10
reddit_lookback_days: int = 7

# Webcam
webcam_timeout_seconds: float = 5.0
webcam_snapshot_dir: str = "snapshots"
webcam_concurrency: int = 5

# Qualitative scoring
qualitative_concurrency: int = 5
qualitative_timeout_minutes: float = 8.0

# Delivery
telegram_bot_token: str
telegram_chat_id: str
sendgrid_api_key: str
sendgrid_from_email: str = "reports@snow-agent.io"
delivery_email_address: str

# Amadeus (chat agent only — NOT in chat container via env isolation)
amadeus_client_id: str = ""
amadeus_client_secret: str = ""
```

### 2. Qualitative Signal Scorer (`app/scoring/qualitative.py`)

```python
from dataclasses import dataclass
from typing import Literal

@dataclass
class QualitativeResult:
    resort_id: UUID
    score: int                          # 0–20
    sentiment: Literal["positive", "neutral", "negative"]
    summary: str
    negative_keywords: list[str]
    sources_used: list[str]             # e.g. ["reddit", "rss"]
    source_text_snippet: str            # truncated to 1000 chars

class QualitativeSignalScorer:
    async def score(
        self, resort: Resort, mode: Literal["sports", "tourism"]
    ) -> QualitativeResult:
        """Orchestrates Reddit + RSS fetch, GPT-4o-mini summarisation,
        keyword detection, and score computation."""
        ...

    def _compute_score(
        self,
        sentiment: Literal["positive", "neutral", "negative"],
        negative_keywords: list[str],
    ) -> int:
        """Pure function: (sentiment, keywords) → score in [0, 20]."""
        ...
```

**Score computation logic:**

```
IF sentiment == 'positive':
    base = 20
    penalty = min(len(negative_keywords) * 2, 5)   # up to 5 pt penalty for mixed signals
    score = max(15, base - penalty)                 # floor at 15 for positive sentiment

ELIF sentiment == 'neutral':
    score = 10

ELIF sentiment == 'negative':
    base = 5
    penalty = min(len(negative_keywords) * 2, 5)   # up to 5 pt penalty
    score = max(0, base - penalty)                  # floor at 0

RETURN clamp(score, 0, 20)
```

**Subreddit selection by mode:**

| mode | subreddits |
|---|---|
| `sports` | `r/skiing`, `r/snowboarding`, `r/Alps` |
| `tourism` | `r/travel`, `r/europe` |

**GPT-4o-mini prompt structure:**

The scorer sends a single structured prompt to GPT-4o-mini requesting a JSON response with three fields: `summary` (string), `sentiment` (`positive`/`neutral`/`negative`), and `negative_keywords` (array). The response is parsed with `json.loads`; on parse failure the scorer falls back to `neutral` / 10 pts.

### 3. Webcam Classifier (`app/webcam/classifier.py`)

```python
@dataclass
class WebcamResult:
    resort_id: UUID
    classification: Literal["snow_good", "snow_poor", "no_snow", "camera_offline"]
    description: str
    file_path: str | None              # None for camera_offline

class WebcamClassifier:
    async def classify(self, resort: Resort) -> WebcamResult:
        """Captures screenshot via Playwright, sends to GPT-4o vision,
        returns structured classification."""
        ...
```

**Playwright capture strategy:**

- Launch headless Chromium via `playwright.async_api`
- `page.goto(resort.webcam_url, timeout=5000)` — timeout is 5 seconds
- `page.screenshot(path=output_path, full_page=False)` — viewport-only screenshot
- On timeout or navigation error → return `WebcamResult(classification='camera_offline', ...)`
- Saved as PNG at `{settings.webcam_snapshot_dir}/{weekly_run_id}/{resort_id}.png`

**GPT-4o vision prompt:**

Single-turn message with the image encoded as base64 data URL. Prompt asks for a JSON response: `{"classification": "snow_good|snow_poor|no_snow|camera_offline", "description": "one sentence"}`.

**Webcam bonus/penalty applied to qualitative score:**

| classification | adjustment |
|---|---|
| `snow_good` | +3 pts (capped at 20) |
| `snow_poor` | −2 pts (floored at 0) |
| `no_snow` | −5 pts (floored at 0) |
| `camera_offline` | 0 (no adjustment) |

### 4. LangGraph Chat Agent (`app/chat/agent.py`)

The Chat_Agent is a LangGraph `StateGraph` with the following node/edge structure:

```
START → agent_node → [tools_node | END]
              ↑____________|
```

**State schema:**

```python
from typing import Annotated
from langgraph.graph import StateGraph, MessagesState
from langchain_core.messages import BaseMessage

class AgentState(MessagesState):
    session_id: str
    user_id: str
    memory_context: str             # injected at session start
    live_calls_remaining: int       # starts at 10; decremented per fetch_live_snow call
    plan_artifact_created: bool     # True once create_plan_artifact is called
```

**Agent node:**

The `agent_node` calls GPT-4o with the bound tools. The system prompt is constructed from the base instruction template + injected `memory_context` string. Tool calls are dispatched to `tools_node`.

**Tool registry (7 tools, registered via `@tool` decorator):**

1. `filter_results(min_depth_cm, country, min_score, altitude_range)` — queries `snow_scores` / `resorts` from weekly cache
2. `fetch_live_snow(resort_id)` — calls Open-Meteo live; decrements `live_calls_remaining`; raises if exhausted
3. `fetch_webcam(resort_id)` — returns cached `webcam_snapshots` entry; falls back to live Playwright capture if no cache
4. `search_flights_readonly(origin_iata, destination_iata, outbound_date, return_date)` — calls Amadeus SDK
5. `search_hotels_readonly(resort_id, check_in, check_out, max_results=5)` — placeholder returning mock data (Phase 2 scope)
6. `get_historical_reliability(resort_id)` — reads from `snow_scores` historical reliability columns
7. `create_plan_artifact(resort_id, outbound_date, return_date, flight_option, hotel_option, total_gbp, notes)` — writes `trip_plans` record; callable once per session

**Booking Firewall enforcement:**

The Chat_Agent Docker service's `env_file` explicitly excludes `DUFFEL_API_KEY`, `STRIPE_API_KEY`, and hotel credentials. No booking tools are registered in the tool list. A startup assertion in `app/chat/agent.py` verifies the tool registry contains only the 7 permitted tools and raises `RuntimeError` otherwise.

### 5. Memory System (`app/chat/memory.py`)

```python
class MemorySystem:
    async def retrieve_context(
        self, user_id: str, query_text: str
    ) -> str:
        """Embeds query_text, runs 4 parallel pgvector queries,
        formats results into a context string for system prompt injection."""
        ...

    async def extract_and_write(
        self, user_id: str, session_id: str, messages: list[BaseMessage]
    ) -> None:
        """Called on session end. Uses GPT-4o-mini to extract preferences,
        deduplicates, writes embeddings."""
        ...

    async def _embed(self, text: str) -> list[float]:
        """Calls text-embedding-3-small, returns 1536-dim vector."""
        ...

    async def _deduplicate_preference(
        self, user_id: str, embedding: list[float]
    ) -> bool:
        """Returns True if an embedding with cosine similarity >= 0.95 already exists."""
        ...
```

**Retrieval query (4 parallel pgvector cosine queries):**

```sql
-- preference_embeddings
SELECT text FROM preference_embeddings
WHERE user_id = :user_id
ORDER BY embedding <=> :query_vec
LIMIT 3;

-- trip_feedback_embeddings
SELECT text FROM trip_feedback_embeddings
WHERE user_id = :user_id
ORDER BY embedding <=> :query_vec
LIMIT 2;

-- resort_knowledge_embeddings (current season filter)
SELECT text FROM resort_knowledge_embeddings
WHERE season = :current_season
ORDER BY embedding <=> :query_vec
LIMIT 3;

-- session_summary_embeddings (last 30 days)
SELECT text FROM session_summary_embeddings
WHERE user_id = :user_id
  AND created_at > now() - interval '30 days'
ORDER BY embedding <=> :query_vec
LIMIT 1;
```

The `<=>` operator is pgvector's cosine distance operator (lower = more similar). Results are concatenated into a single context string separated by section headers.

**Season computation:**

```python
def current_season() -> str:
    """Returns e.g. '2024/25' based on today's date.
    Season starts in October (month >= 10 → year/year+1, else year-1/year)."""
    today = date.today()
    if today.month >= 10:
        return f"{today.year}/{str(today.year + 1)[2:]}"
    return f"{today.year - 1}/{str(today.year)[2:]}"
```

### 6. Delivery System (`app/delivery/`)

**Telegram (`telegram_bot.py`):**

```python
class TelegramDelivery:
    async def send_weekly_report(
        self, top5: list[SnowScoreWithResort], run_id: UUID
    ) -> bool:
        """Formats and sends the top-5 weekly report message.
        Returns True on success, False on failure."""
        ...

    async def send_failure_notification(
        self, failed_components: list[str], run_id: UUID
    ) -> bool: ...
```

**Telegram message format:**

```
🏔 Snow Report — {date}

1. {resort_name} ({country}) — {score}/100
   ❄️ {depth_cm}cm  📊 {reliability}% reliable
   {qualitative_summary}

2. ...  (top 5 entries)

Powered by Snow Intelligence Agent
```

**Email (`email_sender.py`):**

```python
class SendGridDelivery:
    async def send_weekly_report(
        self, top5: list[SnowScoreWithResort], run_id: UUID, recipient: str
    ) -> bool: ...

    async def send_failure_notification(
        self, failed_components: list[str], run_id: UUID, recipient: str
    ) -> bool: ...
```

The HTML email is constructed from a Jinja2 template stored at `app/delivery/templates/weekly_report.html`. It includes a resort table with the same top-5 data as the Telegram message plus a per-resort score breakdown row.

**Delivery trigger in orchestrator:**

```python
async def _trigger_delivery(self, weekly_run_id: UUID, success: bool):
    telegram = TelegramDelivery(settings)
    email = SendGridDelivery(settings)
    top5 = await self._load_top5(weekly_run_id)

    if success:
        tg_ok = await telegram.send_weekly_report(top5, weekly_run_id)
        if tg_ok:
            await email.send_weekly_report(top5, weekly_run_id, settings.delivery_email_address)
    else:
        failed = await self._load_error_log(weekly_run_id)
        await telegram.send_failure_notification(failed, weekly_run_id)
        await email.send_failure_notification(failed, weekly_run_id, settings.delivery_email_address)
```

---

## Data Models

### New ORM Models

#### `QualitativeSignal` (`app/db/models/qualitative_signal.py`)

```python
class QualitativeSignal(Base):
    __tablename__ = "qualitative_signals"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    weekly_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("weekly_runs.id"), nullable=False)
    resort_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resorts.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)  # CHECK IN ('reddit','rss')
    raw_snippet: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    sentiment: Mapped[str | None] = mapped_column(String(20))   # CHECK IN ('positive','neutral','negative')
    negative_keywords: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    score: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
```

#### `WebcamSnapshot` (`app/db/models/webcam_snapshot.py`)

```python
class WebcamSnapshot(Base):
    __tablename__ = "webcam_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    weekly_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("weekly_runs.id"), nullable=False)
    resort_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resorts.id"), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(nullable=False)
    classification: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
```

#### `TripPlan` (`app/db/models/trip_plan.py`)

```python
class TripPlan(Base):
    __tablename__ = "trip_plans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[str] = mapped_column(String(100), nullable=False)
    resort_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resorts.id"), nullable=False)
    outbound_date: Mapped[date] = mapped_column(nullable=False)
    return_date: Mapped[date] = mapped_column(nullable=False)
    flight_option: Mapped[dict | None] = mapped_column(JSONB)
    hotel_option: Mapped[dict | None] = mapped_column(JSONB)
    total_estimated_gbp: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    session_notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(onupdate=func.now())
```

#### pgvector Embedding Models (`app/db/models/embeddings.py`)

The pgvector `Vector` type is provided by `pgvector.sqlalchemy`:

```python
from pgvector.sqlalchemy import Vector

class PreferenceEmbedding(Base):
    __tablename__ = "preference_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1536), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

# TripFeedbackEmbedding, ResortKnowledgeEmbedding, SessionSummaryEmbedding
# follow the same pattern with relevant extra columns (resort_id, season, session_id)
```

### Alembic Migration `0002_phase2_schema.py`

Key migration steps:
1. `CREATE EXTENSION IF NOT EXISTS vector` (pgvector extension)
2. Create `qualitative_signals` with CHECK constraints and GIN index on `negative_keywords`
3. Create `webcam_snapshots` with CHECK constraint on `classification`; composite index on `(resort_id, captured_at DESC)`
4. Create `trip_plans` with CHECK constraint on `status`
5. Create all four pgvector tables with `vector(1536)` columns
6. Create IVFFlat indexes: `CREATE INDEX ON {table} USING ivfflat (embedding vector_cosine_ops) WITH (lists=100)`

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

Phase 2 introduces pure-function components (qualitative score computation, webcam score adjustment) and a stateful data system (Memory System pgvector round-trip) that are well-suited to property-based testing. External I/O components (Reddit, OpenAI, Telegram, SendGrid) are tested with integration tests using mocks.

The PBT library used is [Hypothesis](https://hypothesis.readthedocs.io/en/latest/), consistent with Phase 1.

### Property 1: Qualitative Score Bounds and Monotonicity

*For any* sentiment classification and *for any* list of negative keywords, the `_compute_score` pure function SHALL return a value in [0, 20], and `score(positive, keywords) >= score(neutral, keywords) >= score(negative, keywords)` SHALL hold for the same keyword list.

**Validates: Requirements 10.4**

### Property 2: Qualitative DB Write Completeness

*For any* list of N passing resorts (with all external calls mocked), after the Qualitative_Signal_Scorer processes them, there SHALL be exactly N `qualitative_signals` records associated with that `weekly_run_id`.

**Validates: Requirements 10.5**

### Property 3: Webcam Snapshot DB Write Completeness

*For any* list of N passing resorts submitted to the Webcam_Classifier (with Playwright mocked), after processing there SHALL be exactly N `webcam_snapshots` records associated with that `weekly_run_id`, including one record per resort with a NULL `webcam_url` recorded as `camera_offline`.

**Validates: Requirements 11.1, 11.4**

### Property 4: Webcam Bonus/Penalty Bounds and Correctness

*For any* base qualitative score in [0, 20] and *for any* webcam classification, applying the webcam adjustment SHALL produce a result in [0, 20], specifically: `snow_good` → `min(20, base + 3)`, `snow_poor` → `max(0, base - 2)`, `no_snow` → `max(0, base - 5)`, `camera_offline` → `base` unchanged.

**Validates: Requirements 11.5**

### Property 5: fetch_live_snow Rate Limit Enforcement

*For any* sequence of N calls to `fetch_live_snow` within a single chat session where N > 10, calls 1–10 SHALL succeed and calls 11 through N SHALL raise a rate-limit error without invoking the Open-Meteo API.

**Validates: Requirements 12.6**

### Property 6: Memory Retrieval Count Bounds

*For any* pgvector embedding store with K entries, a retrieval query requesting at most M results SHALL return min(K, M) results, never more than M, and the returned results SHALL be ordered by cosine similarity descending.

**Validates: Requirements 13.1**

### Property 7: Preference Deduplication Idempotence

*For any* preference text that has already been written to `preference_embeddings` for a given user, attempting to write the same text again SHALL leave the count of that user's preference embeddings unchanged (the duplicate write is blocked by the 0.95 cosine similarity threshold).

**Validates: Requirements 13.5**

### Property 8: Preference Embedding Round-Trip

*For any* preference text written to `preference_embeddings`, querying the `preference_embeddings` table with the embedding of the original text as the query vector SHALL return that record as the top result (rank 1 by cosine similarity).

**Validates: Requirements 13.7**

---

## Error Handling

### Qualitative Signal Scorer

- **Reddit rate-limit / auth failure**: caught per-resort; affected resorts fall back to RSS only. If RSS also fails, neutral score (10 pts) is assigned. All failures appended to `weekly_runs.error_log`.
- **GPT-4o-mini parse failure**: JSON parse error on structured response → log warning, assign neutral score (10 pts) and empty keywords.
- **Concurrency**: `asyncio.Semaphore(settings.qualitative_concurrency)` (default 5) prevents hammering Reddit and OpenAI simultaneously.

### Webcam Classifier

- **Playwright timeout (5s)**: returns `camera_offline`; continues to next resort without raising.
- **GPT-4o vision parse failure**: on invalid JSON response → falls back to `camera_offline`.
- **Snapshot directory missing**: `Path(settings.webcam_snapshot_dir).mkdir(parents=True, exist_ok=True)` called at startup.

### Chat Agent

- **Tool failure**: LangGraph `ToolNode` catches exceptions from individual tools and returns an error message to the agent node; the agent then explains the failure to the user rather than propagating a 500.
- **Session timeout**: LangGraph `StateGraph` checkpointer stores state in Redis (`session:{session_id}:state`); on WebSocket reconnect the state is restored.
- **Memory retrieval failure**: if pgvector query times out (>500ms), the agent proceeds without memory context and logs a warning; the user is not notified.

### Memory System

- **Embedding API failure**: on OpenAI embedding error, the Memory_System logs the error and skips the write; it does not raise to the caller.
- **Deduplication query failure**: on pgvector query error during deduplication check, the Memory_System blocks the write entirely (fail-safe: do not write rather than write duplicate).

### Delivery System

- **Telegram failure**: logged; email dispatch is skipped (as per Requirement 14.2 — email depends on successful Telegram dispatch).
- **SendGrid failure**: logged; weekly run is marked complete regardless (delivery failure does not fail the run).
- **Both channels fail**: `weekly_runs.error_log` is updated with delivery failure details.

---

## Testing Strategy

Phase 2 uses the same dual-testing approach as Phase 1:

**Property-based tests** (Hypothesis, `@settings(max_examples=200)`): validate pure functions and data invariants against Properties 1–8 above.

**Integration tests** (pytest-asyncio, mocked external calls): validate wiring between components — Reddit client → scorer → DB write, Playwright → vision API → DB write, session lifecycle → memory retrieval/write, delivery trigger → Telegram/SendGrid.

**Smoke tests**: verify startup assertions (tool registry, booking firewall environment check, pgvector extension enabled, Alembic migration applied).

**Test isolation strategy:**

- OpenAI calls mocked with `pytest-mock` (`AsyncMock` for async methods)
- praw calls mocked with fixture returning pre-built `Submission` objects
- Playwright mocked at the `async_playwright()` context manager level using `AsyncMock`
- Telegram and SendGrid clients mocked to capture sent payloads without network calls
- pgvector tests run against a real PostgreSQL test instance with pgvector extension (Docker Compose test profile)

**Key test files and coverage targets:**

| File | Covers |
|---|---|
| `tests/test_qualitative.py` | Properties 1–2; integration for Reddit/RSS/GPT mock; error fallback |
| `tests/test_webcam.py` | Properties 3–4; Playwright mock; GPT-4o vision mock |
| `tests/test_chat_agent.py` | Property 5; booking firewall smoke; session lifecycle |
| `tests/test_memory.py` | Properties 6–8; pgvector round-trip; deduplication |
| `tests/test_delivery.py` | Telegram/SendGrid integration; failure notification path |

Property tests use `@settings(max_examples=200, deadline=None)` consistent with Phase 1 conventions.
