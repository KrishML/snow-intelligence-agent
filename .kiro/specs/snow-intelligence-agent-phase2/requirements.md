# Requirements Document

## Introduction

Phase 2 of the Snow Intelligence Agent extends the Phase 1 snow data pipeline with five new capabilities that transform the system from a pure meteorological scorer into a multi-signal, conversational trip-planning assistant.

The five deliverables are:

1. **Qualitative Signal Scorer** — replaces the hardcoded `score_qualitative=10` placeholder by scraping Reddit posts, RSS feeds, and official resort condition pages, summarising them with GPT-4o-mini, and producing a sentiment-based score (0–20 pts).
2. **Webcam Classifier** — captures resort webcam screenshots via Playwright and classifies each image with GPT-4o vision into one of four conditions; feeds the classification back into the qualitative score component.
3. **LangGraph Chat Agent** — a stateful, memory-aware conversational agent backed by GPT-4o that lets users explore and refine resort recommendations through natural language; produces a structured Plan Artifact at session end.
4. **Memory System** — PostgreSQL + pgvector semantic memory that persists user preferences, trip feedback, resort knowledge, and session summaries across chat sessions.
5. **Delivery System** — automated weekly report dispatch via Telegram bot and SendGrid email after each successful weekly run.

Phase 1 deliverables (FastAPI app, database schema, Open-Meteo client, Snow Score Engine, weekly run orchestrator, APScheduler trigger, 70-resort seed data) are **already implemented** and must not be re-specified here. Phase 2 builds on top of them.

---

## Glossary

- **System**: The Snow Intelligence Agent as a whole
- **Qualitative_Signal_Scorer**: The component that fetches Reddit posts, RSS entries, and official resort condition reports, summarises them with GPT-4o-mini, and produces a 0–20 qualitative score
- **Webcam_Classifier**: The component that captures resort webcam screenshots via Playwright and classifies each image using GPT-4o vision
- **Chat_Agent**: The LangGraph-based stateful conversational agent powered by GPT-4o; operates in strict isolation from all booking capabilities
- **Memory_System**: The pgvector-backed semantic retrieval and write system that stores user preferences, trip feedback, resort knowledge, and session summaries
- **Delivery_System**: The component that dispatches formatted weekly snow reports via Telegram bot and SendGrid email
- **Plan_Artifact**: The structured JSON document written to the `trip_plans` table at the end of a successful chat session, containing resort ID, dates, flight option, hotel option, and cost breakdown
- **Weekly_Cache**: The latest weekly run results cached in the database; read by the Chat_Agent and Delivery_System without re-running live API calls
- **Weekly_Run_Engine**: The scheduled Phase 1 orchestrator (already implemented); Phase 2 adds qualitative and webcam steps to its pipeline
- **Snow_Score_Engine**: The Phase 1 pure-function scorer (already implemented); Phase 2 replaces its hardcoded `score_qualitative=10` with the live qualitative signal
- **GPT-4o**: The OpenAI model used for Chat_Agent responses, webcam vision classification, and plan artifact generation
- **GPT-4o-mini**: The OpenAI model used for qualitative signal summarisation and session memory extraction
- **text-embedding-3-small**: The OpenAI embedding model used for all pgvector writes and retrieval queries
- **LangGraph**: The Python library used to implement the Chat_Agent's stateful, tool-calling graph workflow
- **Amadeus**: The read-only flight search SDK available as a tool inside the Chat_Agent only
- **pgvector**: The PostgreSQL extension that provides vector(1536) columns and IVFFlat cosine similarity indexes
- **praw**: The Python Reddit API Wrapper used to fetch subreddit posts
- **feedparser**: The Python RSS/Atom feed parsing library used to fetch SnowForecast.com entries
- **python-telegram-bot**: The async Telegram Bot SDK used by the Delivery_System
- **SendGrid**: The email delivery service used as the secondary delivery channel
- **Booking_Firewall**: The architectural constraint that prohibits any booking credential or network path from existing in the Chat_Agent container
- **Resort_Mode**: The per-resort `mode` field (`sports`, `tourism`, or `both`) that governs which resort list is used for each operation

---

## Requirements

---

### Requirement 10: Qualitative Signal Scorer

**User Story:** As the user, I want the snow score to reflect community reports, official resort communications, and social media sentiment, so that the qualitative score component is based on real-world signals rather than a neutral placeholder.

#### Acceptance Criteria

1. WHEN a resort passes the Minimum_Viable_Gate, THE Qualitative_Signal_Scorer SHALL fetch posts from the subreddits `r/skiing`, `r/snowboarding`, and `r/Alps` (for `sports` mode) or `r/travel` and `r/europe` (for `tourism` mode) that mention the resort name using the praw library, retrieving the 10 most recent posts within the last 7 days.
2. WHEN a resort passes the Minimum_Viable_Gate, THE Qualitative_Signal_Scorer SHALL fetch the latest 5 entries from the SnowForecast.com RSS feed for that resort using feedparser.
3. THE Qualitative_Signal_Scorer SHALL pass the aggregated raw text from all sources (Reddit posts, RSS entries) to GPT-4o-mini with a structured prompt requesting: a concise 2–3 sentence summary of current resort conditions, a sentiment classification of `positive`, `neutral`, or `negative`, and a list of any detected negative indicator keywords from the set `{icy, slush, closed, bare, thin, rock, artificial}`.
4. THE Qualitative_Signal_Scorer SHALL compute the qualitative score from the GPT-4o-mini sentiment classification as follows: `positive` = 15–20 pts (scaled by keyword absence), `neutral` = 10 pts, `negative` = 0–5 pts (scaled by keyword count); WHEN no source data is available, THE Qualitative_Signal_Scorer SHALL assign 10 pts as the neutral default.
5. THE Qualitative_Signal_Scorer SHALL write one `qualitative_signals` database record per resort per weekly run containing: the resort ID, weekly run ID, source type (`reddit`, `rss`), raw text snippet (truncated to 1000 characters), GPT-4o-mini summary, sentiment classification, negative keywords detected, and computed score.
6. THE Qualitative_Signal_Scorer SHALL embed the GPT-4o-mini summary using text-embedding-3-small and write the embedding to the `resort_knowledge_embeddings` pgvector table.
7. IF the Reddit API is unavailable due to rate-limiting, network timeout, or authentication failure, THEN THE Qualitative_Signal_Scorer SHALL log the error, skip Reddit for all affected resorts, use RSS and any other available sources, and include the failure in the weekly run's error log; IF all sources fail for a resort, THE Qualitative_Signal_Scorer SHALL assign the neutral score of 10 pts.
8. THE Qualitative_Signal_Scorer SHALL complete processing for all passing resorts within 8 minutes to stay within the 30-minute weekly run budget.

---

### Requirement 11: Webcam Classifier

**User Story:** As the user, I want AI-classified webcam screenshots from each top resort included in the weekly run, so that I have visual confirmation of snow conditions alongside the quantitative score.

#### Acceptance Criteria

1. WHEN the Weekly_Run_Engine runs, THE Webcam_Classifier SHALL attempt to capture a screenshot of each resort's `webcam_url` field using Playwright for all resorts that pass the Minimum_Viable_Gate; IF a resort's `webcam_url` is NULL, THE Webcam_Classifier SHALL skip that resort and record a `camera_offline` classification.
2. THE Webcam_Classifier SHALL pass each successfully captured screenshot as a base64-encoded image to the GPT-4o vision API with a structured prompt requesting classification into exactly one of: `snow_good`, `snow_poor`, `no_snow`, or `camera_offline`, along with a one-sentence description of what is visible.
3. WHEN a webcam URL is unresponsive or Playwright navigation times out, THE Webcam_Classifier SHALL record a `camera_offline` classification and continue processing remaining resorts without blocking the weekly run; THE timeout for each webcam capture attempt SHALL be 5 seconds.
4. THE Webcam_Classifier SHALL write each result to the `webcam_snapshots` table containing: resort ID, weekly run ID, snapshot timestamp, classification (`snow_good`, `snow_poor`, `no_snow`, `camera_offline`), GPT-4o description, and the relative file path where the screenshot PNG is stored on disk.
5. WHEN a webcam classification of `snow_good` is available for a resort, THE Snow_Score_Engine SHALL add 3 bonus points to that resort's qualitative score component, capped at the 20-point maximum; WHEN the classification is `snow_poor`, THE Snow_Score_Engine SHALL apply a 2-point penalty to the qualitative score, floored at 0.

---

### Requirement 12: LangGraph Chat Agent

**User Story:** As the user, I want to explore snow destinations conversationally with a memory-aware agent that remembers my preferences and can look up live flights, so that I can plan a trip through natural dialogue without manually cross-referencing multiple tools.

#### Acceptance Criteria

1. THE Chat_Agent SHALL be implemented as a LangGraph `StateGraph` with GPT-4o as the language model and tool-calling nodes for each of the tools defined in Requirement 13 of the master requirements document.
2. THE Chat_Agent SHALL operate in a Docker service that contains no Duffel API key, no hotel booking credentials, and no Stripe API key; THE Chat_Agent service SHALL have no network route to any endpoint under `/booking/*`.
3. WHEN a chat session begins via `POST /api/chat/session`, THE System SHALL create a session ID, initialise a LangGraph `StateGraph` instance, retrieve memory context from the Memory_System (Requirement 13), inject the retrieved context into the LangGraph system prompt, and return the session ID to the client.
4. THE Chat_Agent SHALL stream LangGraph token output to the client via WebSocket at `WS /api/chat/session/{session_id}/messages` with the first token delivered within 3 seconds of the user submitting a message.
5. WHEN a chat session ends via `POST /api/chat/session/{session_id}/end`, THE Chat_Agent SHALL trigger asynchronous memory extraction and writing (Requirement 13) before confirming session closure to the client.
6. THE Chat_Agent SHALL read snow condition data from the Weekly_Cache by default; WHEN the user explicitly requests live conditions for a specific resort, THE Chat_Agent SHALL call the `fetch_live_snow` tool; the `fetch_live_snow` tool SHALL be rate-limited to at most 10 calls per session.
7. THE Chat_Agent SHALL never suggest specific prices without a real-time Amadeus API call in the current session confirming them.
8. THE Chat_Agent SHALL never use language that implies a booking has been made, confirmed, or is in progress.

---

### Requirement 13: Memory System

**User Story:** As the user, I want the chat agent to remember my preferences and resort feedback across sessions, so that I do not need to re-state my preferences in every conversation.

#### Acceptance Criteria

1. WHEN a chat session begins, THE Memory_System SHALL embed the user's first message using text-embedding-3-small and execute pgvector cosine similarity queries to retrieve: the top 3 matching preference embeddings from `preference_embeddings`, the top 2 matching trip feedback embeddings from `trip_feedback_embeddings`, the top 3 matching resort knowledge embeddings from `resort_knowledge_embeddings` filtered to the current ski season, and the top 1 session summary from `session_summary_embeddings` created within the last 30 days; WHEN fewer embeddings exist than the specified counts, THE Memory_System SHALL proceed with whatever is available.
2. THE Memory_System pgvector cosine similarity retrieval queries SHALL each complete within 500ms.
3. WHEN a chat session ends, THE Memory_System SHALL use GPT-4o-mini to extract discrete preference statements from the session transcript and write each as a separate vector to `preference_embeddings`.
4. WHEN a chat session ends, THE Memory_System SHALL write a single session summary as a vector to `session_summary_embeddings`.
5. BEFORE writing a new preference embedding, THE Memory_System SHALL query `preference_embeddings` for that user and SHALL NOT write the new embedding if an existing embedding has a cosine similarity score of 0.95 or greater with the candidate embedding.
6. THE Memory_System SHALL never store raw session transcripts, payment data, or personally identifiable information in any pgvector table; all pgvector content SHALL be extracted summaries and preference statements only.
7. FOR ALL valid preference embeddings written to and retrieved from the `preference_embeddings` table, THE Memory_System SHALL return the stored embedding as the top result when the original text is embedded and used as the query vector (round-trip retrieval property).

---

### Requirement 14: Delivery System

**User Story:** As the user, I want to receive a formatted weekly snow report on my phone and by email every Friday after the weekly run, so that I get the snow intelligence briefing without opening the web app.

#### Acceptance Criteria

1. WHEN the Weekly_Run_Engine completes successfully, THE Delivery_System SHALL send a Telegram message to the user's registered `telegram_chat_id` by 18:15 UTC on Friday; the message SHALL list the top 5 resorts in ranked order, each showing: resort name, country, snow depth in cm, Snow_Score out of 100, historical reliability percentage, and the GPT-4o-mini qualitative summary sentence.
2. WHEN the Telegram message is successfully dispatched, THE Delivery_System SHALL send a SendGrid HTML email to the user's registered email address within 5 minutes of the Telegram dispatch; the email SHALL contain the same top 5 resort list plus a Snow_Score breakdown table for each resort.
3. WHEN the Weekly_Run_Engine experiences a failure on any component, THE Delivery_System SHALL send a failure notification via Telegram and email within 2 minutes of the failure event; the notification SHALL list each failed component, the failure reason, and whether any results were available from the partial run.
4. THE Delivery_System SHALL use a single delivery trigger event after each completed or failed weekly run to produce both Telegram and email outputs, ensuring content consistency between the two channels.

---

### Requirement 15: Database Schema Extensions for Phase 2

**User Story:** As the developer, I want the Phase 2 database tables and pgvector indexes created via Alembic migration, so that all new components have a schema to write to from the first Phase 2 weekly run.

#### Acceptance Criteria

1. THE System SHALL create a `qualitative_signals` table via Alembic migration with columns: `id` (UUID PK), `weekly_run_id` (FK → `weekly_runs`), `resort_id` (FK → `resorts`), `source_type` (VARCHAR CHECK IN `('reddit','rss')`), `raw_snippet` (TEXT, max 1000 chars), `summary` (TEXT), `sentiment` (VARCHAR CHECK IN `('positive','neutral','negative')`), `negative_keywords` (TEXT[]), `score` (INTEGER), `created_at` (TIMESTAMPTZ server default).
2. THE System SHALL create a `webcam_snapshots` table via Alembic migration with columns: `id` (UUID PK), `weekly_run_id` (FK → `weekly_runs`), `resort_id` (FK → `resorts`), `captured_at` (TIMESTAMPTZ), `classification` (VARCHAR CHECK IN `('snow_good','snow_poor','no_snow','camera_offline')`), `description` (TEXT), `file_path` (TEXT), `created_at` (TIMESTAMPTZ server default).
3. THE System SHALL create a `trip_plans` table via Alembic migration with columns: `id` (UUID PK), `session_id` (VARCHAR), `resort_id` (FK → `resorts`), `outbound_date` (DATE), `return_date` (DATE), `flight_option` (JSONB), `hotel_option` (JSONB), `total_estimated_gbp` (NUMERIC(10,2)), `session_notes` (TEXT), `status` (VARCHAR CHECK IN `('draft','booked','cancelled')`), `created_at` (TIMESTAMPTZ server default), `updated_at` (TIMESTAMPTZ).
4. THE System SHALL create the following pgvector tables via Alembic migration, each with a `vector(1536)` column and a `user_id` column: `preference_embeddings` (`id` UUID PK, `user_id` UUID, `text` TEXT, `embedding` vector(1536), `created_at` TIMESTAMPTZ), `trip_feedback_embeddings` (`id` UUID PK, `user_id` UUID, `text` TEXT, `embedding` vector(1536), `created_at` TIMESTAMPTZ), `resort_knowledge_embeddings` (`id` UUID PK, `resort_id` UUID FK, `season` VARCHAR(9), `text` TEXT, `embedding` vector(1536), `created_at` TIMESTAMPTZ), `session_summary_embeddings` (`id` UUID PK, `user_id` UUID, `session_id` VARCHAR, `text` TEXT, `embedding` vector(1536), `created_at` TIMESTAMPTZ).
5. THE System SHALL create IVFFlat cosine similarity indexes on the `embedding` column of each pgvector table using `lists=100` with `vector_cosine_ops`; THE System SHALL also create a GIN index on `qualitative_signals.negative_keywords` and a B-tree index on `webcam_snapshots(resort_id, captured_at DESC)`.
