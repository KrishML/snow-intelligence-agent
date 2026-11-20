# Snow Intelligence Agent - Detailed Implementation Plan

**Status:** Approved for phased implementation
**Baseline:** September 2026 product specification
**Plan date:** 20 November 2026
**Repository:** `snow-agent`

## 1. Purpose

This document turns the product specification into an executable, dependency-ordered implementation plan. Work must proceed phase by phase. A phase is not complete until its code, tests, migrations, configuration, and acceptance checks are complete.

The implementation must preserve these non-negotiable rules:

- Snow mode is known before weather, scoring, travel, or UI logic executes.
- A destination must pass the mode-specific snow gate before travel search runs.
- The chat agent remains booking-free and cannot access booking credentials or routes.
- A booking can begin only after itemised cost display and timestamped explicit consent.
- Live search calls and partial failures are visible; stale or unavailable data is never silent.
- Weekly runs store authoritative results in PostgreSQL and chat reads the cache by default.
- London remains the default residence and origin configuration for the first implementation unless preferences explicitly change it.

## 2. Current Implementation Baseline

### 2.1 Implemented

- 70 seeded resorts: 40 sports and 30 tourism.
- Open-Meteo current snow, forecast, retry, and historical reliability clients.
- Mode-specific gate and 0-100 snow scoring engine.
- APScheduler Friday 17:00 UTC job.
- Weekly run orchestration and database result persistence.
- Reddit and RSS qualitative signals.
- GPT-4o-mini summarisation and text embeddings.
- Playwright and GPT-4o vision webcam classification.
- Telegram and SendGrid report delivery.
- LangGraph chat with a fixed seven-tool read-only registry.
- Amadeus read-only flight search.
- PII-sanitised semantic memory retrieval and session-end extraction.
- PostgreSQL, pgvector, and draft trip-plan schema.
- FastAPI chat-session, WebSocket, and plan routes.
- 147 passing automated tests at baseline.

### 2.2 Not Implemented

- Persisted residence city and `user_prefs` API.
- Origin-airport configuration for cities other than London.
- Redis-backed chat sessions and rate limits.
- Dashboard backend routes.
- React dashboard and chat UI.
- Working hotel search.
- Plan-card email and Telegram delivery.
- Official resort-report collection.
- S3 webcam storage.
- Separate booking service.
- Duffel, Stripe, hotel booking, consent log, and bookings state machine.
- Telegram `/book` and `/confirm` commands.
- Production deployment and secrets isolation.

### 2.3 Status after implementation

| Phase | State | Open items |
|---|---|---|
| 1 Preferences / residence city | Done | `residence_country` is derived from the city registry, not patched directly. Registry has London plus five UK cities. |
| 2 Redis sessions | Done | Message history and counters live in Redis (24h TTL); no Redis-native LangGraph checkpointer. |
| 3 Dashboard API | Done | Single bearer token: the authenticated user is the admin (`ADMIN_USER_IDS`). |
| 4 React dashboard | Done | Stripe/booking UI not built (see Phase 7). |
| 5 Plans, hotel search, delivery | Done | Hotel search uses Amadeus Hotel APIs (read-only); not exercised against the live API. |
| 6 Evidence hardening | Code done | Needs operator data: webcam URLs (`data/webcams.json`, 0 of 70 curated), official report sources (`data/report_sources.json`), labelled images for the 85% vision check. |
| 7 Booking service | Backend done | No hotel booking provider; Duffel cannot book Amadeus-sourced flights; no Stripe Payment Element UI; Duffel and Stripe calls verified against mocks only. `bookable` is false until these exist. |
| 8 Telegram booking | Code done, disabled | Off by default; unlocks after 4 successful web bookings and needs a saved payment method and traveller profile. |
| 9 Production readiness | Repo-level done | Logging, `/ready`, migrate-on-start, health checks, run-duration warnings. AWS (ECS, RDS, ElastiCache, S3, Secrets Manager) and TLS proxy are not provisioned here. |

## 3. Implementation Principles

### 3.1 Sequencing

Phases must be implemented in this order:

1. Traveller preferences and residence city
2. Redis and durable session state
3. Dashboard backend
4. React dashboard
5. Chat-plan completion and hotel search
6. Webcam and qualitative evidence hardening
7. Booking service isolation
8. Telegram booking
9. Production readiness

Phases 4 and 6 may overlap after their prerequisites are complete. Booking work must not begin until the draft-plan and isolated-network phases are complete.

### 3.2 Delivery Unit

Each implementation slice must include:

- Database migration, if data shape changes.
- Pydantic request and response schemas.
- Focused unit tests and integration tests.
- Configuration updates for local and Docker environments.
- API or UI documentation when externally visible.
- Failure behaviour and user-visible status.
- A clean focused test run before moving to the next slice.

### 3.3 Test Gates

No phase closes unless it passes:

- All existing tests.
- New tests covering the phase acceptance criteria.
- Relevant migration upgrade and downgrade checks.
- At least one manual or browser smoke test for user-facing features.
- Security checks for authentication and credential boundaries.

## 4. Phase 1 - Traveller Preferences and Residence City

**Goal:** Replace the London-only assumption with persisted, validated traveller preferences while retaining London as the default.

### 4.1 Data Model

Add `user_prefs` with at least:

- `id` UUID primary key
- `user_id` UUID unique
- `residence_city` string
- `residence_country` string
- `preferred_airports` JSON or PostgreSQL text array
- `excluded_airlines` text array
- `mode` enum: `sports`, `tourism`
- `budget_total_gbp` numeric, nullable
- `email` string, nullable
- `telegram_chat_id` string, nullable
- `created_at` timestamptz
- `updated_at` timestamptz

Add database constraints for valid modes and non-negative budgets.

### 4.2 Residence and Airport Configuration

Create a static, typed registry containing:

- City name and country
- Valid IATA airport codes
- Default preferred airport
- Optional nearby alternative airports

Initial registry:

- London, United Kingdom: `LHR`, `LGW`, `STN`, `LTN`, `LCY`; default `LHR`

The registry must support adding more cities without schema changes.

### 4.3 API Work

Implement:

- `GET /api/user/prefs`
- `PATCH /api/user/prefs`

The PATCH body must support:

- `residence_city`
- `residence_country`
- `preferred_airports`
- `excluded_airlines`
- `mode`
- `budget_total_gbp`
- `email`
- `telegram_chat_id`

Rules:

- All preference endpoints require authentication.
- A user can read or modify only their own preferences.
- Preferred airports must belong to the configured airports for the residence city.
- Residence-city updates atomically update defaults only if the preferred airport list is empty or no longer valid.
- A missing preference record receives London defaults.
- Invalid airport codes return `422` with an explanatory field error.

### 4.4 Application Integration

- Remove the hard-coded London airport set from flight tools.
- Resolve origin airports through preferences with London fallback.
- Pass the persisted mode into chat, weekly runs, dashboard queries, and resort filtering.
- Add current mode and origin airport to relevant chat tool responses.
- Use configured residence city instead of a fixed origin only in travel-search code.

### 4.5 Tests

- New-user preferences default to London.
- London preferences resolve all five supported airports.
- Switching to a newly configured city requires a valid airport.
- Invalid residence-airport combinations are rejected.
- One user cannot read or update another user's preferences.
- Chat and Amadeus flight search receive the selected origin airport.
- Mode preference changes route to the correct resort list.
- Migration creates the table without altering existing resort or score data.

### 4.6 Acceptance Criteria

- London remains the default with no configuration changes.
- The user can change their residence city and origin airport.
- No flight-search code accepts an origin airport outside the validated residence registry.
- Persisted mode is used consistently instead of only `settings.default_mode`.

## 5. Phase 2 - Redis and Durable Session State

**Goal:** Replace process-local chat state and add the Redis cache required by the architecture.

### 5.1 Infrastructure

Add Redis to Docker Compose with:

- Health check.
- Internal application connection URL.
- Development persistence as appropriate.
- Environment-based TLS support for production.

### 5.2 Session State

Replace `_sessions` process memory with Redis-backed session metadata:

- Session ID
- Owner user ID
- Active/closed status
- Memory-loaded state
- Live snow-call count
- Plan-created state
- Created and updated timestamps

Store LangGraph thread/checkpoint state through the existing Redis-compatible mechanism or a project-approved Redis checkpointer implementation.

### 5.3 TTL and Key Rules

Use bounded Redis key namespaces:

- `snow-agent:session:{session_id}:metadata`
- `snow-agent:session:{session_id}:checkpoints`
- `snow-agent:session:{session_id}:live-snow`
- `snow-agent:chat-rate:{session_id}`

Apply 24-hour TTL to session and message-state keys. Keep PostgreSQL trip plans as durable plan state.

### 5.4 Fallback and Failure Behaviour

- Fail clearly if Redis is required and unavailable.
- Do not silently downgrade a session to in-process-only state.
- Return a service unavailable or retry response to the WebSocket client.
- Log key expiry and Redis connectivity failures.

### 5.5 Tests

- Session state survives application process restart.
- Ownership checks remain enforced after session recreation.
- Live-call count remains capped across graph rebuilds and process restarts.
- Redis keys receive and refresh the configured TTL.
- End-session writes extracted memory once and removes active session state.
- Redis outage produces no cross-user session access.

### 5.6 Acceptance Criteria

- Chat sessions are not lost because the API worker restarted.
- Live call limits cannot reset by reconnecting or restarting a worker.
- Raw chat messages are not persisted beyond Redis's session TTL.
- Redis is represented in Docker Compose and configured by environment.

## 6. Phase 3 - Dashboard Backend APIs

**Goal:** Build authenticated, cache-only dashboard and preference endpoints.

### 6.1 Routes

Implement:

- `GET /api/dashboard/weekly`
- `GET /api/dashboard/resorts`
- `POST /api/dashboard/refresh`

### 6.2 Weekly Dashboard Response

The weekly response must include:

- Latest completed run ID
- Run date and mode
- User residence and origin airports
- Top 10 ranked passing resorts
- Score components
- Depth, temperature, forecast status, and historical reliability
- Qualitative summary
- Latest webcam classification and image path or URL
- Partial-failure state
- Last-updated timestamp

### 6.3 Resorts Response

The resorts response must include:

- Resort mode tag
- Country and region
- Base and peak altitude
- Piste and lift count
- Nearest airports
- Active status
- Webcam availability

### 6.4 Refresh Behaviour

- Require admin authentication.
- Start a new weekly run for the authenticated user's persisted mode.
- Return run ID and estimated completion time.
- Prevent duplicate refreshes for an already-running run unless policy explicitly permits it.
- Never serve live travel or snow providers through dashboard reads.

### 6.5 Tests

- Dashboard reads the latest completed run only.
- No external weather, OpenAI, webcam, or flight call occurs on dashboard reads.
- Manual refresh requires admin authorization.
- Refresh can start only the configured mode.
- Partial-failure data is included in the response.
- Resort filtering honors sports, tourism, and both mode values.

### 6.6 Acceptance Criteria

- Cached dashboard response is under two seconds in a local representative environment.
- Results are mode-specific and tied to the latest completed run.
- Refresh and delivery cannot silently suppress partial errors.

## 7. Phase 4 - React Dashboard

**Goal:** Build the browser interface for preferences, rankings, evidence, and chat.

### 7.1 Application Setup

Create a React application with:

- React 18 or the repository-approved current equivalent.
- React Query for server state.
- Zustand for local UI state.
- Authentication state integration.
- Responsive routing.

Add environment configuration for API and WebSocket URLs.

### 7.2 Dashboard Views

Build:

- Top navigation and operational mode control.
- Residence city and airport selector.
- Budget input and excluded-airline controls.
- Top 10 snow-score cards.
- Score component breakdown.
- Webcam thumbnail or offline state.
- Last-updated badge.
- Partial-failure warning banner.
- Latest-plan access.

### 7.3 Chat Panel

Build:

- Authenticated WebSocket connection.
- Streaming assistant token rendering.
- Tool-call status indicators.
- Clear live-versus-cached result labels.
- Draft-plan card rendering.
- Read-only state with no booking controls until Phase 7.

### 7.4 Plan Card

The plan card must display:

- Resort and dates
- Outbound and return airports
- Flight offer summary and total
- Hotel summary and total, once available
- Itemised estimated GBP total
- Draft status
- Timestamp
- Explicit statement that no booking or payment occurred

### 7.5 UI States

Implement loading, empty, stale, partial-failure, offline, authentication, and API-error states. Do not render a provider price unless it is from a real provider response.

### 7.6 Tests

- Playwright smoke test for login/setup, mode switching, dashboard load, and chat.
- Residence city and airport controls update preferences.
- Mobile and desktop layouts do not overlap or hide primary controls.
- Plan cards render only when the server returns a valid draft plan.
- API failures show actionable visible states.

### 7.7 Acceptance Criteria

- The actual application first screen is operational data, not a marketing landing page.
- London remains selected by default.
- Changing residence city updates available origin airports.
- Dashboard can load without starting live provider calls.
- No booking button exists before the isolated booking workflow is complete.

## 8. Phase 5 - Chat-Plan Completion and Hotel Search

**Goal:** Finish accurate draft-plan creation and plan delivery.

### 8.1 Hotel Search

Integrate one real hotel provider or retain an explicit unavailable state until it is integrated.

A hotel response must be normalised to:

- Property name
- Address
- Property type
- Check-in date
- Checkout date
- Nightly price
- Total price
- Currency
- Provider reference
- Search timestamp

No hotel price may be inferred or fabricated.

### 8.2 Plan Tool Changes

Update `create_plan_artifact` to:

- Accept an actual normalised hotel object.
- Reject hotel objects that were not returned in this session.
- Validate dates and destination.
- Recheck that the resort passed the latest completed snow gate.
- Store currency and provider references.
- Prevent duplicate plan creation in one session.

### 8.3 Plan Delivery

On successful draft-plan creation:

- Dispatch through configured email and Telegram channels.
- Include a link or identifier for the React plan view.
- Label all content explicitly as a draft.
- Record delivery failures in the plan or run error state.

### 8.4 Plan API

Complete:

- `DELETE /api/plans/{plan_id}`
- Ownership checks for plan read and cancellation.
- `draft`, `cancelled`, and later `booked` status handling.
- Rejection of cancellation for an already-booked plan.

### 8.5 Tests

- Hotels returned by search can be selected in a plan.
- Fabricated hotel objects are rejected.
- Plans require a gate-passing resort.
- Duplicate plan creation is rejected.
- Plan creation dispatches to configured channels.
- Delivery failure is visible and retried or logged according to policy.
- Another user cannot read or cancel a plan.

### 8.6 Acceptance Criteria

- Draft plans contain a real flight and, once available, a real hotel.
- Estimated totals are itemised and currency-safe.
- Plan creation never implies a booking.
- Plan delivery and plan-card rendering occur within two seconds of tool completion when providers succeed.

## 9. Phase 6 - Evidence and Webcam Hardening

**Goal:** Improve condition evidence quality and source observability.

### 9.1 Source Work

- Add official resort report collection where stable public sources exist.
- Add tourism-specific RSS and travel sources.
- Curate a webcam URL for every resort.
- Add source metadata and retrieval timestamps.
- Capture multiple images only if needed for fallback or validation.

### 9.2 Storage

Move webcam files from local-only storage to object storage:

- S3 in production.
- Configurable local or S3 backend in development.
- Store original capture path or object key.
- Apply the 90-day snapshot retention policy.

### 9.3 Vision Quality

Create a labelled webcam evaluation set for `snow_good`, `snow_poor`, `no_snow`, and `camera_offline`.

Tune prompt and classification rules against at least 85% agreement with manual review.

### 9.4 Tests

- Missing webcam URL returns `camera_offline` quickly without browser start.
- Browser launch failure becomes an explicit offline snapshot.
- Vision invalid JSON or invalid classification is treated as offline or review-required.
- Failed screenshots are stored with an error state when useful.
- S3 failure does not prevent database result persistence.

### 9.5 Acceptance Criteria

- At least 80% of top-10 resorts have an attempted webcam capture.
- Unusable cameras return offline within five seconds.
- Local and production storage expose the same snapshot schema.
- Webcam and qualitative failures remain visible in weekly run logs.

## 10. Phase 7 - Booking Service Isolation

**Goal:** Implement the first genuinely safe end-to-end transaction workflow behind explicit consent.

### 10.1 Booking Service Structure

Create a separate FastAPI application and Docker container:

- `app/booking/main.py`
- Booking-only configuration module.
- Booking authentication middleware.
- Dedicated `/booking/*` routes.
- Its own network namespace or container boundary.

The booking container must not contain OpenAI or Amadeus credentials. The chat container must not contain Duffel, Stripe, or hotel-booking credentials.

### 10.2 Database Schema

Add:

- `consent_log`
- `bookings`
- Booking state history or status timestamps.

Booking states should include at least:

- `pending_summary`
- `consented`
- `flight_held`
- `hotel_held`
- `payment_authorised`
- `booked`
- `failed`
- `cancelled`

Consent records must be append-only and retain channel, plan ID, user ID, timestamp, itemised breakdown, and price-lock version.

### 10.3 API

Implement:

- `GET /booking/plans/{plan_id}/summary`
- `POST /booking/plans/{plan_id}/confirm`
- `GET /booking/{booking_id}/status`
- `GET /booking/history`

Rules:

- Summary validates the plan and ten-minute price lock.
- Confirmation body must include `user_confirmed: true`.
- The web UI dedicated button is the only supported consent initiation.
- Consent is committed before the first external flight or hotel API call.
- Raw card data is never received or stored by the application.

### 10.4 Booking Sequence

1. Revalidate the plan and price lock.
2. Display itemised flight, hotel, and total cost.
3. Receive explicit confirmation from the dedicated UI button.
4. Persist consent.
5. Hold flight.
6. Hold hotel.
7. Authorise Stripe payment.
8. Confirm flight and hotel.
9. Create the booking record and final references.
10. Dispatch confirmation to email and Telegram.

Any failure must halt the sequence and execute compensating actions.

### 10.5 Failure Rules

- Flight hold failure: no hotel call and no Stripe call.
- Hotel hold failure: release flight hold; no Stripe call.
- Payment failure: release hotel and flight holds.
- Confirmation failure after payment: roll back or begin a supported manual-recovery workflow; never silently report success.
- No booking attempt may retry without fresh confirmation.

### 10.6 Tests

- Consent exists before every mocked external provider call.
- Flight hold failure aborts before hotel or Stripe calls.
- Hotel hold failure releases the flight hold.
- Payment failure releases both holds.
- Expired price locks return `409` with no consent and no provider calls.
- Chat cannot call a `/booking/*` route.
- Chat environment checks reject Duffel, Stripe, or hotel credentials.
- Booking container environment rejects OpenAI or Amadeus credentials.
- Partial provider failures return a user-visible failed state.

### 10.7 Acceptance Criteria

- No partial silent completions.
- Every paid booking has consent and provider references.
- One booking attempt can complete in under 60 seconds under normal provider latency.
- End-to-end test reaches a successful booking using sandbox providers only.
- Booking state and compensating actions are testable through mocks.

## 11. Phase 8 - Telegram Booking

**Goal:** Add Telegram booking only after web booking is stable and validated in real sandbox/small-scale use.

### 11.1 Commands

Implement:

- `/book <plan_id>`: read-only itemised cost summary.
- `/confirm <plan_id>`: explicit second-stage confirmation.

Keep `/mode`, `/budget`, `/report`, `/plan`, `/start`, and `/help` in scope outside the optional flow.

### 11.2 Consent Rules

- `/book` creates no hold and no payment.
- `/confirm` requires a prior `/book` in the same Telegram session.
- The confirmation state expires with the price lock.
- Consent channel is `telegram`.
- The existing booking service, not Telegram bot code, executes external transactions.

### 11.3 Tests

- `/book` never calls a provider or writes consent.
- `/confirm` without `/book` is rejected.
- `/confirm` uses the same booking sequence and rollback rules.
- Telegram-origin consent is auditable in `consent_log`.
- Telegram booking has no separate payment implementation.

### 11.4 Acceptance Criteria

- The Telegram flow is thin orchestration over the existing booking service.
- All web and Telegram booking safety rules are identical.

## 12. Phase 9 - Production Readiness

**Goal:** Move the complete system to secure, observable, repeatable deployment.

### 12.1 Infrastructure

- PostgreSQL 16 managed service with pgvector.
- Redis managed cache.
- ECS Fargate or equivalent for main and booking containers.
- S3 webcam storage.
- Secrets Manager or equivalent.
- HTTPS reverse proxy and TLS.
- Separate booking container and network policies.
- Automated migrations on controlled deployment.

### 12.2 Observability

Add:

- Structured application logs.
- Weekly run metrics and duration.
- Provider latency and failure metrics.
- Booking state-transition metrics.
- Delivery success metrics.
- Alerting for failed Friday runs and partial reports.
- Database backup and restore validation.

### 12.3 Reliability

- Run migrations before application rollout.
- Roll back application deployment on migration failure.
- Verify Redis and PostgreSQL health in startup probes.
- Run Playwright browser installation in the application image or worker.
- Configure retry and timeout budgets consistently.

### 12.4 Security

- Store all secrets outside source control.
- Use separate credentials for chat and booking containers.
- Restrict booking routes to authorized network and authenticated users.
- Apply HTTPS only in production.
- Encrypt or tokenise Stripe payment methods.
- Make consent log append-only.
- Limit retention according to the specification.

### 12.5 Production Acceptance

- Friday report is delivered by 18:15 UTC.
- Failure notifications arrive within two minutes.
- Dashboard loads within two seconds from cache.
- Chat first token arrives within three seconds under normal provider latency.
- Weekly run completes within 30 minutes.
- pgvector queries complete within 500 milliseconds.
- Booking sandbox flow completes in under 60 seconds.
- All credentials and container boundaries pass a security review.

## 13. Cross-Phase Quality Requirements

### 13.1 Testing

Maintain:

- pytest for unit and integration tests.
- pytest-asyncio for async behaviour.
- pytest-httpx for provider HTTP mocks.
- Playwright for browser and end-to-end tests.
- Alembic upgrade/downgrade test coverage.
- Docker Compose smoke tests.
- Provider sandbox tests only for external bookings.

### 13.2 Coverage Priorities

Risk-weighted test coverage must include:

- Snow gate and mode routing.
- Weekly-run partial failure handling.
- Chat tool registry and tool count.
- Residence and origin-airport validation.
- Session ownership and Redis expiry.
- Score and plan persistence.
- Consent ordering.
- Provider rollback.
- Credential isolation.

### 13.3 Data and Cost Controls

- Embeddings remain deduplicated before preference writes.
- OpenAI chat uses GPT-4o only where needed.
- GPT-4o-mini handles high-volume summarisation.
- Live Amadeus calls remain user-requested.
- Redis keys remain bounded and expire.
- Snow, run, webcam, booking, consent, and vector retention follow specification.

## 14. Definition of Done for the Repository

The project is done only when:

- The traveller can configure residence city and origin airports, defaulting to London.
- A persistent mode and budget affect backend and UI behaviour.
- Dashboard and chat consume cached weekly results without unintended live calls.
- Qualitative and webcam evidence are visible and failure-aware.
- Chat creates accurate draft plans and cannot initiate a transaction.
- Hotel and flight totals come only from provider data.
- Booking executes in a separate container with credential isolation.
- Explicit consent is written before every external booking call.
- Partial failures roll back or report a recoverable state.
- Telegram optional booking reuses the same isolated booking logic.
- Docker, migrations, tests, monitoring, and secrets are production-ready.

## 15. Recommended Implementation Cadence

### Sprint 1

- Phase 1 preferences and residence registry.
- London default migration and flight-tool integration.
- Preference API tests.

### Sprint 2

- Phase 2 Redis sessions.
- Redis Docker health and TTL tests.
- Session restoration tests.

### Sprint 3

- Phase 3 dashboard APIs.
- Cache-only integration tests.

### Sprint 4

- Phase 4 React dashboard and preference controls.
- Browser smoke tests.

### Sprint 5

- Phase 5 real hotel provider and plan delivery.
- Plan ownership and provider data tests.

### Sprint 6

- Phase 6 evidence hardening and object storage.
- Vision-labelled validation set.

### Sprint 7

- Phase 7 booking service.
- Sandbox end-to-end and rollback tests.

### Sprint 8

- Phase 8 Telegram booking and Phase 9 production rollout.

Implementation must pause at each phase gate until tests and acceptance criteria pass. The first code slice should be Phase 1, starting with the preference migration and London-compatible residence registry.
