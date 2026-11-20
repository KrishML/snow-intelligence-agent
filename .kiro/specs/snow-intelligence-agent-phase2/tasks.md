# Implementation Plan: Snow Intelligence Agent — Phase 2

## Overview

Phase 2 extends the Phase 1 pipeline with five new subsystems built on top of the existing FastAPI app, SQLAlchemy models, and weekly run orchestrator. All implementation uses Python 3.11+, with the following new dependencies added to `pyproject.toml`: `langchain-openai`, `langgraph`, `praw`, `feedparser`, `playwright`, `pgvector`, `python-telegram-bot`, `sendgrid`, `amadeus`, `jinja2`, `asyncpg` (already present), and `hypothesis` (already present).

The tasks follow the extended weekly run flow: database schema first, then new signal sources (qualitative + webcam), then chat agent + memory, then delivery, then orchestrator wiring.

---

## Tasks

- [x] 1. Extend settings and add Phase 2 dependencies
  - Add Phase 2 dependencies to `pyproject.toml` with pinned versions: `langgraph`, `langchain-openai`, `praw`, `feedparser`, `playwright`, `pgvector`, `python-telegram-bot`, `sendgrid`, `amadeus`, `jinja2`
  - Extend `app/settings.py` with all Phase 2 settings keys: `openai_api_key`, `openai_model_chat`, `openai_model_qualitative`, `openai_embedding_model`, `reddit_client_id`, `reddit_client_secret`, `reddit_user_agent`, `webcam_timeout_seconds`, `webcam_snapshot_dir`, `telegram_bot_token`, `telegram_chat_id`, `sendgrid_api_key`, `sendgrid_from_email`, `delivery_email_address`, `qualitative_concurrency`, `webcam_concurrency`, `amadeus_client_id`, `amadeus_client_secret`
  - Update `.env.example` with all new keys (no real values)
  - Run `playwright install chromium` as a Dockerfile step
  - _Requirements: 10.1, 11.1, 12.1, 13.1, 14.1_

- [x] 2. Phase 2 Alembic migration
  - [x] 2.1 Create Alembic migration `0002_phase2_schema.py`
    - `CREATE EXTENSION IF NOT EXISTS vector` as first migration step
    - Create `qualitative_signals` table with all columns and CHECK constraints from design; add GIN index on `negative_keywords`
    - Create `webcam_snapshots` table with CHECK constraint on `classification`; add composite index on `(resort_id, captured_at DESC)`
    - Create `trip_plans` table with CHECK constraint on `status`
    - Create four pgvector tables: `preference_embeddings`, `trip_feedback_embeddings`, `resort_knowledge_embeddings`, `session_summary_embeddings`, each with `vector(1536)` column
    - Create IVFFlat cosine similarity indexes on each pgvector table: `CREATE INDEX USING ivfflat (embedding vector_cosine_ops) WITH (lists=100)`
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5_

  - [x] 2.2 Create SQLAlchemy ORM models for Phase 2 tables
    - Implement `app/db/models/qualitative_signal.py` matching the `qualitative_signals` schema exactly
    - Implement `app/db/models/webcam_snapshot.py` matching the `webcam_snapshots` schema exactly
    - Implement `app/db/models/trip_plan.py` matching the `trip_plans` schema exactly
    - Implement `app/db/models/embeddings.py` with all four pgvector ORM models using `pgvector.sqlalchemy.Vector(1536)`
    - _Requirements: 15.1, 15.2, 15.3, 15.4_

  - [~]* 2.3 Write smoke tests for Phase 2 schema
    - Apply `alembic upgrade head` against test DB and assert all Phase 2 tables exist
    - Assert CHECK constraints reject invalid `source_type`, `sentiment`, `classification`, and `status` values
    - Assert pgvector extension is enabled and `vector(1536)` columns are queryable with the `<=>` operator
    - Metadata tests and a temporary PostgreSQL/pgvector migration/query check are complete; automated live-database smoke/property tests remain.
    - _Requirements: 15.1–15.5_

- [x] 3. OpenAI async client
  - [x] 3.1 Implement shared OpenAI async client (`app/clients/openai_client.py`)
    - Wrap `langchain_openai.ChatOpenAI` for GPT-4o-mini text completions with structured JSON output (`response_format={"type": "json_object"}`)
    - Wrap `openai.AsyncOpenAI` for GPT-4o vision completions (base64 image input)
    - Wrap `openai.AsyncOpenAI` embeddings for `text-embedding-3-small` returning `list[float]` of length 1536
    - All three methods must be async and respect `settings.openai_api_key`
    - _Requirements: 10.3, 11.2, 13.1_

  - [x]* 3.2 Write integration tests for OpenAI client
    - Mock `openai.AsyncOpenAI` and `langchain_openai.ChatOpenAI` using `AsyncMock`
    - Verify GPT-4o-mini returns parsed JSON dict from structured completion
    - Verify embedding returns a list of exactly 1536 floats
    - Verify vision completion handles base64 image input correctly
    - _Requirements: 10.3, 11.2_

- [x] 4. Reddit and RSS clients
  - [x] 4.1 Implement Reddit client (`app/clients/reddit.py`)
    - Wrap `praw.Reddit` in an async-compatible helper (use `asyncio.to_thread` since praw is synchronous)
    - `fetch_posts(resort_name: str, mode: str) -> list[str]` — searches mode-appropriate subreddits for posts mentioning the resort name within `settings.reddit_lookback_days`, returns up to `settings.reddit_post_limit` post title + body strings
    - On `praw.exceptions.PRAWException` or any `Exception`: log error and return empty list (caller handles fallback)
    - _Requirements: 10.1, 10.7_

  - [x] 4.2 Implement RSS client (`app/clients/rss.py`)
    - `fetch_entries(resort_name: str) -> list[str]` — constructs SnowForecast.com RSS URL for resort, calls `feedparser.parse()`, returns up to 5 entry title + summary strings
    - On feedparser parse error or empty feed: return empty list
    - _Requirements: 10.2_

  - [x]* 4.3 Write integration tests for Reddit and RSS clients
    - Mock `praw.Reddit` with fixture returning pre-built `Submission` objects; verify mode-based subreddit selection (sports → `r/skiing,r/snowboarding,r/Alps`; tourism → `r/travel,r/europe`)
    - Mock `feedparser.parse()` with a fixture RSS dict; verify 5 entries are returned and correctly formatted
    - Verify that praw exceptions return empty list without raising
    - _Requirements: 10.1, 10.2, 10.7_

- [x] 5. Qualitative Signal Scorer
  - [x] 5.1 Implement `_compute_score` pure function and `QualitativeSignalScorer`
    - Implement `_compute_score(sentiment, negative_keywords) -> int` as a pure function using the priority logic in the design: positive base=20, neutral=10, negative base=5; apply keyword penalty `min(len(keywords)*2, 5)`; clamp result to [0, 20]
    - Implement `QualitativeSignalScorer.score(resort, mode)` orchestrating: Reddit fetch → RSS fetch → text aggregation → GPT-4o-mini structured prompt → JSON parse → score computation → `QualitativeResult`
    - On GPT-4o-mini JSON parse failure: log warning, return `QualitativeResult(score=10, sentiment='neutral', ...)`
    - Wrap all external calls in `asyncio.Semaphore(settings.qualitative_concurrency)`
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.7_

  - [x]* 5.2 Write property test for qualitative score computation
    - Implement `tests/test_qualitative.py` with `@given` / `@settings(max_examples=200, deadline=None)`
    - **Property 1: Qualitative Score Bounds and Monotonicity** — generate all combinations of sentiment and keyword list length [0, 5]; assert result ∈ [0, 20]; assert `score(positive, kw) >= score(neutral, kw) >= score(negative, kw)` for same keyword count
    - **Validates: Requirements 10.4**

  - [x] 5.3 Implement `qualitative_signals` DB write and `resort_knowledge_embeddings` embed/write
    - After `QualitativeSignalScorer.score()` returns, write one `QualitativeSignal` ORM record per resort to `qualitative_signals` (truncate `raw_snippet` to 1000 chars)
    - Call `openai_client.embed(result.summary)` → write `ResortKnowledgeEmbedding` record with `resort_id`, `season=current_season()`, and the embedding vector
    - _Requirements: 10.5, 10.6_

  - [-]* 5.4 Write property test for qualitative DB write completeness
    - **Property 2: Qualitative DB Write Completeness** — generate resort lists of size N (1–30) with all external calls mocked; after `QualitativeSignalScorer` processes all N resorts, assert exactly N `qualitative_signals` records exist for that `weekly_run_id`
    - **Validates: Requirements 10.5**

- [x] 6. Checkpoint — qualitative signal scorer
  - Ensure all qualitative scorer tests pass, ask the user if questions arise.

- [x] 7. Webcam Classifier
  - [x] 7.1 Implement `WebcamClassifier` with Playwright capture and GPT-4o vision classification
    - Implement `WebcamClassifier.classify(resort) -> WebcamResult` using `playwright.async_api.async_playwright()`
    - On NULL `webcam_url`: immediately return `WebcamResult(classification='camera_offline', description='No webcam URL configured', file_path=None)`
    - Launch headless Chromium; `page.goto(url, timeout=5000)`; on timeout or nav error: return `camera_offline`
    - On successful capture: save PNG to `{settings.webcam_snapshot_dir}/{weekly_run_id}/{resort_id}.png`; send base64-encoded image to GPT-4o vision via `openai_client`; parse JSON response for `classification` and `description`
    - On GPT-4o JSON parse failure: fall back to `camera_offline`
    - _Requirements: 11.1, 11.2, 11.3_

  - [x] 7.2 Implement webcam bonus/penalty adjuster and `webcam_snapshots` DB write
    - Implement `apply_webcam_adjustment(base_score: int, classification: str) -> int` as a pure function: `snow_good` → `min(20, base+3)`, `snow_poor` → `max(0, base-2)`, `no_snow` → `max(0, base-5)`, `camera_offline` → `base`
    - Write `WebcamSnapshot` ORM record to `webcam_snapshots` for every resort processed (including `camera_offline` cases)
    - _Requirements: 11.4, 11.5_

  - [~]* 7.3 Write property tests for webcam classifier
    - Implement `tests/test_webcam.py` with `@given` / `@settings(max_examples=200, deadline=None)`
    - **Property 3: Webcam Snapshot DB Write Completeness** — generate resort lists with mixed NULL/non-NULL webcam URLs; mock Playwright and GPT-4o vision; assert exactly N records written (one per resort) with `camera_offline` for NULL URLs
    - **Property 4: Webcam Bonus/Penalty Bounds and Correctness** — generate base scores ∈ [0, 20] and all four classifications; assert result ∈ [0, 20] and matches the exact formula
    - **Validates: Requirements 11.1, 11.4, 11.5**
    - Focused classifier/adjustment tests and a PostgreSQL write check pass; generated property tests against PostgreSQL remain.

- [x] 8. Integrate qualitative + webcam into weekly run orchestrator
  - [x] 8.1 Extend `WeeklyRunOrchestrator` with Phase 2 pipeline steps
    - After Phase 1 gate/scoring step, add step 5: parallel qualitative + webcam scoring for all passing resorts (semaphore-limited to `settings.qualitative_concurrency`)
    - For each passing resort: call `QualitativeSignalScorer.score()`, call `WebcamClassifier.classify()`, call `apply_webcam_adjustment()`, accumulate results
    - Step 6: update `snow_scores.score_qualitative` for all passing resorts with the computed adjusted score
    - Step 7: re-rank passing resorts by updated `score_total DESC`; update `rank` fields
    - Step 8: trigger delivery event (implemented in task 12)
    - _Requirements: 10.1–10.8, 11.1–11.5_

  - [~]* 8.2 Write integration test for extended weekly run orchestrator
    - Mock Open-Meteo, praw, feedparser, OpenAI, and Playwright; run `WeeklyRunOrchestrator.run()` end-to-end
    - Assert `snow_scores.score_qualitative` is no longer hardcoded to 10 for all resorts
    - Assert `qualitative_signals` records exist for each passing resort
    - Assert `webcam_snapshots` records exist for each passing resort
    - Assert final `score_total` reflects updated qualitative score
    - _Requirements: 10.5, 11.4_
    - End-to-end behavior was exercised against temporary PostgreSQL; a repeatable automated integration test remains.

- [~] 9. Memory System
  - [x] 9.1 Implement `MemorySystem.retrieve_context()`
    - Embed `query_text` using `openai_client.embed()` → 1536-dim vector
    - Execute 4 parallel pgvector cosine similarity queries using SQLAlchemy async raw SQL with the `<=>` operator
    - Respect the `LIMIT` values from Requirement 13.1: 3, 2, 3, 1 for the four tables; filter `resort_knowledge_embeddings` by `current_season()`; filter `session_summary_embeddings` by `created_at > now() - interval '30 days'`
    - Concatenate results into a formatted context string with section headers
    - Return empty string (not an error) if no embeddings exist yet
    - _Requirements: 13.1, 13.2_

  - [~]* 9.2 Write property tests for memory retrieval
    - Implement `tests/test_memory.py` with `@given` / `@settings(max_examples=100, deadline=None)` against a real PostgreSQL test DB with pgvector extension
    - **Property 6: Memory Retrieval Count Bounds** — populate embedding store with K random embeddings (K varies 0–20); query with max M; assert returned count == min(K, M) and results are ordered by similarity
    - **Validates: Requirements 13.1**
    - Focused mocked tests and a temporary PostgreSQL retrieval check pass; generated live-database property tests remain.

  - [x] 9.3 Implement `MemorySystem._deduplicate_preference()` and `extract_and_write()`
    - `_deduplicate_preference(user_id, embedding) -> bool`: query `preference_embeddings` for user, return True if any existing embedding has cosine similarity >= 0.95 (i.e., cosine distance `<=>` <= 0.05)
    - `extract_and_write(user_id, session_id, messages)`: call GPT-4o-mini with session transcript to extract discrete preference statements; for each: embed → deduplicate check → write only if not duplicate
    - Write one `SessionSummaryEmbedding` record per session end
    - _Requirements: 13.3, 13.4, 13.5, 13.6_

  - [~]* 9.4 Write property tests for preference deduplication and round-trip
    - **Property 7: Preference Deduplication Idempotence** — for any preference text already written, writing the same text again should not change the count of that user's preference embeddings
    - **Property 8: Preference Embedding Round-Trip** — for any preference text written, querying with the original text embedding should return that record as rank-1 result
    - **Validates: Requirements 13.5, 13.7**
    - Focused unit tests and PostgreSQL memory write/retrieval checks pass; generated deduplication and round-trip property tests remain.

- [~] 10. LangGraph Chat Agent
  - [x] 10.1 Implement LangGraph `StateGraph` and tool registry (`app/chat/agent.py`)
    - Define `AgentState` extending `MessagesState` with `session_id`, `user_id`, `memory_context`, `live_calls_remaining=10`, `plan_artifact_created=False`
    - Create `StateGraph(AgentState)` with `agent_node` (GPT-4o + bound tools) and `tools_node` (LangGraph `ToolNode`)
    - Add edges: `START → agent_node → conditional(tools_node | END)` → `agent_node`
    - Compile graph with an in-memory checkpointer (LangGraph `MemorySaver`)
    - Add startup assertion: `assert len(graph.get_tools()) == 7, "Unexpected tool count"` — raises `RuntimeError` if violated
    - _Requirements: 12.1, 12.2_

  - [~] 10.2 Implement all 7 Chat_Agent tools (`app/chat/tools.py`)
    - `filter_results`: async function querying `snow_scores JOIN resorts` with optional filters (min_depth, country, min_score, altitude_range)
    - `fetch_live_snow`: async function calling `OpenMeteoClient.get_current_conditions()` + `get_5day_forecast()`; decrements `state.live_calls_remaining`; raises `ToolError("Rate limit reached")` if `live_calls_remaining <= 0`
    - `fetch_webcam`: async function returning latest `WebcamSnapshot` for resort; on no cache, calls `WebcamClassifier.classify()` live
    - `search_flights_readonly`: async function calling Amadeus Python SDK `shopping.flight_offers_search.get()`; returns top 5 offers formatted as dicts
    - `search_hotels_readonly`: reports hotel search as unavailable (mock hotel data from the original placeholder requirement is not implemented; real hotel API integration is Phase 7)
    - `get_historical_reliability`: reads `snow_scores.historical_reliability_pct` for the latest weekly run for the resort
    - `create_plan_artifact`: writes `TripPlan` ORM record to DB with `status='draft'`; sets `state.plan_artifact_created=True`; raises `ToolError` if called again in same session
    - _Requirements: 12.6, 12.7, 12.8, 13.1_

  - [~] 10.3 Implement `ChatSession` lifecycle manager (`app/chat/session.py`)
    - `create_session(user_id) -> str`: generate UUID session ID, initialise `AgentState`, call `MemorySystem.retrieve_context()`, inject context into system prompt, return session ID
    - `send_message(session_id, user_message) -> AsyncIterator[str]`: invoke LangGraph graph with streaming; yield tokens as they arrive
    - `end_session(session_id)`: collect full message history from graph state, call `MemorySystem.extract_and_write()` asynchronously, clear session from checkpointer
    - _Requirements: 12.3, 12.4, 12.5_
    - Active session history is in-process; Redis-backed 24-hour session persistence remains unimplemented.

  - [~]* 10.4 Write property test for rate limit enforcement
    - Implement `tests/test_chat_agent.py`
    - **Property 5: fetch_live_snow Rate Limit Enforcement** — generate call sequences of length N (N ranges 1–20); for any N > 10, calls 1–10 succeed and calls 11+ raise `ToolError`; for N <= 10, all calls succeed
    - **Validates: Requirements 12.6**
    - A focused test verifies ten successful calls and rejection of the eleventh; generated call-sequence property coverage remains.

  - [~]* 10.5 Write smoke test for booking firewall
    - Assert `app/chat/agent.py` tool registry contains exactly 7 tools with names matching the permitted list
    - Assert none of the registered tools reference Duffel, Stripe, or hotel booking endpoints
    - Assert `DUFFEL_API_KEY`, `STRIPE_API_KEY` are not present in the Chat_Agent's `os.environ` during testing
    - _Requirements: 12.2_
    - Exact seven-tool registry and no-booking-tool behavior are tested; the environment-variable assertions remain.

- [x] 11. Checkpoint — chat agent and memory
  - Ensure all chat agent and memory tests pass, ask the user if questions arise.

- [x] 12. Delivery System
  - [x] 12.1 Implement Telegram delivery (`app/delivery/telegram_bot.py`)
    - Implement `TelegramDelivery.send_weekly_report(top5, run_id) -> bool` using `python_telegram_bot.Bot.send_message()`
    - Format message using the template from the design: emoji headers, ranked list with depth/score/reliability/summary per resort
    - Implement `TelegramDelivery.send_failure_notification(failed_components, run_id) -> bool`
    - On `telegram.error.TelegramError`: log error, return `False`
    - _Requirements: 14.1, 14.3_

  - [x] 12.2 Implement SendGrid email delivery (`app/delivery/email_sender.py`)
    - Create Jinja2 template at `app/delivery/templates/weekly_report.html` containing the top-5 resort table with score breakdown columns
    - Implement `SendGridDelivery.send_weekly_report(top5, run_id, recipient) -> bool` using the `sendgrid` Python SDK
    - Implement `SendGridDelivery.send_failure_notification(failed_components, run_id, recipient) -> bool`
    - On `sendgrid` API error: log error, return `False`
    - _Requirements: 14.2, 14.3_

  - [x] 12.3 Wire delivery trigger into `WeeklyRunOrchestrator`
    - Replace the Phase 1 delivery stub (log message) with a real `DeliveryService.dispatch(weekly_run_id, success)` call at the end of `WeeklyRunOrchestrator.run()`
    - `dispatch()` loads top-5 results from DB, sends Telegram first, sends email only if Telegram succeeded
    - On delivery failure: update `weekly_runs.error_log` with delivery failure details; do not change run `status` to `failed` (run itself succeeded)
    - _Requirements: 14.4_

  - [x]* 12.4 Write integration tests for delivery system
    - Implement `tests/test_delivery.py` with mocked `python_telegram_bot.Bot` and `sendgrid.SendGridAPIClient`
    - Test `send_weekly_report`: mock top-5 data; assert Telegram send called with correctly formatted message string; assert SendGrid send called with HTML containing all 5 resort names
    - Test failure notification: mock failed run; assert both channels receive failure message listing failed components
    - Test email-after-telegram sequencing: when Telegram fails, assert SendGrid is NOT called
    - _Requirements: 14.1, 14.2, 14.3, 14.4_

- [~] 13. FastAPI endpoints for chat session and plans
  - [~] 13.1 Add Chat_Agent and plan endpoints to FastAPI app
    - `POST /api/chat/session` → calls `ChatSession.create_session()`, returns `{"session_id": str}`
    - `WS /api/chat/session/{session_id}/messages` → WebSocket handler streaming tokens from `ChatSession.send_message()`; closes gracefully on `end_session`
    - `POST /api/chat/session/{session_id}/end` → calls `ChatSession.end_session()`, returns 200
    - `GET /api/plans` → returns all `TripPlan` records for the current user as JSON list
    - `GET /api/plans/{plan_id}` → returns single `TripPlan` record
    - All endpoints require authentication header (use existing auth middleware from Phase 1)
    - _Requirements: 12.3, 12.4, 12.5_
    - Endpoints use a configured static bearer token and single default user because no Phase 1 auth middleware/user model exists; plan reads are not user-scoped.

  - [x]* 13.2 Write integration tests for chat endpoints
    - Test `POST /api/chat/session` creates a session ID and returns 200
    - Test `POST /api/chat/session/{id}/end` triggers memory extraction (mock `MemorySystem.extract_and_write`)
    - Test `GET /api/plans` returns correct records for authenticated user
    - _Requirements: 12.3, 12.5_

- [~] 14. Final checkpoint — end-to-end integration
  - Ensure all Phase 2 tests pass across qualitative, webcam, chat, memory, and delivery modules.
  - Verify `alembic upgrade head` applies both migrations cleanly on a fresh database.
  - Verify `docker compose up` starts the extended service and the health endpoint responds.
  - Ask the user if questions arise.
  - Current automated suite: 147 tests pass. PostgreSQL migration, pgvector indexes/query, memory persistence/retrieval, and weekly-run persistence were verified manually; automated live-PostgreSQL coverage and a Docker Compose health check remain.

---

## Notes

- `search_hotels_readonly` returns mock data in Phase 2 — real hotel API integration is deferred to Phase 7 (Booking Engine). The tool stub must still conform to the expected interface so the Chat_Agent can call it without errors.
- pgvector property tests (tasks 9.2, 9.4) require a real PostgreSQL instance with pgvector extension. These tests must run against the Docker Compose test DB, not SQLite. The `tests/conftest.py` should be extended with a `pg_with_vector` async fixture scoped to the test session.
- The `create_plan_artifact` tool writes to `trip_plans` with `status='draft'`. Phase 7 (Booking Engine) adds the `status='booked'` transition — Phase 2 scope ends at draft plan creation.
- All PBT tests use `@settings(max_examples=200, deadline=None)` consistent with Phase 1 conventions. pgvector property tests may use `@settings(max_examples=100, deadline=None)` due to DB round-trip cost.
- The Amadeus `search_flights_readonly` tool requires valid Amadeus test credentials in `.env`. In CI, mock the Amadeus SDK using `AsyncMock`.
- `current_season()` utility function belongs in `app/utils.py` (create if not already present) and is shared between the Memory System and Qualitative Signal Scorer.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1", "2.2"] },
    { "id": 2, "tasks": ["2.3", "3.1"] },
    { "id": 3, "tasks": ["3.2", "4.1", "4.2"] },
    { "id": 4, "tasks": ["4.3", "5.1"] },
    { "id": 5, "tasks": ["5.2", "5.3"] },
    { "id": 6, "tasks": ["5.4", "7.1"] },
    { "id": 7, "tasks": ["7.2", "9.1"] },
    { "id": 8, "tasks": ["7.3", "9.2", "10.1"] },
    { "id": 9, "tasks": ["8.1", "9.3", "10.2"] },
    { "id": 10, "tasks": ["8.2", "9.4", "10.3"] },
    { "id": 11, "tasks": ["10.4", "10.5", "12.1", "12.2"] },
    { "id": 12, "tasks": ["12.3"] },
    { "id": 13, "tasks": ["12.4", "13.1"] },
    { "id": 14, "tasks": ["13.2"] }
  ]
}
```
