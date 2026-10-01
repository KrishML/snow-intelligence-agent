# Snow Intelligence & Winter Travel Booking Agent
## Product Specification — Spec-Driven Development Format

**Version:** 1.0  
**Author:** Krish  
**Date:** September 2026  
**Status:** Draft — Ready for Implementation

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Governing Principles](#2-governing-principles)
3. [User Personas & Modes](#3-user-personas--modes)
4. [System Architecture](#4-system-architecture)
5. [Data Models](#5-data-models)
6. [Feature Specifications](#6-feature-specifications)
7. [Agent Tool Specifications](#7-agent-tool-specifications)
8. [API Specifications](#8-api-specifications)
9. [Non-Functional Requirements](#9-non-functional-requirements)
10. [Phase Implementation Plan](#10-phase-implementation-plan)
11. [Tech Stack Reference](#11-tech-stack-reference)
12. [Testing Requirements](#12-testing-requirements)
13. [Open Questions & Decisions Log](#13-open-questions--decisions-log)

---

## 1. Project Overview

### 1.1 Problem Statement

European snow destinations have become increasingly unreliable due to climate variability. A user based in London wants to take winter weekend trips (snow tourism and/or winter sports) between November and March but has been repeatedly disappointed by travelling to destinations with inadequate or absent snow cover. Manual research across resort websites, weather services, Reddit, and booking platforms is time-consuming and often incomplete.

### 1.2 Solution Summary

An automated weekly agent that:
- Aggregates and scores snow conditions across 70+ European destinations
- Surfaces the top 5 destinations every Friday with verified, multi-source condition intelligence
- Allows conversational exploration and refinement via a web chat interface
- Produces a concrete trip plan that can be dispatched to email and Telegram
- Enables end-to-end flight and hotel booking with payment, behind a mandatory human approval gate

### 1.3 Success Criteria

- User receives a Friday brief every week from November through March with ≥5 destinations that meet minimum viable snow thresholds
- Snow scores correlate with actual conditions (validated manually for 4 weeks before full deployment)
- Chat sessions produce a concrete plan artifact within ≤10 conversational turns on average
- Booking flow completes end-to-end without partial failures in ≥95% of attempts
- User does not need to repeat preferences across chat sessions (semantic memory active)

### 1.4 Scope

**In scope:**
- European destinations only
- Weekend trips (Friday–Sunday or Saturday–Monday) departing from any London airport (LHR, LGW, STN, LTN, LCY)
- November through March season
- Two modes: Snow Tourism and Winter Sports
- Web interface, email delivery, Telegram bot
- Flight and hotel booking with payment

**Out of scope:**
- Non-European destinations
- Long-haul or multi-week trips
- Car hire, ski pass, or equipment rental booking (Phase 1–8)
- Group bookings (single traveller only in v1)

---

## 2. Governing Principles

These are non-negotiable architectural constraints. Every implementation decision must be checked against them.

### P1 — Booking Firewall

The chat agent and the booking service are completely isolated execution contexts. The chat agent:
- Has no Duffel API key, hotel booking credentials, or Stripe key in its environment
- Cannot call any endpoint under `/booking/*`
- Can only produce a read-only plan artifact as output
- Cannot initiate, modify, query, or cancel any booking

The only crossing point between the two contexts is a plan artifact (structured JSON) passed by reference (plan ID). No callbacks, no shared state, no open connections between chat and booking services.

### P2 — Explicit Consent Gate

No financial transaction occurs without:
1. A full itemised cost breakdown displayed to the user (flight, hotel, total)
2. A dedicated UI button press — not a chat message, not a Telegram text
3. The consent timestamped and written to `consent_log` in the database **before** any booking API call is made
4. Failure at any step halts the entire booking — no partial silent completions

### P3 — Mode-First Architecture

Every data fetch, threshold evaluation, score calculation, resort list selection, and UI display is downstream of the mode setting (Snow Tourism or Winter Sports). Mode is determined first in every execution path. Nothing executes before mode is known.

### P4 — Score Before Search

Travel search (flights, hotels) only executes for destinations that have passed the Snow Score minimum viable gate. A destination below the threshold gate is excluded entirely — no travel options are fetched or displayed for it, regardless of how cheap the flights are.

### P5 — Fail Loud, Never Silent

Any data fetch failure, API error, scraping block, or partial failure surfaces immediately to the user via Telegram and email. The system never:
- Silently substitutes stale data without flagging it
- Degrades to partial output without notification
- Retries a booking without fresh explicit user confirmation

### P6 — Stateless Weekly Cache

The weekly automated run writes all results to the database. The chat agent reads from this cache by default. Live API calls are only triggered when the user explicitly requests something not covered by the cache (e.g. "what's the snow like in Bansko right now?"). This keeps chat responsive and API costs controlled.

---

## 3. User Personas & Modes

### 3.1 Primary User

- Based in London (all airports accessible)
- Interested in winter weekend trips, November–March
- Motivated by reliable snow — has been disappointed by poor conditions before
- Comfortable with technology; wants automation but maintains control over spending

### 3.2 Operational Modes

The system operates in one of two modes at all times. Mode is persistent (stored in `user_prefs`) but toggleable at any time from the dashboard or chat.

#### Mode A: Snow Tourism

The user wants to experience snowy landscapes, villages, atmosphere. Skiing ability is not required.

| Parameter | Value |
|---|---|
| Target destination types | Villages, towns, national parks, scenic areas |
| Altitude range | 500m–1400m (charm over snowpack depth) |
| Minimum snow depth gate | 15cm at destination |
| Forecast exclusion | Rain or temps > 3°C in 5-day forecast |
| Resort list | Tourism destination list (~30 locations) |
| Qualitative sources | Travel blogs, tourism boards, general travel forums |
| Accommodation preference | Town centre, boutique, walkable |

#### Mode B: Winter Sports

The user wants to ski or snowboard. Slope access, lift status, and snow depth are primary concerns.

| Parameter | Value |
|---|---|
| Target destination types | Ski resorts with lift infrastructure |
| Altitude range | 1200m+ preferred (snow retention critical) |
| Minimum snow depth gate | 40cm settled depth at resort altitude |
| Additional gate | At least 1 blue or green run open |
| Forecast exclusion | Rain or temps > 3°C in 5-day forecast |
| Resort list | Sports resort list (~40 locations) |
| Qualitative sources | r/skiing, r/snowboarding, r/Alps, resort-specific subs |
| Accommodation preference | Ski-in/ski-out or within 500m of main lift |

---

## 4. System Architecture

### 4.1 High-Level Component Map

```
┌─────────────────────────────────────────────────────────────────┐
│                      WEB INTERFACE (React)                       │
│                                                                  │
│   Dashboard View                  Chat Interface                 │
│   ─────────────                  ───────────────                 │
│   Snow score cards                Conversational agent           │
│   Mode toggle                     Memory-augmented               │
│   Budget input                    Tool-enabled                   │
│   Webcam thumbnails               Ends in: Plan Artifact         │
│   Last-updated badge                                             │
└──────────────┬──────────────────────────┬───────────────────────┘
               │ REST / WebSocket         │ REST
               ▼                          ▼
┌──────────────────────┐    ┌─────────────────────────────────────┐
│   MAIN FASTAPI       │    │        BOOKING FASTAPI SERVICE       │
│   SERVICE            │    │        (/booking/* routes only)      │
│                      │    │                                      │
│   Weekly run         │    │   Own auth middleware                │
│   Chat agent         │    │   Own env (Duffel, Stripe, Hotel)    │
│   Dashboard API      │    │   Reads plan artifact from DB        │
│   Delivery           │    │   Writes bookings + consent_log      │
└──────────┬───────────┘    └─────────────────┬───────────────────┘
           │                                  │
           ▼                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                     DATA LAYER                                    │
│                                                                  │
│   PostgreSQL + pgvector          Redis                           │
│   ─────────────────────          ─────                           │
│   Structured tables              Session message history         │
│   Vector store                   API rate limit counters         │
│                                  Weekly run cache                │
└──────────────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────────┐
│                  EXTERNAL INTEGRATIONS                            │
│                                                                  │
│  Open-Meteo    Reddit(praw)    Amadeus     Duffel    Stripe      │
│  SnowFcst.com  Feedparser      Booking.com           SendGrid    │
│  Playwright    OpenAI API      Telegram              AWS S3      │
└──────────────────────────────────────────────────────────────────┘
```

### 4.2 Weekly Automated Run Flow

```
APScheduler fires — Friday 17:00 UTC
        │
        ▼
Read mode from user_prefs
Select resort list (sports or tourism)
        │
        ▼
For each resort in list (parallel, rate-limited):
  ├── Open-Meteo: current depth + temperature
  ├── Open-Meteo: 5-day forecast
  └── Open-Meteo historical: 5yr weekend reliability %
        │
        ▼
Apply minimum viable gate (hard exclusion):
  Sports:  depth < 40cm → exclude
  Tourism: depth < 15cm → exclude
  Either:  rain or temp > 3°C in forecast → exclude
        │
        ▼
For passing resorts:
  ├── Reddit scrape (praw)
  ├── SnowForecast.com RSS
  ├── Resort official report scrape
  └── GPT-4o-mini: summarise → Qualitative Signal Score
        │
        ▼
Playwright: screenshot webcams for top 10
GPT-4o vision: classify each (snow_good/poor/none/offline)
        │
        ▼
Compute Snow Score (0–100) for each passing resort
Rank — write top 10 to DB (weekly_runs + snow_scores tables)
        │
        ▼
Embed qualitative summaries → write to pgvector
        │
        ▼
Trigger delivery event:
  ├── Telegram: top 5 brief
  └── Email (SendGrid): HTML weekly report
```

### 4.3 Chat Session Flow

```
User sends message
        │
        ▼
Retrieve session history from Redis
Embed message → query pgvector:
  ├── Relevant past preferences
  ├── Trip feedback
  └── Resort knowledge corpus
Inject top-k retrieved context into system prompt
        │
        ▼
LangGraph agent (GPT-4o) selects tool or responds directly
        │
  ┌─────┴─────────────────────────────────────┐
  │ Tool execution (read-only, no booking)    │
  │ filter_results | fetch_live_snow          │
  │ fetch_webcam | search_flights_readonly    │
  │ search_hotels_readonly                    │
  │ get_historical_reliability                │
  └─────┬─────────────────────────────────────┘
        │
        ▼
Response streamed to UI via WebSocket
Session history updated in Redis
        │
        ▼ (when user signals booking intent)
create_plan_artifact() called:
  ├── Structured plan written to trip_plans table
  ├── Plan card rendered in UI
  ├── Plan dispatched to email + Telegram
  └── "Confirm & Book" button enabled
        │
        ▼ (end of session)
GPT-4o-mini: extract preferences from session
Embed preference chunks → write to pgvector
Session history cleared from Redis
```

### 4.4 Booking Flow

```
User presses "Confirm & Book" — UI only, not chat
        │
        ▼
Booking service loads plan artifact by plan_id
Displays full itemised cost:
  Outbound flight: carrier, route, time, £X
  Return flight: £X
  Hotel (N nights @ £Y/night): £Z
  ─────────────────────────────────────
  Total: £[sum]
  "Prices valid for 10 minutes"
        │
        ▼
User presses "Book Now — £[total]" (cost shown on button)
Consent written to consent_log (timestamp, user_id, plan_id, total)
        │
        ▼
Execute sequentially:
  Step 1: Duffel API — hold flight
  Step 2: Hotel API — hold accommodation
  Step 3: Stripe — authorise charge against PaymentMethod token
  Step 4: Duffel API — confirm flight
  Step 5: Hotel API — confirm accommodation
  Step 6: Write refs + total to bookings table
        │
  ┌─────┴──────────────────┐
  │ Success                │ Failure (any step)
  │                        │
  │ Confirmation page      │ Halt immediately
  │ Email: full itinerary  │ Rollback any holds
  │ Telegram: refs         │ Log full context
  │                        │ Notify: TG + email
  └────────────────────────┘ No retry without
                             fresh user confirm
```

---

## 5. Data Models

### 5.1 PostgreSQL Schema

#### `resorts`
```sql
CREATE TABLE resorts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(100) NOT NULL,
    country         VARCHAR(50) NOT NULL,
    region          VARCHAR(100),
    latitude        DECIMAL(9,6) NOT NULL,
    longitude       DECIMAL(9,6) NOT NULL,
    altitude_base_m INTEGER NOT NULL,
    altitude_peak_m INTEGER,
    mode            VARCHAR(20) NOT NULL CHECK (mode IN ('sports','tourism','both')),
    nearest_airports VARCHAR(20)[] NOT NULL,  -- IATA codes e.g. {KRK, KTW}
    piste_count     INTEGER,                  -- sports mode only
    lift_count      INTEGER,                  -- sports mode only
    webcam_url      TEXT,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

#### `weekly_runs`
```sql
CREATE TABLE weekly_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_date        DATE NOT NULL,
    mode            VARCHAR(20) NOT NULL,
    status          VARCHAR(20) NOT NULL CHECK (status IN ('running','complete','failed')),
    resorts_checked INTEGER,
    resorts_passed  INTEGER,
    error_log       JSONB,
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);
```

#### `snow_scores`
```sql
CREATE TABLE snow_scores (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    weekly_run_id             UUID REFERENCES weekly_runs(id),
    resort_id                 UUID REFERENCES resorts(id),
    passed_gate               BOOLEAN NOT NULL,
    gate_failure_reason       TEXT,
    depth_cm                  INTEGER,
    temperature_c             DECIMAL(4,1),
    forecast_stable           BOOLEAN,
    forecast_detail           JSONB,           -- raw 5-day forecast
    historical_reliability_pct DECIMAL(5,2),
    score_depth               INTEGER,         -- 0–30
    score_forecast            INTEGER,         -- 0–25
    score_historical          INTEGER,         -- 0–25
    score_qualitative         INTEGER,         -- 0–20
    score_total               INTEGER,         -- 0–100
    rank                      INTEGER,
    created_at                TIMESTAMPTZ DEFAULT NOW()
);
```

#### `qualitative_signals`
```sql
CREATE TABLE qualitative_signals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    weekly_run_id   UUID REFERENCES weekly_runs(id),
    resort_id       UUID REFERENCES resorts(id),
    source          VARCHAR(50) NOT NULL,      -- 'reddit','rss','resort_official','webcam_vision'
    raw_text        TEXT,
    summary         TEXT,                      -- GPT-4o-mini extracted summary
    sentiment       VARCHAR(20),               -- 'positive','neutral','negative'
    keywords_flagged VARCHAR(50)[],            -- ['icy','closed','slush']
    runs_open       INTEGER,
    lifts_open      INTEGER,
    fetched_at      TIMESTAMPTZ DEFAULT NOW()
);
```

#### `webcam_snapshots`
```sql
CREATE TABLE webcam_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    resort_id       UUID REFERENCES resorts(id),
    weekly_run_id   UUID REFERENCES weekly_runs(id),
    image_url       TEXT NOT NULL,             -- S3 URL or local path
    classification  VARCHAR(20) CHECK (
                        classification IN (
                            'snow_good','snow_poor','no_snow','camera_offline'
                        )
                    ),
    captured_at     TIMESTAMPTZ DEFAULT NOW()
);
```

#### `trip_plans`
```sql
CREATE TABLE trip_plans (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    weekly_run_id       UUID REFERENCES weekly_runs(id),
    resort_id           UUID REFERENCES resorts(id),
    outbound_date       DATE NOT NULL,
    return_date         DATE NOT NULL,
    flight_option       JSONB NOT NULL,        -- carrier, route, times, price, Amadeus ref
    hotel_option        JSONB NOT NULL,        -- name, address, room type, price/night
    total_estimated_gbp DECIMAL(8,2) NOT NULL,
    notes               TEXT,
    chat_session_id     VARCHAR(100),
    status              VARCHAR(20) DEFAULT 'draft'
                            CHECK (status IN ('draft','confirmed','booked','cancelled')),
    dispatched_at       TIMESTAMPTZ,           -- when plan sent to email/TG
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
```

#### `bookings`
```sql
CREATE TABLE bookings (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_plan_id        UUID REFERENCES trip_plans(id),
    duffel_order_id     VARCHAR(100),          -- flight booking ref
    hotel_booking_ref   VARCHAR(100),
    stripe_payment_id   VARCHAR(100),
    total_charged_gbp   DECIMAL(8,2),
    status              VARCHAR(20) DEFAULT 'pending'
                            CHECK (status IN (
                                'pending','flight_held','hotel_held',
                                'payment_authorised','confirmed','failed',
                                'cancelled'
                            )),
    failure_reason      TEXT,
    booked_at           TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
```

#### `consent_log`
```sql
CREATE TABLE consent_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_plan_id    UUID REFERENCES trip_plans(id),
    user_id         VARCHAR(100) NOT NULL,
    channel         VARCHAR(20) NOT NULL CHECK (channel IN ('web','telegram')),
    total_shown_gbp DECIMAL(8,2) NOT NULL,
    consented_at    TIMESTAMPTZ DEFAULT NOW()
);
```

#### `user_prefs`
```sql
CREATE TABLE user_prefs (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 VARCHAR(100) UNIQUE NOT NULL,
    mode                    VARCHAR(20) DEFAULT 'sports'
                                CHECK (mode IN ('sports','tourism')),
    budget_total_gbp        DECIMAL(8,2),
    preferred_airports      VARCHAR(10)[] DEFAULT '{LHR,LGW,STN,LTN,LCY}',
    excluded_airlines       VARCHAR(10)[],
    stripe_payment_method   VARCHAR(100),      -- tokenised — never raw card data
    telegram_chat_id        VARCHAR(50),
    email                   VARCHAR(200),
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);
```

### 5.2 pgvector Schema (within PostgreSQL)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

-- User preference statements extracted from chat sessions
CREATE TABLE preference_embeddings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         VARCHAR(100) NOT NULL,
    text            TEXT NOT NULL,
    embedding       vector(1536),              -- text-embedding-3-small dimensions
    session_date    DATE,
    resorts_mentioned VARCHAR(100)[],
    sentiment       VARCHAR(20),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Post-trip feedback
CREATE TABLE trip_feedback_embeddings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         VARCHAR(100) NOT NULL,
    resort_id       UUID REFERENCES resorts(id),
    text            TEXT NOT NULL,
    embedding       vector(1536),
    visit_month     VARCHAR(7),                -- 'YYYY-MM'
    sentiment       VARCHAR(20),
    booking_id      UUID REFERENCES bookings(id),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Qualitative resort knowledge corpus
CREATE TABLE resort_knowledge_embeddings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    resort_id       UUID REFERENCES resorts(id),
    text            TEXT NOT NULL,
    embedding       vector(1536),
    source          VARCHAR(50),
    month           INTEGER,                   -- 1–12, for seasonal filtering
    year            INTEGER,
    sentiment       VARCHAR(20),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Compressed chat session summaries
CREATE TABLE session_summary_embeddings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         VARCHAR(100) NOT NULL,
    session_id      VARCHAR(100) NOT NULL,
    summary_text    TEXT NOT NULL,
    embedding       vector(1536),
    session_date    DATE,
    plan_created    BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for ANN search
CREATE INDEX ON preference_embeddings USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX ON trip_feedback_embeddings USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX ON resort_knowledge_embeddings USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX ON session_summary_embeddings USING ivfflat (embedding vector_cosine_ops);
```

### 5.3 Redis Key Schema

```
KEY PATTERN                           TTL         CONTENT
────────────────────────────────────  ──────────  ──────────────────────────────
session:{session_id}:messages         24 hours    JSON array of chat messages
session:{session_id}:context          24 hours    Retrieved pgvector context
weekly_run:cache:latest               8 days      Serialised top 10 results
api:rate:openai:{minute_bucket}       90 seconds  Request count integer
api:rate:reddit:{minute_bucket}       90 seconds  Request count integer
webcam:{resort_id}:url                7 days      Webcam URL string
```

### 5.4 Plan Artifact Schema (JSON)

The structured object produced by `create_plan_artifact()` and stored in `trip_plans.flight_option` and `trip_plans.hotel_option`:

```json
{
  "plan_id": "uuid",
  "created_at": "ISO8601",
  "resort": {
    "id": "uuid",
    "name": "Zakopane",
    "country": "Poland",
    "altitude_base_m": 850,
    "mode": "both"
  },
  "dates": {
    "outbound": "2025-01-10",
    "return": "2025-01-12",
    "nights": 2
  },
  "snow_conditions": {
    "depth_cm": 65,
    "score_total": 82,
    "forecast_summary": "Stable cold conditions, light snowfall expected Saturday",
    "historical_reliability_pct": 78.4
  },
  "flight": {
    "outbound": {
      "carrier": "Wizz Air",
      "flight_number": "W6 1234",
      "origin": "STN",
      "destination": "KRK",
      "departs": "2025-01-10T18:30:00",
      "arrives": "2025-01-10T22:15:00",
      "price_gbp": 89.00,
      "amadeus_ref": "..."
    },
    "return": {
      "carrier": "Wizz Air",
      "flight_number": "W6 1235",
      "origin": "KRK",
      "destination": "STN",
      "departs": "2025-01-12T20:00:00",
      "arrives": "2025-01-12T22:30:00",
      "price_gbp": 74.00
    }
  },
  "hotel": {
    "name": "Hotel Tatry",
    "address": "ul. Krupówki 12, Zakopane",
    "room_type": "Standard Double",
    "checkin": "2025-01-10",
    "checkout": "2025-01-12",
    "price_per_night_gbp": 55.00,
    "total_hotel_gbp": 110.00,
    "booking_ref": "..."
  },
  "costs": {
    "flights_total_gbp": 163.00,
    "hotel_total_gbp": 110.00,
    "grand_total_gbp": 273.00,
    "within_budget": true
  },
  "notes": "User requested altitude >800m, preferred Poland. Avoided Ryanair per session preference."
}
```

---

## 6. Feature Specifications

### 6.1 Snow Score Engine

**Description:** Computes a 0–100 composite score for each resort that passes the minimum viable gate.

**Inputs:** Open-Meteo current depth, 5-day forecast, 5-year historical archive, qualitative signal summary

**Score Components:**

| Component | Weight | Logic |
|---|---|---|
| Current Depth | 30 pts | Sports: <20cm=0, 20–39cm=10, 40–59cm=20, 60cm+=30. Tourism: <10cm=0, 10–19cm=10, 20–29cm=20, 30cm+=30 |
| Forecast Stability | 25 pts | Snowfall incoming +5pts bonus. Rain in forecast -15pts penalty. Temp >3°C -10pts penalty. Stable cold & dry = full 25pts |
| Historical Reliability | 25 pts | % of weekends in last 5 years that met threshold × 0.25 |
| Qualitative Signal | 20 pts | Positive sentiment = 15–20pts. Neutral/no signal = 10pts. Negative keywords (icy/slush/closed/bare) = 0–5pts |

**Minimum Viable Gate (applied before scoring):**

| Mode | Condition | Action |
|---|---|---|
| Sports | depth < 40cm at resort altitude | Exclude — do not score |
| Sports | No open blue/green run confirmed | Exclude |
| Tourism | depth < 15cm at destination | Exclude |
| Either | Rain forecast within 5 days | Exclude |
| Either | Temperature > 3°C in 5-day forecast | Exclude |

**Output:** Score 0–100 per resort, rank, gate pass/fail with reason. Written to `snow_scores` table.

---

### 6.2 Memory Retrieval System

**Description:** Injects relevant long-term context into the chat agent's system prompt at the start of each session turn.

**Retrieval query:** User's message embedded with text-embedding-3-small. Cosine similarity search against four pgvector tables with metadata filters.

**Context injected per turn:**

```
[MEMORY CONTEXT]
Past preferences: {top 3 preference_embeddings by similarity}
Trip feedback: {top 2 trip_feedback_embeddings by similarity}
Resort knowledge: {top 3 resort_knowledge_embeddings by similarity, filtered to current season}
Recent sessions: {top 1 session_summary_embeddings, last 30 days}
[END MEMORY CONTEXT]
```

**Memory write (end of session):**
- GPT-4o-mini processes full session history
- Extracts discrete preference statements (each as a separate embedding)
- Writes session summary as a single embedding
- Post-trip feedback written if user provides it during session

**Memory must not:**
- Repeat the same preference if already stored (deduplication check before write)
- Store raw conversation transcripts in pgvector
- Store any payment or PII data

---

### 6.3 Weekly Delivery

**Telegram message format (every Friday ~18:00 local):**

```
❄️ Snow Report — [Date]
Mode: [Sports/Tourism]

🥇 [Resort Name], [Country]
   Depth: Xcm | Score: YY/100 | Reliability: ZZ%
   "[One line qualitative summary]"

🥈 [Resort Name], [Country]
   ...

🥉 [Resort Name], [Country]
   ...

4️⃣ ...
5️⃣ ...

[N] destinations checked. [M] met minimum threshold.
Open the app to explore and plan → [URL]
```

**Email format:** HTML email with same content, expanded to include webcam thumbnails (Phase 6+), Snow Score breakdown table, and direct "Plan a trip" CTA button linking to the web app.

**Failure notification format:**

```
⚠️ Weekly Snow Run — Partial Failure
[Date/time]

Failed components:
- Reddit scrape: rate limited (qualitative scores set to neutral)
- Resort X webcam: timeout (no image this week)

Scores computed with available data. Stale data flagged.
[N] of [M] components completed successfully.
```

---

### 6.4 Chat Interface Behaviour

**Session initialisation:**
- Load user prefs (mode, budget, excluded airlines) from DB
- Retrieve relevant memory from pgvector
- System prompt constructed with: mode context, budget, memory context, tool list

**Supported conversation intents:**

| User Intent | Agent Behaviour |
|---|---|
| Filter by attribute | Calls `filter_results()` with extracted parameters |
| Ask about specific resort | Calls `fetch_live_snow()` for that resort_id |
| Request webcam image | Calls `fetch_webcam()`, returns image inline |
| Ask about history/reliability | Calls `get_historical_reliability()` |
| Change dates | Calls `search_flights_readonly()` and `search_hotels_readonly()` for new dates |
| Exclude airline/hotel type | Updates filter state for remainder of session |
| Express booking intent | Calls `create_plan_artifact()`, renders Plan Card |

**Plan confirmation trigger phrases** (agent recognises these as booking intent):
- "let's go with [X]"
- "book [X]"
- "I'll take [X]"
- "go ahead with [X]"
- "confirm [X]"
- Any variant where user selects a specific destination + date combination

**Chat agent must NOT:**
- Suggest specific prices without a real-time API call confirming them
- Confirm or imply a booking has been made
- Ask the user to type payment details into the chat
- Retain session history beyond the current session (Redis TTL: 24 hours)

---

### 6.5 Booking Engine

**Pre-conditions (all must be true before booking service proceeds):**
- Valid `trip_plan_id` exists in DB with status `draft`
- User is authenticated (valid session)
- `user_prefs.stripe_payment_method` is set
- All plan data (flight ref, hotel ref, dates) is present and not expired

**Booking sequence (strictly sequential — no parallelism):**

1. Load and validate plan artifact
2. Display full cost breakdown to user
3. Wait for explicit UI button press ("Book Now — £X")
4. Write to `consent_log` (must succeed — if DB write fails, abort)
5. Call Duffel API: hold flight (temporary hold, not charged)
6. Call Hotel API: hold accommodation
7. Call Stripe: authorise charge
8. Call Duffel API: confirm flight booking (now charged)
9. Call Hotel API: confirm accommodation booking
10. Update `bookings` table with all refs and status `confirmed`
11. Update `trip_plans` status to `booked`
12. Dispatch confirmation to email + Telegram

**Failure handling per step:**

| Step | On Failure |
|---|---|
| 1–4 | Abort, show error in UI, no external calls made |
| 5 (flight hold) | Abort, log, notify user. No hotel or payment calls |
| 6 (hotel hold) | Release flight hold via Duffel, abort, log, notify |
| 7 (payment auth) | Release both holds, abort, log, notify |
| 8 (flight confirm) | Refund Stripe auth, release hotel hold, abort, log, notify |
| 9 (hotel confirm) | Attempt Duffel cancellation, attempt Stripe refund, log, notify with manual follow-up instructions |
| 10–12 | Booking is complete — log failure separately, retry delivery only |

**Price lock:** Displayed prices are valid for 10 minutes only. If user takes longer, re-fetch and re-display before allowing confirmation.

---

## 7. Agent Tool Specifications

All tools available to the chat agent. No other tools exist in the chat agent's environment.

### `filter_results`
```
Purpose:  Filter the current weekly run results
Inputs:   min_depth_cm: int | None
          max_altitude_m: int | None
          min_altitude_m: int | None
          countries: list[str] | None
          exclude_resort_ids: list[UUID] | None
          min_score: int | None
Output:   List of snow_scores records matching filters, ranked
Side effects: None — read-only from DB cache
```

### `fetch_live_snow`
```
Purpose:  Fetch real-time snow data for a specific resort
Inputs:   resort_id: UUID
Output:   Current depth, temperature, 5-day forecast from Open-Meteo (live call)
Side effects: Writes result to snow_scores if newer than cached value
Rate limit: Max 10 live calls per chat session
```

### `fetch_webcam`
```
Purpose:  Return current webcam image for a resort
Inputs:   resort_id: UUID
Output:   Image URL from webcam_snapshots (cached) or live Playwright fetch
Side effects: None
Fallback: Return cached image with timestamp if live fetch fails
```

### `search_flights_readonly`
```
Purpose:  Search available flights — no booking capability
Inputs:   destination_airports: list[str]   -- IATA codes
          outbound_date: date
          return_date: date
          max_price_gbp: float | None
          excluded_airlines: list[str] | None
          cabin_class: str = 'economy'
Output:   Top 5 flight options with carrier, times, price, Amadeus ref
Side effects: None — no booking initiated
API:      Amadeus Flight Search (read-only key)
```

### `search_hotels_readonly`
```
Purpose:  Search available accommodation — no booking capability
Inputs:   destination: str
          checkin: date
          checkout: date
          max_price_per_night_gbp: float | None
          property_type: str | None    -- 'hotel','hostel','apartment'
Output:   Top 5 accommodation options with name, price, address
Side effects: None
API:      Booking.com Affiliate (search only)
```

### `get_historical_reliability`
```
Purpose:  Return historical snow reliability for a resort and month
Inputs:   resort_id: UUID
          month: int  -- 1-12
Output:   % of weekends meeting threshold in last 5 years, year-by-year breakdown
Side effects: None — reads from snow_scores historical records
```

### `create_plan_artifact`
```
Purpose:  Ends the chat exploration phase, produces a concrete trip plan
Inputs:   resort_id: UUID
          outbound_date: date
          return_date: date
          flight_option: dict   -- from search_flights_readonly result
          hotel_option: dict    -- from search_hotels_readonly result
          notes: str | None
Output:   plan_id: UUID, full plan artifact JSON, total_estimated_gbp
Side effects:
  - Writes to trip_plans table (status: draft)
  - Dispatches plan (not booking) to email + Telegram
  - Renders Plan Card in UI with "Confirm & Book" button
  - Ends the active chat session
Cannot be called: more than once per chat session
Cannot be called: if flight_option or hotel_option is None
```

---

## 8. API Specifications

### 8.1 Main FastAPI Service Endpoints

#### Dashboard
```
GET  /api/dashboard/weekly
     Returns: top 10 snow_scores for latest weekly_run, current mode
     Auth: required
     Cache: reads from DB — no live API calls

GET  /api/dashboard/resorts
     Returns: full resort list with mode tags
     Auth: required

POST /api/dashboard/refresh
     Triggers: fresh weekly run on demand
     Auth: required, admin only
     Returns: run_id, estimated completion time
```

#### User Preferences
```
GET  /api/user/prefs
     Returns: current user_prefs record

PATCH /api/user/prefs
      Body: { mode?, budget_total_gbp?, preferred_airports?,
              excluded_airlines?, email?, telegram_chat_id? }
      Auth: required
```

#### Chat
```
WS   /api/chat/session
     Opens WebSocket for chat session
     Auth: required
     Protocol: JSON messages {role, content, tool_calls?}

POST /api/chat/session/{session_id}/end
     Triggers: memory extraction + pgvector write
     Auth: required
```

#### Plans
```
GET  /api/plans
     Returns: all trip_plans for user, ordered by created_at desc

GET  /api/plans/{plan_id}
     Returns: full plan artifact

DELETE /api/plans/{plan_id}
       Updates: status to 'cancelled' (if not booked)
```

### 8.2 Booking Service Endpoints

All under `/booking/*` — separate auth middleware, separate environment.

```
GET  /booking/plans/{plan_id}/summary
     Returns: full cost breakdown for display before confirmation
     Validates: plan is draft, prices not expired (< 10 min old)
     Auth: required

POST /booking/plans/{plan_id}/confirm
     Body: { user_confirmed: true }  -- must be explicit boolean true
     Triggers: full booking sequence
     Writes: consent_log entry before any API calls
     Returns: booking_id, status stream
     Auth: required

GET  /booking/{booking_id}/status
     Returns: current booking status + all refs
     Auth: required

GET  /booking/history
     Returns: all completed bookings for user
     Auth: required
```

### 8.3 Telegram Bot Commands

```
/start          Register user, link telegram_chat_id to user_prefs
/mode           Show current mode (sports/tourism)
/mode sports    Switch to Winter Sports mode
/mode tourism   Switch to Snow Tourism mode
/budget [£X]    Set budget envelope
/report         Request latest snow report immediately
/plan           Show latest trip plan (if one exists)
/book [plan_id] Show cost summary for plan (Phase 8 only)
/confirm [plan_id]  Trigger booking for plan (Phase 8 only)
/help           List all commands
```

---

## 9. Non-Functional Requirements

### 9.1 Performance

| Metric | Requirement |
|---|---|
| Weekly run completion | < 30 minutes end-to-end |
| Dashboard page load | < 2 seconds (reading from DB cache) |
| Chat response first token | < 3 seconds |
| Webcam screenshot capture | < 5 seconds per resort |
| Booking flow completion | < 60 seconds from confirm button press |
| pgvector retrieval | < 500ms per query |

### 9.2 Reliability

| Metric | Requirement |
|---|---|
| Weekly run success rate | ≥ 95% complete without errors |
| Booking completion rate | ≥ 95% (once payment authorised) |
| Telegram delivery | ≥ 99% of Friday reports delivered |
| Partial failure handling | Always notified, never silent |

### 9.3 Security

- Raw card numbers never stored on application servers (Stripe tokenisation)
- Stripe PaymentMethod token stored encrypted at rest
- All API keys in environment variables (dev) / AWS Secrets Manager (prod)
- Booking service isolated from chat service at network level (separate containers, no shared env)
- Consent log is append-only — no deletes permitted
- All endpoints require authentication
- HTTPS only in production

### 9.4 Data Retention

| Data | Retention |
|---|---|
| Snow scores | 3 years (trend analysis) |
| Weekly run logs | 3 years |
| Webcam snapshots | 90 days |
| Redis session history | 24 hours (TTL enforced) |
| Chat session raw messages | Not persisted — Redis only |
| Booking records | 7 years (financial records) |
| Consent log | 7 years (legal compliance) |
| pgvector embeddings | Indefinite (user memory) |

### 9.5 Cost Controls

| Component | Control |
|---|---|
| OpenAI API | GPT-4o-mini for high-volume tasks; GPT-4o only for chat agent + plan generation |
| OpenAI API | Max 70 summarisation calls per weekly run (one per resort) |
| Amadeus API | Search calls only triggered from chat on user request — not in automated run |
| Open-Meteo API | Free tier — no cost control needed |
| Redis | 24-hour TTL on all session keys — no unbounded growth |
| pgvector | Deduplication check before every preference write |

---

## 10. Phase Implementation Plan

### Phase 1 — Snow Intelligence Core

**Deliverables:**
- `resorts` table seeded with 40 sports resorts + 30 tourism destinations
- Open-Meteo integration (current depth, forecast, historical archive)
- Snow Score engine with minimum viable gate
- Mode-first routing (separate resort lists, separate thresholds)
- Weekly run writer (results to `weekly_runs` + `snow_scores` tables)
- APScheduler Friday trigger

**Acceptance criteria:**
- Weekly run completes in < 30 minutes
- Scores produced for all passing resorts
- Gate exclusions logged with reasons
- Manual verification: 4 consecutive weekend runs match known conditions

**Stack:** Python, LangGraph, SQLAlchemy, Alembic, PostgreSQL, Open-Meteo API, APScheduler, Docker Compose

---

### Phase 2 — Qualitative Signal Layer

**Deliverables:**
- Reddit scraper (praw, read-only)
- SnowForecast.com RSS parser (feedparser)
- Resort official report scraper (Playwright + BeautifulSoup4)
- Tourism mode qualitative sources (travel blog RSS)
- GPT-4o-mini summarisation per resort
- Qualitative Signal Score replacing neutral placeholder
- `qualitative_signals` table populated
- pgvector extension enabled, resort knowledge corpus seeding begins

**Dependencies:** Phase 1 complete and validated

**Acceptance criteria:**
- Qualitative scores adjust Snow Score visibly week-to-week
- Negative keywords correctly suppress scores for known poor-condition resorts
- pgvector contains at least 1 embedding per resort after first run

**Stack:** praw, feedparser, Playwright, BeautifulSoup4, GPT-4o-mini, pgvector, text-embedding-3-small

---

### Phase 3 — Data Persistence Layer

**Can run in parallel with Phase 2.**

**Deliverables:**
- Full PostgreSQL schema (all tables from Section 5.1)
- Full pgvector schema (all vector tables from Section 5.2)
- Redis setup with key schema from Section 5.3
- DB access rules enforced in code (no cross-writes between services)
- Alembic migration baseline
- Docker Compose: FastAPI + PostgreSQL + Redis

**Acceptance criteria:**
- All tables created with correct constraints
- pgvector cosine similarity queries return results in < 500ms
- Redis keys expire correctly per TTL spec
- No cross-service table writes possible (enforced by ORM layer access controls)

**Stack:** PostgreSQL 16 + pgvector, Redis, SQLAlchemy, Alembic, Docker Compose

---

### Phase 4 — Web Dashboard + Delivery Channels

**Deliverables:**
- FastAPI routes: `/api/dashboard/weekly`, `/api/dashboard/resorts`, `/api/user/prefs`
- React dashboard: snow score cards, mode toggle, budget input, last-updated badge, webcam placeholders
- Telegram bot: Friday weekly brief, all commands from Section 8.3 except `/book` and `/confirm`
- SendGrid HTML email: weekly report template
- Single delivery event system (one trigger → both Telegram + email)
- Failure notification format implemented

**Acceptance criteria:**
- Dashboard loads in < 2 seconds reading from DB cache
- Friday Telegram message delivered by 18:15 UTC
- Email received within 5 minutes of Telegram message
- Failure notifications sent within 2 minutes of run failure

**Stack:** FastAPI, React 18, Tailwind, shadcn/ui, React Query, Zustand, SendGrid, python-telegram-bot

---

### Phase 5 — Chat Interface + Plan Artifact

**Deliverables:**
- LangGraph chat agent with all 7 tools from Section 7
- Memory retrieval system (pgvector query on session start per Section 6.2)
- Session memory write (pgvector on session end)
- WebSocket chat endpoint
- React chat panel with streaming responses
- Plan Card UI component (rendered on `create_plan_artifact` call)
- "Confirm & Book" button (opens booking flow — Phase 7)
- Plan dispatch to email + Telegram on creation
- All booking-related tools absent from chat agent environment

**Acceptance criteria:**
- Chat agent correctly identifies all intent types from Section 6.4
- `create_plan_artifact` triggers Plan Card rendering within 2 seconds
- Memory context visibly influences agent responses (test: express preference in session 1, verify used in session 2)
- Attempting to add booking tools to chat agent raises build-time error (enforced by tool registry)

**Stack:** LangGraph, GPT-4o, GPT-4o-mini, pgvector, text-embedding-3-small, Redis, WebSockets, Amadeus API, React

---

### Phase 6 — Webcam Visual Evidence

**Can run in parallel with Phase 5.**

**Deliverables:**
- `webcam_url` column populated for all 70 resorts (manual curation)
- Playwright Friday morning job (runs before weekly run) capturing top 10 webcam screenshots
- GPT-4o vision classification per image
- `webcam_snapshots` table populated
- Dashboard webcam thumbnails (replaces placeholders)
- `fetch_webcam` tool returns real images
- Vision classification score feeds into Qualitative Signal Score

**Acceptance criteria:**
- Webcam screenshots captured for ≥ 80% of top 10 resorts (some cameras offline is acceptable)
- Vision classification matches manual assessment in ≥ 85% of test cases
- `camera_offline` classification returned within 5 seconds for unresponsive cameras

**Stack:** Playwright, GPT-4o (vision), AWS S3 / local filesystem, PostgreSQL

---

### Phase 7 — Booking Engine

**Deliverables:**
- Separate FastAPI booking service (`/booking/*` routes)
- Own Docker container with isolated environment (Duffel, Stripe, Hotel API keys)
- Full booking sequence from Section 6.5
- Stripe Payment Element in web UI (card setup flow)
- All failure handling from Section 6.5 failure table
- Price lock validation (10-minute window)
- Booking confirmation dispatch (email + Telegram)
- Consent log implementation
- `bookings` table status machine

**Acceptance criteria:**
- Booking service container has no OpenAI, Amadeus search, or chat-related env vars
- Chat service container has no Duffel, Stripe, or hotel booking env vars
- End-to-end test booking completes without partial failures
- Partial failure scenarios all trigger correct rollback + user notification
- Consent log entry created before every external API call in booking sequence
- No booking possible via chat message (tested: sending "book this" in chat must not trigger booking)

**Stack:** Duffel API, Booking.com Affiliate API, Stripe Payment Element + API, FastAPI, AWS Secrets Manager, PostgreSQL

---

### Phase 8 — Telegram Booking Trigger *(Optional)*

**Prerequisite:** Phase 7 stable for minimum 4 real bookings.

**Deliverables:**
- `/book <plan_id>` command — returns cost summary
- `/confirm <plan_id>` command — triggers booking service
- `consent_log.channel` = `'telegram'` for Telegram-originated bookings
- Same failure handling, same rollback logic as Phase 7
- Same price lock validation

**Acceptance criteria:**
- `/book` without `/confirm` does not initiate any booking or payment
- `/confirm` without prior `/book` in same session is rejected
- Booking flow identical to Phase 7 in all respects except UI channel

**Stack:** python-telegram-bot, existing booking service (no new booking logic added)

---

## 11. Tech Stack Reference

### Core Stack

| Category | Technology | Version | Purpose |
|---|---|---|---|
| Language | Python | 3.11+ | Primary throughout |
| Orchestration | LangGraph | Latest | Agent workflow, conditional routing, tool execution |
| Scheduling | APScheduler | 3.x | Weekly Friday trigger |
| Backend | FastAPI | Latest | Main API + booking service |
| Validation | Pydantic | v2 | Data validation, schema enforcement |
| ORM | SQLAlchemy | 2.x | All database operations |
| Migrations | Alembic | Latest | Schema versioning |
| Frontend | React | 18 | Dashboard + chat interface |
| Styling | Tailwind CSS | 3.x | Utility-first styling |
| Components | shadcn/ui | Latest | UI component library |
| State (server) | React Query | 5.x | Server state, caching |
| State (client) | Zustand | Latest | Mode, budget, session |
| Realtime | WebSockets | — | Chat message streaming |

### Data Layer

| Category | Technology | Purpose |
|---|---|---|
| Primary DB | PostgreSQL 16 | Structured data |
| Vector store | pgvector (extension) | Semantic memory within Postgres |
| Cache / session | Redis | Ephemeral session data, rate limits |
| Dev DB | SQLite | Local development only |

### LLM & Embeddings (All OpenAI)

| Model | Use Case | Justification |
|---|---|---|
| GPT-4o | Chat agent, webcam vision, plan artifact generation | Strong tool use + reasoning required |
| GPT-4o-mini | Qualitative summarisation (×70/week), session memory extraction | High volume, straightforward extraction — cost sensitive |
| text-embedding-3-small | All pgvector embeddings | Fast, cheap, 1536 dimensions, single SDK |
| SDK | openai (Python) | Single vendor, single key for all LLM tasks |

### External APIs

| Service | API | Tier | Purpose |
|---|---|---|---|
| Weather/Snow | Open-Meteo | Free | Current depth, forecast, 5yr historical |
| Snow reports | SnowForecast.com | Scraping | Resort snow reports |
| Reddit | praw | Free (rate-limited) | Community condition reports |
| RSS | feedparser | — | Blog + SnowForecast feeds |
| Flight search | Amadeus | Free dev tier | Read-only flight search in chat |
| Flight booking | Duffel API | Per-booking commission | Hold + confirm flight bookings |
| Hotel | Booking.com Affiliate | Application required | Hotel search + booking |
| Flight fallback | Kiwi.com Tequila | Free tier | Backup flight search |
| Payment | Stripe | Standard pricing | Card tokenisation + charge |
| Email | SendGrid | Free tier (100/day) | HTML weekly report + confirmations |
| Telegram | python-telegram-bot | Free | Bot interface |
| File storage | AWS S3 | Pay-per-use | Webcam screenshots |
| Scraping | Playwright + BeautifulSoup4 | — | JS pages + HTML parsing |

### Infrastructure

| Environment | Technology | Usage |
|---|---|---|
| Dev | Docker + Docker Compose | Local multi-service stack |
| Dev hosting | Railway or Render | Simple cloud hosting Phase 1–6 |
| Prod containers | AWS ECS Fargate | Phase 7+ |
| Prod DB | AWS RDS PostgreSQL | Managed, automated backups |
| Prod cache | AWS ElastiCache Redis | Managed Redis |
| Prod files | AWS S3 | Webcam image storage |
| Prod proxy | Nginx | Reverse proxy, TLS termination |
| Secrets (dev) | python-dotenv | `.env` file |
| Secrets (prod) | AWS Secrets Manager | Phase 7+ |

### Testing

| Tool | Purpose |
|---|---|
| pytest | Unit + integration tests |
| pytest-asyncio | Async test support |
| Playwright (test) | Frontend E2E tests |
| httpx TestClient | FastAPI route testing |

---

## 12. Testing Requirements

### 12.1 Phase 1 — Snow Score Engine Tests

```
test_gate_excludes_shallow_sports_depth
  Input: resort with 35cm depth, mode=sports
  Expected: passed_gate=False, reason='depth_below_threshold'

test_gate_excludes_rain_forecast
  Input: resort with 60cm depth, mode=sports, 3-day rain forecast
  Expected: passed_gate=False, reason='rain_in_forecast'

test_score_components_sum_correctly
  Input: known score values for each component
  Expected: total = sum of components, within 0–100

test_historical_reliability_calculation
  Input: 5 years of weekend depth data
  Expected: reliability_pct = (weekends_above_threshold / total_weekends) × 100

test_mode_routing_uses_correct_resort_list
  Input: mode='tourism'
  Expected: only tourism-tagged resorts evaluated, sports resorts not fetched

test_weekly_run_writes_to_db
  Input: completed run
  Expected: weekly_runs record created, snow_scores records for all passing resorts
```

### 12.2 Phase 5 — Chat Agent Tests

```
test_booking_tools_absent_from_chat_agent
  Assert: chat agent tool registry contains no booking-related tools
  Assert: DUFFEL_API_KEY not in chat service environment
  Assert: STRIPE_SECRET_KEY not in chat service environment

test_create_plan_artifact_triggers_plan_card
  Input: user selects resort + dates + flight + hotel
  Expected: trip_plans record created, WebSocket message with plan_card type sent

test_memory_retrieval_injects_context
  Setup: preference embedding exists for user
  Input: user message related to that preference
  Expected: retrieved context present in agent system prompt

test_chat_booking_intent_does_not_trigger_booking
  Input: user sends "book this" in chat
  Expected: no calls to /booking/* endpoints, no consent_log entry created
```

### 12.3 Phase 7 — Booking Engine Tests

```
test_consent_logged_before_api_calls
  Mock: Duffel API, Hotel API, Stripe
  Action: trigger booking sequence
  Assert: consent_log entry exists BEFORE first mock was called

test_flight_hold_failure_aborts_sequence
  Mock: Duffel hold returns error
  Expected: no hotel API call, no Stripe call, booking status='failed'

test_hotel_hold_failure_releases_flight
  Mock: Duffel hold succeeds, hotel hold returns error
  Expected: Duffel release called, no Stripe call, booking status='failed'

test_price_lock_expired_blocks_confirm
  Setup: plan created 11 minutes ago
  Action: attempt /booking/plans/{id}/confirm
  Expected: 409 response, no consent logged, no API calls

test_booking_not_triggerable_from_chat
  Action: POST to /booking/* from chat service network namespace
  Expected: connection refused (network isolation enforced)
```

---

## 13. Open Questions & Decisions Log

| # | Question | Status | Decision |
|---|---|---|---|
| 1 | Booking.com Affiliate API requires application approval — what is fallback if rejected? | Open | Fallback: RapidAPI hotel aggregator endpoint, or Stays.net API |
| 2 | Reddit API restrictions post-2023 — is praw sufficient for weekly read-only use? | Open | Start with praw free tier; if blocked, switch to targeted HTML scraping of public subreddit pages |
| 3 | Duffel API commission per booking — confirm acceptable commercial terms before Phase 7 | Open | Review Duffel pricing page before Phase 7 begins |
| 4 | Single user vs multi-user — `user_id` in schema supports multi-user but Phase 1–8 treats as single user | Decided | Single user in v1. Schema is multi-user-ready for future. |
| 5 | OpenAI rate limits on dev/low-balance account during 70-resort weekly run | Decided | GPT-4o-mini for all summarisation; batching with exponential backoff implemented from Phase 2 |
| 6 | Webcam URL curation — 70 resorts, manual effort | Decided | One-time manual effort in Phase 6; stored in resorts table; updated as needed |
| 7 | Phase 8 (Telegram booking) — risk of accidental `/confirm` commands | Decided | Require `/book` in same session before `/confirm` is accepted; session window = 30 minutes |
| 8 | AWS vs Railway/Render for Phases 1–6 | Decided | Railway for simplicity until Phase 7; migrate to AWS ECS at Phase 7 when payment handling begins |
| 9 | Should plan dispatch (email + Telegram) include booking button / deep link? | Open | Yes — link should deep-link to web app plan view with "Confirm & Book" button pre-visible |
| 10 | Group bookings / plus-one in v2? | Deferred | Out of scope v1. Schema `trip_plans` should add `party_size` column in v2 |

---

*End of Specification v1.0*
