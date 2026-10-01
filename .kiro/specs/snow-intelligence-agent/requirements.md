# Requirements Document

## Introduction

The Snow Intelligence & Winter Travel Booking Agent is an automated weekly system that aggregates and scores snow conditions across 70+ European destinations, surfaces the top recommendations every Friday, supports conversational trip exploration via a memory-augmented chat agent, and enables end-to-end flight and hotel booking behind a mandatory human approval gate. The system operates in two modes — Snow Tourism and Winter Sports — with all data fetching, scoring, and display downstream of the mode setting. A booking firewall strictly isolates the chat agent from all financial transaction capabilities.

---

## Glossary

- **System**: The Snow Intelligence & Winter Travel Booking Agent as a whole
- **Weekly_Run_Engine**: The scheduled component that executes the automated Friday snow data collection and scoring pipeline
- **Snow_Score_Engine**: The component that computes a 0–100 composite snow quality score for each resort that passes the minimum viable gate
- **Mode**: The operational mode setting — either `sports` (Winter Sports) or `tourism` (Snow Tourism) — stored in `user_prefs` and applied to every data fetch, threshold, and display
- **Resort**: A European snow destination (ski resort or winter tourism location) in the system's curated list
- **Minimum_Viable_Gate**: The hard exclusion filter applied before scoring; resorts failing any gate condition are excluded entirely
- **Snow_Score**: The 0–100 composite score computed per resort per weekly run, comprising depth, forecast, historical reliability, and qualitative signal components
- **Qualitative_Signal_Scorer**: The component that aggregates Reddit posts, RSS feeds, and official resort reports and uses GPT-4o-mini to produce a qualitative score component
- **Webcam_Classifier**: The component that captures resort webcam screenshots via Playwright and classifies them using GPT-4o vision
- **Chat_Agent**: The LangGraph-based conversational agent with semantic memory and read-only tools; operates in complete isolation from booking capabilities
- **Memory_System**: The pgvector-backed semantic retrieval and write system that persists user preferences, trip feedback, resort knowledge, and session summaries
- **Plan_Artifact**: The structured JSON document produced at the end of a chat session describing a specific resort, dates, flight, and hotel option with full cost breakdown
- **Booking_Service**: The isolated FastAPI service that handles the sequential booking flow including consent gate, Duffel flight booking, hotel booking, and Stripe payment
- **Consent_Log**: The append-only database table that records timestamped user consent before any financial transaction
- **Delivery_System**: The component responsible for dispatching weekly snow reports and plan confirmations via Telegram and SendGrid email
- **Dashboard**: The React web interface showing snow score cards, mode toggle, budget input, webcam thumbnails, and the chat panel
- **Weekly_Cache**: The database-backed cache of the latest weekly run results, read by the Chat_Agent and Dashboard without triggering live API calls
- **APScheduler**: The Python scheduling library that triggers the Weekly_Run_Engine every Friday at 17:00 UTC
- **Open_Meteo**: The free weather API used for current snow depth, 5-day forecast, and 5-year historical archive
- **Duffel**: The flight booking API used by the Booking_Service to hold and confirm flights
- **Stripe**: The payment processor used by the Booking_Service to authorise and capture card charges
- **Amadeus**: The read-only flight search API available as a tool to the Chat_Agent
- **pgvector**: The PostgreSQL extension providing vector similarity search for the Memory_System
- **LangGraph**: The Python library used to implement the Chat_Agent's stateful tool-calling workflow
- **GPT-4o**: The OpenAI model used for the Chat_Agent, webcam vision classification, and plan artifact generation
- **GPT-4o-mini**: The OpenAI model used for qualitative signal summarisation and session memory extraction

---

## Requirements

---

### Requirement 1: Governing Principle — Booking Firewall

**User Story:** As the system operator, I want the chat agent and booking service to be completely isolated execution contexts, so that the conversational interface can never initiate, query, or modify any financial transaction.

#### Acceptance Criteria

1. THE Chat_Agent SHALL operate in an environment that contains no Duffel API key, no hotel booking credentials, and no Stripe key.
2. THE Chat_Agent SHALL have no network path to any endpoint under `/booking/*`.
3. THE Chat_Agent SHALL produce only a read-only Plan_Artifact as output of a session; the Plan_Artifact SHALL be passed to the Booking_Service by plan ID reference only; the Booking_Firewall isolation requirement is satisfied if EITHER the Plan_Artifact is enforced as read-only OR it is passed solely by ID reference with no direct object access from the Chat_Agent context.
4. THE Booking_Service SHALL have no OpenAI API key, no Amadeus search key, and no chat-session-related environment variables in its container environment.
5. WHEN the Chat_Agent environment is started, THE System SHALL raise a build-time error if any booking-related tool is registered in the Chat_Agent's tool registry.

---

### Requirement 2: Governing Principle — Explicit Consent Gate

**User Story:** As the user, I want no financial transaction to occur without my explicit, informed consent via a dedicated UI action, so that I am never charged without knowingly approving a specific itemised cost.

#### Acceptance Criteria

1. WHEN a booking is initiated, THE Booking_Service SHALL immediately display a full itemised cost breakdown showing the outbound flight price, return flight price, hotel total, and grand total in GBP before any further action — including button rendering, API calls, or consent recording — is possible.
2. THE Booking_Service SHALL require an explicit UI button press — not a chat message and not a Telegram text command — to proceed with a booking.
3. THE System SHALL write a `consent_log` record containing the user ID, plan ID, total amount shown, and a UTC timestamp before any call to Duffel, the hotel API, or Stripe is made.
4. IF the `consent_log` write fails, THEN THE Booking_Service SHALL abort the entire booking sequence and make no external API calls.
5. IF any booking step fails, THEN THE Booking_Service SHALL halt the entire sequence with no partial silent completions.

---

### Requirement 3: Governing Principle — Mode-First Architecture

**User Story:** As the user, I want every data fetch, threshold evaluation, and display to be downstream of my current mode setting, so that the system always operates coherently for either snow tourism or winter sports without mixing data sets.

#### Acceptance Criteria

1. WHEN any execution path begins in the Weekly_Run_Engine or Chat_Agent, THE System SHALL read the Mode from `user_prefs` before fetching any resort data.
2. THE Weekly_Run_Engine SHALL select the resort list appropriate to the current Mode: the tourism destination list (~30 locations) for `tourism` mode and the sports resort list (~40 locations) for `sports` mode; IF the Weekly_Run_Engine reads the Mode successfully but fails to select a resort list, THEN THE System SHALL halt and display an explicit error preventing undefined behavior.
3. THE Snow_Score_Engine SHALL apply the Minimum_Viable_Gate thresholds specific to the current Mode for every resort evaluation.
4. THE Dashboard SHALL display score cards, webcam thumbnails, and resort attributes filtered and labelled according to the current Mode.
5. THE System SHALL persist the Mode setting in `user_prefs` and apply it consistently across all components in the same execution context.

---

### Requirement 4: Governing Principle — Score Before Search

**User Story:** As the user, I want travel search results only shown for destinations that have passed the snow score gate, so that I am never presented with booking options for destinations with inadequate snow conditions.

#### Acceptance Criteria

1. THE Chat_Agent tools `search_flights_readonly` and `search_hotels_readonly` SHALL only be called for resort IDs that have a `passed_gate = true` entry in `snow_scores` for the current weekly run; each tool MAY be called independently of the other.
2. WHEN a resort fails the Minimum_Viable_Gate, THE System SHALL exclude that resort from all flight and hotel search results regardless of price.
3. THE Dashboard SHALL display no travel options for resorts that did not pass the Minimum_Viable_Gate in the latest weekly run.

---

### Requirement 5: Governing Principle — Fail Loud, Never Silent

**User Story:** As the user, I want all failures, partial failures, and stale data substitutions to be surfaced immediately, so that I am never misled by incomplete or degraded output.

#### Acceptance Criteria

1. WHEN any data fetch fails during the Weekly_Run_Engine, THE Delivery_System SHALL send a failure notification to the user via both Telegram and email within 2 minutes of the failure.
2. IF stale cached data is substituted for a failed live fetch, THEN THE System SHALL flag the substitution explicitly in the output delivered to the user.
3. THE Weekly_Run_Engine SHALL never silently degrade to partial output; THE Delivery_System SHALL always include a summary of which components succeeded and which failed.
4. WHEN a booking step fails, THE Booking_Service SHALL log the full error context and notify the user via Telegram and email before terminating the sequence.

---

### Requirement 6: Governing Principle — Stateless Weekly Cache

**User Story:** As the user, I want chat responses to be fast and API costs to be controlled, so that the chat agent reads from the cached weekly results by default and only makes live calls when I explicitly request fresh data.

#### Acceptance Criteria

1. THE Chat_Agent SHALL read snow condition data from the Weekly_Cache by default for all user queries that can be answered from the latest weekly run.
2. WHEN the user explicitly requests current live conditions for a specific resort (e.g. "what's the snow like in Bansko right now?"), THE Chat_Agent SHALL call `fetch_live_snow` and return a live result; IF the live call fails, THE Chat_Agent SHALL fall back to cached data and include a disclaimer that the data is not live.
3. THE `fetch_live_snow` tool SHALL be rate-limited to a maximum of 10 live calls per chat session.
4. THE Weekly_Cache SHALL be invalidated and rewritten after each successful Weekly_Run_Engine execution.
5. WHEN the Weekly_Cache is refreshed after a successful Weekly_Run_Engine execution, THE System SHALL block live `fetch_live_snow` calls for a cooling period of 5 minutes to ensure users receive the fresh cached data.

---

### Requirement 7: Weekly Automated Snow Run

**User Story:** As the user, I want a fully automated Friday snow intelligence run that checks all European destinations and produces ranked results, so that I receive fresh, multi-source condition data every week without manual effort.

#### Acceptance Criteria

1. WHEN APScheduler fires at 17:00 UTC every Friday, THE Weekly_Run_Engine SHALL begin execution for all resorts in the Mode-appropriate resort list.
2. THE Weekly_Run_Engine SHALL fetch current snow depth, 5-day forecast, and 5-year historical weekend reliability data from Open_Meteo for each resort in parallel with rate limiting applied.
3. THE Weekly_Run_Engine SHALL complete the full run, including data fetching, scoring, and delivery trigger, within 30 minutes.
4. THE Weekly_Run_Engine SHALL write a `weekly_runs` record with status, resort counts, and error log to the database upon completion.
5. THE Weekly_Run_Engine SHALL write a `snow_scores` record for every resort evaluated, recording whether the resort passed the Minimum_Viable_Gate and, if it failed, the reason for exclusion.
6. THE Weekly_Run_Engine SHALL rank the passing resorts by Snow_Score and write the top 10 to the database.
7. WHEN the Weekly_Run_Engine completes successfully, THE System SHALL trigger a single delivery event that dispatches the weekly report via both Telegram and email.
8. WHEN the Weekly_Run_Engine fails or times out before completion, THE Delivery_System SHALL still send a report to the user via Telegram and email including a summary of the failure and any partial results available.
9. IF the delivery trigger fails after a successful Weekly_Run_Engine execution, THEN THE System SHALL mark the `weekly_runs` record status as `failed` and log the delivery failure.

---

### Requirement 8: Snow Score Engine — Minimum Viable Gate

**User Story:** As the user, I want destinations with inadequate snow to be hard-excluded before scoring, so that I only see resorts that meet minimum viable conditions for my chosen mode.

#### Acceptance Criteria

1. WHEN the current Mode is `sports` and a resort's settled snow depth at resort altitude is below 40cm, THE Snow_Score_Engine SHALL exclude that resort and record `passed_gate = false` with reason `depth_below_threshold`; THE Snow_Score_Engine SHALL exclude a resort when ANY exclusion criterion is met, regardless of which reason is recorded.
2. WHEN the current Mode is `sports` and no open blue or green run is confirmed for a resort, THE Snow_Score_Engine SHALL exclude that resort and record `passed_gate = false` with reason `no_open_run`.
3. WHEN the current Mode is `tourism` and a resort's snow depth is below 15cm, THE Snow_Score_Engine SHALL exclude that resort and record `passed_gate = false` with reason `depth_below_threshold`.
4. WHEN rain is forecast within the 5-day forecast window for a resort in either Mode, THE Snow_Score_Engine SHALL exclude that resort and record `passed_gate = false` with reason `rain_in_forecast`.
5. WHEN a temperature above 3°C is forecast within the 5-day forecast window for a resort in either Mode, THE Snow_Score_Engine SHALL exclude that resort and record `passed_gate = false` with reason `warm_temperature_forecast`.

---

### Requirement 9: Snow Score Engine — Composite Score Calculation

**User Story:** As the user, I want each passing destination to receive a transparent, composite 0–100 score based on multiple data sources, so that I can compare destinations with confidence in the ranking.

#### Acceptance Criteria

1. THE Snow_Score_Engine SHALL compute a composite Snow_Score on a 0–100 scale comprising four components: Current Depth (0–30 pts), Forecast Stability (0–30 pts), Historical Reliability (0–25 pts), and Qualitative Signal (0–20 pts); WHEN the sum of all component scores exceeds 100, THE Snow_Score_Engine SHALL cap the total at 100.
2. WHEN the current Mode is `sports`, THE Snow_Score_Engine SHALL award depth points as follows: below 20cm = 0 pts, 20–39cm = 10 pts, 40–59cm = 20 pts, 60cm or above = 30 pts.
3. WHEN the current Mode is `tourism`, THE Snow_Score_Engine SHALL award depth points as follows: below 10cm = 0 pts, 10–19cm = 10 pts, 20–29cm = 20 pts, 30cm or above = 30 pts.
4. THE Snow_Score_Engine SHALL award forecast stability points as: stable cold and dry = 25 pts, snowfall incoming = +5 pts bonus applied to the baseline, rain in forecast = −15 pts penalty, temperature above 3°C forecast = −10 pts penalty; the maximum Forecast Stability score inclusive of the snowfall bonus is 30 pts; WHEN no specific weather condition is detected, THE Snow_Score_Engine SHALL award 0 points as the default baseline; WHEN multiple conflicting forecast conditions occur simultaneously, THE Snow_Score_Engine SHALL use a priority system where negative conditions (rain in forecast, temperature above 3°C) override positive conditions (stable cold and dry, snowfall incoming bonus).
5. THE Snow_Score_Engine SHALL compute historical reliability as the percentage of weekends in the last 5 years that met the Mode-appropriate threshold, multiplied by 0.25, to produce a score of 0–25 pts.
6. THE Snow_Score_Engine SHALL award qualitative signal points as: positive sentiment = 15–20 pts, neutral or no signal = 10 pts, negative keywords present (icy, slush, closed, bare) = 0–5 pts.
7. THE Snow_Score_Engine SHALL compute the Snow_Score total as the sum of the four component scores, capped at 100, and SHALL ensure the total is within the range 0–100.
8. FOR ALL resorts evaluated, THE Snow_Score_Engine SHALL produce a total score equal to the lesser of 100 and the arithmetic sum of the four component scores without rounding error.

---

### Requirement 10: Qualitative Signal Layer

**User Story:** As the user, I want the snow score to incorporate community reports, official resort communications, and social media signals, so that the ranking reflects real-world conditions beyond raw meteorological data.

#### Acceptance Criteria

1. WHEN a resort passes the Minimum_Viable_Gate, THE Qualitative_Signal_Scorer SHALL fetch posts from the mode-appropriate subreddits (r/skiing, r/snowboarding, r/Alps for sports mode; travel forums for tourism mode) using the praw library.
2. WHEN a resort passes the Minimum_Viable_Gate, THE Qualitative_Signal_Scorer SHALL fetch the latest entries from the SnowForecast.com RSS feed for that resort using feedparser.
3. WHEN a resort passes the Minimum_Viable_Gate, THE Qualitative_Signal_Scorer SHALL scrape the official resort condition report page using Playwright and BeautifulSoup4.
4. THE Qualitative_Signal_Scorer SHALL pass the aggregated raw text from all sources to GPT-4o-mini to produce a concise qualitative summary and a sentiment classification of `positive`, `neutral`, or `negative`.
5. THE Qualitative_Signal_Scorer SHALL extract keywords from the summary and flag negative indicators including `icy`, `slush`, `closed`, and `bare`.
6. THE Qualitative_Signal_Scorer SHALL write a `qualitative_signals` record per source per resort per weekly run to the database.
7. IF the Reddit API is unavailable during a weekly run for any reason including rate-limiting, network timeout, or authentication failure, THEN THE Qualitative_Signal_Scorer SHALL log the error, set the qualitative score to neutral (10 pts) for affected resorts, and include the failure in the run's error log; WHEN the error logging itself fails, THE Qualitative_Signal_Scorer SHALL continue with neutral scoring regardless.
8. THE Weekly_Run_Engine SHALL embed qualitative summaries as vectors and write them to the `resort_knowledge_embeddings` pgvector table after each run.

---

### Requirement 11: Webcam Visual Evidence

**User Story:** As the user, I want webcam screenshots from each top resort classified by AI vision, so that I can see visual confirmation of snow conditions alongside the quantitative score.

#### Acceptance Criteria

1. WHEN the Weekly_Run_Engine runs, THE Webcam_Classifier SHALL capture a screenshot of each top 10 resort's webcam URL using Playwright before the scoring phase completes.
2. THE Webcam_Classifier SHALL pass each captured image to GPT-4o vision and receive a classification of one of: `snow_good`, `snow_poor`, `no_snow`, or `camera_offline`.
3. WHEN a webcam URL is unresponsive, THE Webcam_Classifier SHALL return a `camera_offline` classification within 5 seconds and continue processing remaining resorts.
4. THE Webcam_Classifier SHALL write each snapshot and its classification to the `webcam_snapshots` table and store the image at the configured S3 path.
5. THE Webcam_Classifier SHALL produce classifications matching manual assessment in at least 85% of test cases.
6. THE Webcam_Classifier SHALL successfully capture webcam screenshots for at least 80% of the top 10 resorts per weekly run.
7. THE Snow_Score_Engine SHALL incorporate the webcam vision classification into the Qualitative Signal Score component when a classification is available.

---

### Requirement 12: Memory-Augmented Chat Agent

**User Story:** As the user, I want the chat agent to remember my preferences, past trip feedback, and resort knowledge across sessions, so that I do not have to repeat myself and get personalised recommendations over time.

#### Acceptance Criteria

1. WHEN a chat session begins, THE Memory_System SHALL embed the user's message using text-embedding-3-small and query the pgvector store to retrieve: the top 3 matching preference embeddings, the top 2 matching trip feedback embeddings, the top 3 matching resort knowledge embeddings filtered to the current season, and the top 1 session summary from the last 30 days; WHEN fewer embeddings exist than the specified retrieval counts, THE Memory_System SHALL proceed with whatever embeddings are available rather than failing.
2. THE Chat_Agent SHALL inject the retrieved memory context into the system prompt before passing any message to GPT-4o.
3. WHEN a chat session ends, THE Memory_System SHALL use GPT-4o-mini to extract discrete preference statements from the session history and write each as a separate embedding to `preference_embeddings`.
4. WHEN a chat session ends, THE Memory_System SHALL write a single session summary embedding to `session_summary_embeddings`.
5. BEFORE writing a preference embedding, THE Memory_System SHALL perform a deduplication check and SHALL NOT write the embedding if a semantically equivalent preference already exists for that user.
6. THE Memory_System SHALL never store raw session transcripts in pgvector, never store payment data, and never store PII in the vector store.
7. THE pgvector cosine similarity retrieval queries SHALL complete within 500ms.
8. FOR ALL valid preference embeddings stored and retrieved, THE Memory_System SHALL return the same top result when the original embedding is used as the query (round-trip property).

---

### Requirement 13: Chat Agent Tools

**User Story:** As the user, I want to explore and refine snow destinations conversationally using live data lookups, so that I can make an informed decision about which destination and dates to plan a trip to.

#### Acceptance Criteria

1. THE Chat_Agent SHALL provide exactly the following read-only tools and no others: `filter_results`, `fetch_live_snow`, `fetch_webcam`, `search_flights_readonly`, `search_hotels_readonly`, `get_historical_reliability`, and `create_plan_artifact`.
2. WHEN the user expresses intent to filter resorts by attribute such as minimum depth, country, altitude range, or minimum score, THE Chat_Agent SHALL call `filter_results` with the extracted parameters and return the ranked filtered results.
3. WHEN the user requests current conditions for a specific resort, THE Chat_Agent SHALL call `fetch_live_snow` with the resort ID, which SHALL call Open_Meteo live and return current depth, temperature, and 5-day forecast.
4. WHEN the user requests a webcam image for a resort, THE Chat_Agent SHALL call `fetch_webcam` and return the image inline from the cached snapshot, falling back to a live Playwright fetch if no cached image is available.
5. WHEN the user requests flight options for specific dates, THE Chat_Agent SHALL call `search_flights_readonly` using Amadeus and return the top 5 flight options with carrier, times, and price; no booking SHALL be initiated.
6. WHEN the user requests accommodation options, THE Chat_Agent SHALL call `search_hotels_readonly` and return the top 5 hotel options with name, address, and price per night; no booking SHALL be initiated and THE Chat_Agent SHALL actively prevent any language that could confirm or imply that a booking has been made.
7. WHEN the user expresses a booking intent phrase such as "let's go with X", "book X", "I'll take X", "go ahead with X", or "confirm X" combined with a specific destination and date selection, THE Chat_Agent SHALL call `create_plan_artifact`.
8. THE Chat_Agent SHALL never suggest specific prices without a real-time API call confirming them in the current session.
9. THE Chat_Agent SHALL actively prevent any language that could confirm or imply that a booking has been made.
10. THE Chat_Agent SHALL never ask the user to type payment details into the chat interface.

---

### Requirement 14: Plan Artifact Creation and Dispatch

**User Story:** As the user, I want a concrete, structured trip plan produced at the end of a chat session that captures all the details needed to proceed to booking, so that I can review the full plan before committing to any purchase.

#### Acceptance Criteria

1. WHEN `create_plan_artifact` is called, THE Chat_Agent SHALL write a `trip_plans` record to the database with status `draft` containing the resort ID, outbound date, return date, flight option, hotel option, total estimated GBP, and any session notes.
2. WHEN `create_plan_artifact` is called, THE System SHALL render a Plan Card in the chat UI within 2 seconds displaying the full trip summary including destination, dates, flight details, hotel details, and grand total; WHEN Plan Card rendering fails after `create_plan_artifact` executes successfully, THE System SHALL automatically retry rendering up to 3 attempts before showing an error to the user.
3. WHEN a Plan_Artifact is created and the `trip_plans` database write has succeeded, THE Delivery_System SHALL dispatch the plan (not a booking confirmation) to the user via both email and Telegram; IF the `trip_plans` write has not succeeded, THE Delivery_System SHALL not dispatch any notification; IF the overall plan dispatch process fails, THE Delivery_System SHALL retry the entire dispatch up to 3 times, even if individual notifications were already sent.
4. THE Plan Card SHALL include a "Confirm & Book" button that links to the Booking_Service; this button SHALL be the only path from the plan to initiating a booking.
5. THE `create_plan_artifact` tool SHALL be callable at most once per chat session.
6. IF `create_plan_artifact` is called without a valid `flight_option` or without a valid `hotel_option`, THEN THE Chat_Agent SHALL return an error and SHALL NOT write a `trip_plans` record.

---

### Requirement 15: Booking Engine — Sequential Booking Flow

**User Story:** As the user, I want the booking engine to execute the flight, hotel, and payment steps in a strict sequence with full rollback on any failure, so that I am never left with a partial booking or an unexpected charge.

#### Acceptance Criteria

1. WHEN the user presses "Book Now — £[total]" in the Booking_Service UI, THE Booking_Service SHALL execute booking steps strictly sequentially in this order: load and validate plan artifact, display cost breakdown, write `consent_log`, hold flight via Duffel, hold accommodation via hotel API, authorise charge via Stripe, confirm flight via Duffel, confirm accommodation via hotel API, update `bookings` table, update `trip_plans` status to `booked`, and dispatch confirmation.
2. THE Booking_Service SHALL never execute booking steps in parallel.
3. WHEN the Duffel flight hold call fails, THE Booking_Service SHALL abort the sequence, log the full error, notify the user via Telegram and email, and make no hotel API or Stripe calls.
4. WHEN the hotel hold call fails, THE Booking_Service SHALL release the Duffel flight hold, abort the sequence, log the full error, and notify the user.
5. WHEN the Stripe authorisation fails, THE Booking_Service SHALL release both the Duffel flight hold and the hotel hold, abort the sequence, log the full error, and notify the user.
6. WHEN the Duffel flight confirmation call fails, THE Booking_Service SHALL refund the Stripe authorisation, release the hotel hold, abort the sequence, log the full error, and notify the user.
7. WHEN the hotel confirmation call fails, THE Booking_Service SHALL attempt a Duffel cancellation and a Stripe refund, log the full error context, notify the user with manual follow-up instructions, and explicitly abort the booking sequence.
8. THE Booking_Service SHALL complete the full booking sequence within 60 seconds from the "Book Now" button press.
9. IF the plan artifact was created more than 10 minutes ago, THEN THE Booking_Service SHALL reject the confirm request with a 409 status, re-fetch current prices, re-display the updated cost breakdown, and require fresh user confirmation.

---

### Requirement 16: Booking Engine — Pre-conditions and Isolation

**User Story:** As the system operator, I want the booking service to enforce strict pre-conditions before executing any booking, so that partial or invalid booking attempts are prevented entirely.

#### Acceptance Criteria

1. BEFORE beginning the booking sequence, THE Booking_Service SHALL verify that a valid `trip_plan_id` exists in the database with status `draft`.
2. BEFORE beginning the booking sequence, THE Booking_Service SHALL verify that the user is authenticated with a valid session.
3. BEFORE beginning the booking sequence, THE Booking_Service SHALL verify that `user_prefs.stripe_payment_method` is set.
4. BEFORE beginning the booking sequence, THE Booking_Service SHALL verify that all plan data including flight reference, hotel reference, and travel dates is present and not expired.
5. THE Booking_Service SHALL run in a separate Docker container from the Main FastAPI service with no shared environment variables.
6. WHEN a booking attempt is made from the chat service network namespace to any `/booking/*` endpoint, THE System SHALL refuse the connection.

---

### Requirement 17: Weekly Delivery — Telegram and Email

**User Story:** As the user, I want to receive a formatted weekly snow report on my phone and in my email every Friday, so that I get the snow intelligence briefing without needing to open the web app.

#### Acceptance Criteria

1. WHEN the Weekly_Run_Engine completes successfully, THE Delivery_System SHALL send a Telegram message to the user's registered `telegram_chat_id` by 18:15 UTC on Friday listing the top 5 resorts in ranked order with depth, score, reliability percentage, and a one-line qualitative summary for each.
2. WHEN the Telegram message has been successfully dispatched, THE Delivery_System SHALL send the SendGrid HTML email report within 5 minutes of that Telegram dispatch; IF the Telegram dispatch fails, THE Delivery_System SHALL not send the email.
3. THE HTML email SHALL include the same top 5 resort list as the Telegram message, expanded to include webcam thumbnails (Phase 6 onwards), a Snow_Score breakdown table per resort, and a "Plan a trip" CTA button linking to the web app.
4. THE Delivery_System SHALL use a single delivery trigger event for both Telegram and email to ensure consistency between the two outputs.
5. WHEN the Weekly_Run_Engine experiences a partial failure, THE Delivery_System SHALL send a failure notification within 2 minutes listing each failed component, the reason, and whether stale data was substituted.
6. THE Delivery_System SHALL achieve a Telegram delivery success rate of at least 99% across Friday report dispatches.

---

### Requirement 18: React Web Dashboard

**User Story:** As the user, I want a web dashboard that shows me the current week's snow scores and lets me configure my preferences, so that I can assess conditions at a glance and move into the chat to plan a trip.

#### Acceptance Criteria

1. THE Dashboard SHALL display snow score cards for the top 10 resorts from the latest weekly run, each showing resort name, country, snow depth, Snow_Score out of 100, historical reliability percentage, and mode tag.
2. THE Dashboard SHALL display a mode toggle that allows the user to switch between `sports` and `tourism` modes; WHEN the mode is changed, THE System SHALL persist the new mode to `user_prefs` and re-render all score cards and filters.
3. THE Dashboard SHALL provide a budget input field that allows the user to set a total GBP budget envelope; WHEN the budget is saved, THE System SHALL persist it to `user_prefs`; IF the `user_prefs` database persistence fails, THE System SHALL roll back the Dashboard budget display to the previous value and show an error message to the user.
4. THE Dashboard SHALL display a webcam thumbnail for each resort in the top 10 list (Phase 6 onwards), sourced from the latest `webcam_snapshots` record.
5. THE Dashboard SHALL display a "last updated" badge showing the timestamp of the most recent weekly run.
6. THE Dashboard page SHALL load successfully and complete within 2 seconds when reading from the database cache; THE Dashboard SHALL make no live external API calls during page load.
7. THE Dashboard SHALL embed the Chat_Agent panel within the same web interface, allowing the user to begin a chat session directly from the dashboard.

---

### Requirement 19: Telegram Bot Commands

**User Story:** As the user, I want to interact with the system via Telegram commands, so that I can check snow reports and manage preferences from my phone without opening a browser.

#### Acceptance Criteria

1. WHEN the user sends `/mode sports` or `/mode tourism` to the Telegram bot, THE System SHALL update `user_prefs.mode` to the specified value and confirm the change.
2. WHEN the user sends `/budget £X` to the Telegram bot, THE System SHALL update `user_prefs.budget_total_gbp` to the specified value and confirm the change; IF the Telegram bot cannot send the confirmation message, THE System SHALL roll back the budget change.
3. WHEN the user sends `/report` to the Telegram bot, THE Delivery_System SHALL respond with the latest weekly snow report in the standard Telegram report format.
4. WHEN the user sends `/plan` to the Telegram bot and a current draft plan exists, THE System SHALL respond with the latest `trip_plans` record summary.
5. WHEN the user sends `/start` to the Telegram bot, THE System SHALL register the user and link the Telegram chat ID to `user_prefs`.
6. WHERE Phase 8 is enabled, THE System SHALL support `/book <plan_id>` and `/confirm <plan_id>` commands, requiring `/book` to be sent in the same 30-minute session window before `/confirm` is accepted.

---

### Requirement 20: Data Persistence and Schema

**User Story:** As the system operator, I want a well-structured relational database schema with vector search capabilities, so that all snow intelligence data, user preferences, bookings, and semantic memory are stored reliably with appropriate constraints.

#### Acceptance Criteria

1. THE System SHALL maintain the following PostgreSQL tables: `resorts`, `weekly_runs`, `snow_scores`, `qualitative_signals`, `webcam_snapshots`, `trip_plans`, `bookings`, `consent_log`, and `user_prefs` with the column definitions, constraints, and foreign keys specified in the product specification.
2. THE System SHALL maintain the following pgvector tables within PostgreSQL: `preference_embeddings`, `trip_feedback_embeddings`, `resort_knowledge_embeddings`, and `session_summary_embeddings`, each with a `vector(1536)` column and an IVFFlat cosine similarity index.
3. THE `consent_log` table SHALL be append-only; THE System SHALL permit no DELETE or UPDATE operations on `consent_log` records.
4. THE System SHALL maintain Redis keys per the schema defined in the product specification with TTLs enforced: 24 hours for session message history, 8 days for the weekly run cache, and 90 seconds for API rate limit counters.
5. THE System SHALL seed the `resorts` table with at least 40 sports resorts and 30 tourism destinations with accurate latitude, longitude, altitude, mode tags, and nearest airport IATA codes.

---

### Requirement 21: Session Management and Redis

**User Story:** As the user, I want my chat session to be responsive and stateful within a session but automatically cleaned up after 24 hours, so that the system is fast and does not accumulate stale session data.

#### Acceptance Criteria

1. THE System SHALL store each chat session's message history in Redis under the key `session:{session_id}:messages` with a 24-hour TTL.
2. THE System SHALL store the retrieved pgvector context for a session in Redis under `session:{session_id}:context` with a 24-hour TTL.
3. WHEN a chat session ends via `POST /api/chat/session/{session_id}/end`, THE System SHALL wait for memory extraction to complete before clearing the Redis session keys; Redis session keys MAY also be cleared independently of session end requests, such as via TTL expiry.
4. THE Chat_Agent SHALL stream responses to the web UI via WebSocket with the first token delivered within 3 seconds of the user submitting a message.

---

### Requirement 22: User Preferences Management

**User Story:** As the user, I want to configure my mode, budget, preferred airports, excluded airlines, and contact details in one place, so that the system personalises all recommendations and delivery without me repeating this information.

#### Acceptance Criteria

1. THE System SHALL persist user preferences in the `user_prefs` table including: mode, budget in GBP, preferred departure airports as IATA codes, excluded airline codes, Stripe PaymentMethod token, Telegram chat ID, and email address.
2. WHEN the user updates preferences via `PATCH /api/user/prefs`, THE System SHALL apply the changes immediately to subsequent Chat_Agent sessions and weekly run configurations.
3. THE System SHALL store the Stripe PaymentMethod token encrypted at rest and SHALL never store raw card numbers on application servers.
4. WHEN a `user_prefs` record contains no `preferred_airports` entries at all, THE System SHALL default `preferred_airports` to `{LHR, LGW, STN, LTN, LCY}`.

---

### Requirement 23: API Design and Authentication

**User Story:** As the system operator, I want all API endpoints to require authentication and follow the route structure defined in the specification, so that the system is secure and the two services remain cleanly separated.

#### Acceptance Criteria

1. THE System SHALL implement the Main FastAPI service endpoints: `GET /api/dashboard/weekly`, `GET /api/dashboard/resorts`, `POST /api/dashboard/refresh` (admin only), `GET /api/user/prefs`, `PATCH /api/user/prefs`, `WS /api/chat/session`, `POST /api/chat/session/{session_id}/end`, `GET /api/plans`, `GET /api/plans/{plan_id}`, and `DELETE /api/plans/{plan_id}`.
2. THE System SHALL implement the Booking_Service endpoints: `GET /booking/plans/{plan_id}/summary`, `POST /booking/plans/{plan_id}/confirm`, `GET /booking/{booking_id}/status`, and `GET /booking/history`.
3. THE System SHALL require authentication on all endpoints in both services.
4. THE Booking_Service SHALL use its own separate authentication middleware independent of the Main FastAPI service.
5. THE System SHALL enforce HTTPS-only access in the production environment and SHALL automatically redirect HTTP requests to HTTPS.

---

### Requirement 24: Non-Functional Requirements — Performance

**User Story:** As the user, I want all system interactions to be fast and responsive within specified bounds, so that the system feels immediate and does not waste my time.

#### Acceptance Criteria

1. THE Weekly_Run_Engine SHALL complete the full end-to-end run within 30 minutes.
2. THE Dashboard page SHALL load within 2 seconds when data is read from the database cache.
3. THE Chat_Agent SHALL deliver the first response token via WebSocket within 3 seconds of message submission.
4. THE Webcam_Classifier SHALL capture each webcam screenshot within 5 seconds.
5. THE Booking_Service SHALL complete the full booking sequence within 60 seconds of the "Book Now" button press.
6. THE Memory_System pgvector retrieval queries SHALL complete within 500ms.

---

### Requirement 25: Non-Functional Requirements — Reliability

**User Story:** As the user, I want the system to operate reliably with defined success rates, so that I can depend on receiving the weekly brief and completing bookings without manual intervention.

#### Acceptance Criteria

1. THE Weekly_Run_Engine SHALL achieve a complete-without-errors success rate of at least 95% across weekly runs.
2. THE Booking_Service SHALL achieve a booking completion rate of at least 95% once a Stripe payment has been authorised.
3. THE Delivery_System SHALL achieve a Telegram delivery success rate of at least 99% for Friday reports.
4. THE System SHALL surface individual component failures to users when those failures impact the overall reliability thresholds defined in this requirement; THE System SHALL never silently handle a partial failure that breaches a defined reliability threshold.

---

### Requirement 26: Non-Functional Requirements — Security

**User Story:** As the system operator, I want the system to follow security best practices for secrets management, data isolation, and payment handling, so that user data and financial credentials are protected.

#### Acceptance Criteria

1. THE System SHALL store all API keys in environment variables for development and in AWS Secrets Manager for production (Phase 7 onwards).
2. THE Booking_Service and Main FastAPI service SHALL be isolated at the network level in separate Docker containers with no shared environment variables; THE System SHALL also store the Stripe PaymentMethod token encrypted at rest; BOTH conditions SHALL be satisfied together for security compliance.
3. THE System SHALL store the Stripe PaymentMethod token encrypted at rest and SHALL never store raw card numbers on any application server.
4. THE `consent_log` table SHALL permit append operations only with no DELETE or UPDATE operations permitted.

---

### Requirement 27: Non-Functional Requirements — Data Retention

**User Story:** As the system operator, I want defined data retention policies enforced in the system, so that operational data is available for trend analysis, financial records are kept for compliance, and ephemeral data is cleaned up automatically.

#### Acceptance Criteria

1. THE System SHALL retain `snow_scores` and `weekly_runs` records for 3 years.
2. THE System SHALL retain `webcam_snapshots` records and images for 90 days.
3. THE System SHALL enforce a 24-hour TTL on all Redis session keys so that raw chat message history is never persisted beyond a session.
4. THE System SHALL retain `bookings` records for 7 years to meet financial compliance requirements; THE System SHALL retain `consent_log` records for 7 years; each retention policy SHALL be enforced independently.
5. THE System SHALL retain pgvector preference and feedback embeddings indefinitely to support long-term user memory.

---

### Requirement 28: Non-Functional Requirements — Cost Controls

**User Story:** As the system operator, I want OpenAI, Amadeus, and other metered API costs to be controlled within defined bounds, so that the system is economically viable to operate.

#### Acceptance Criteria

1. THE Weekly_Run_Engine SHALL use GPT-4o-mini for all qualitative signal summarisation tasks and SHALL use GPT-4o only for the Chat_Agent, webcam vision classification, and plan artifact generation.
2. THE Weekly_Run_Engine SHALL make at most 70 GPT-4o-mini summarisation calls per weekly run, one per resort.
3. THE Chat_Agent SHALL trigger `search_flights_readonly` Amadeus calls only in response to explicit user requests within a chat session; Amadeus calls from explicit user requests in active chat sessions are permitted even during an active Weekly_Run_Engine execution; THE Chat_Agent SHALL NOT trigger Amadeus calls during the automated weekly run outside of user-initiated chat sessions.
4. THE Memory_System SHALL perform a deduplication check before every pgvector preference embedding write and SHALL NOT write the embedding if a semantically equivalent preference already exists for that user; IF the deduplication check fails or is bypassed for any reason, THE Memory_System SHALL block the embedding write entirely.
5. THE System SHALL enforce Redis key TTLs as specified to prevent unbounded memory growth.

---

### Requirement 29: Phase 1 — Snow Intelligence Core

**User Story:** As the developer, I want the foundational snow intelligence pipeline built and validated in Phase 1, so that subsequent phases have a reliable data backbone to build upon.

#### Acceptance Criteria

1. THE System SHALL seed the `resorts` table with 40 sports resorts and 30 tourism destinations before Phase 1 is considered complete.
2. THE Weekly_Run_Engine SHALL integrate with Open_Meteo to fetch current snow depth, 5-day forecast, and 5-year historical archive for each resort.
3. THE Snow_Score_Engine SHALL produce scores for all passing resorts with gate exclusions logged with reasons.
4. THE APScheduler Friday 17:00 UTC trigger SHALL be operational and verified.
5. WHEN evaluated against manually verified conditions over 4 consecutive weekend runs, THE Snow_Score_Engine scores SHALL correlate with known actual conditions.

---

### Requirement 30: Phase 2 — Qualitative Signal Layer

**User Story:** As the developer, I want qualitative signals from Reddit, RSS feeds, and official resort reports incorporated into the Snow_Score in Phase 2, so that the score reflects real-world sentiment and on-the-ground reports.

#### Acceptance Criteria

1. THE Qualitative_Signal_Scorer SHALL be operational for Reddit (praw), SnowForecast.com RSS (feedparser), and official resort report scraping (Playwright + BeautifulSoup4) by the end of Phase 2.
2. THE `qualitative_signals` table SHALL be populated after each weekly run.
3. WHEN negative keywords such as `icy`, `slush`, `closed`, or `bare` are present in qualitative signals, THE Snow_Score_Engine SHALL produce a visibly lower qualitative component score for affected resorts.
4. THE `resort_knowledge_embeddings` pgvector table SHALL contain at least 1 embedding per resort after the first Phase 2 weekly run completes.

---

### Requirement 31: Phase 3 — Data Persistence Layer

**User Story:** As the developer, I want the full database schema, pgvector, and Redis infrastructure operational in Phase 3, so that all subsequent phases have a reliable data layer.

#### Acceptance Criteria

1. THE System SHALL create all PostgreSQL tables, pgvector tables, and Redis key patterns as specified in Requirement 20 by the end of Phase 3.
2. THE System SHALL enforce database access rules in the ORM layer so that no cross-service table writes are possible; THE System SHALL additionally enforce database-level constraints or connection restrictions to prevent cross-service writes independently of ORM-layer rules.
3. THE System SHALL include an Alembic migration baseline covering all tables.
4. THE Docker Compose configuration SHALL start the Main FastAPI service, PostgreSQL, and Redis as separate services.

---

### Requirement 32: Phase 4 — Web Dashboard and Delivery Channels

**User Story:** As the developer, I want the web dashboard and delivery channels operational in Phase 4, so that the user can view snow scores and receive weekly reports without using the CLI.

#### Acceptance Criteria

1. THE Dashboard SHALL be operational with score cards, mode toggle, budget input, and a last-updated badge by the end of Phase 4.
2. THE Delivery_System SHALL send the Friday Telegram brief and SendGrid HTML email by the end of Phase 4.
3. THE Telegram bot SHALL support at minimum the `/help` and `/start` commands by the end of Phase 4; THE Telegram bot SHALL also support all other commands from Requirement 19 except `/book` and `/confirm` by the end of Phase 4.
4. THE failure notification format SHALL be implemented and tested.

---

### Requirement 33: Phase 5 — Chat Interface and Plan Artifact

**User Story:** As the developer, I want the full memory-augmented chat agent with all 7 tools and plan artifact creation operational in Phase 5, so that the user can have a complete conversational trip planning experience.

#### Acceptance Criteria

1. THE Chat_Agent SHALL be operational with all 7 tools from Requirement 13 by the end of Phase 5.
2. THE Memory_System retrieval and write workflows SHALL be operational by the end of Phase 5.
3. THE `create_plan_artifact` tool SHALL trigger Plan Card rendering in the UI within 2 seconds.
4. WHEN a preference is expressed in session 1, THE Memory_System SHALL surface that preference as context in session 2 for the same user.

---

### Requirement 34: Phase 6 — Webcam Visual Evidence

**User Story:** As the developer, I want webcam screenshots captured, classified, and displayed in Phase 6, so that the user has visual confirmation of conditions for each top resort.

#### Acceptance Criteria

1. THE Webcam_Classifier SHALL be operational and integrated into the weekly run pipeline by the end of Phase 6.
2. THE Dashboard SHALL display webcam thumbnails sourced from `webcam_snapshots` for each top 10 resort by the end of Phase 6.
3. THE `fetch_webcam` Chat_Agent tool SHALL return real classified images by the end of Phase 6.
4. THE `webcam_url` column SHALL be populated for all 70 resorts in the `resorts` table by the end of Phase 6.

---

### Requirement 35: Phase 7 — Booking Engine

**User Story:** As the developer, I want the isolated booking engine with full Duffel, hotel, and Stripe integration operational in Phase 7, so that the user can complete end-to-end trip bookings with full financial safeguards.

#### Acceptance Criteria

1. THE Booking_Service SHALL be deployed as a separate Docker container with isolated credentials by the end of Phase 7.
2. THE full booking sequence from Requirement 15 SHALL be implemented and tested end-to-end including all failure and rollback scenarios.
3. THE Stripe Payment Element card setup flow SHALL be integrated into the web UI.
4. THE `consent_log` entry SHALL demonstrably exist in the database before the first mock external API call in all booking sequence tests.
5. WHEN a booking is attempted via a chat message, THE System SHALL not trigger any booking API call or consent log entry.

---

### Requirement 36: Phase 8 — Telegram Booking Trigger (Optional)

**User Story:** As the developer, I want Telegram-initiated booking to be optionally available in Phase 8 after Phase 7 is stable, so that the user can confirm bookings from their phone without opening a browser.

#### Acceptance Criteria

1. WHERE Phase 8 is enabled and Phase 7 has processed at least 4 real bookings, THE System SHALL support `/book <plan_id>` and `/confirm <plan_id>` Telegram commands.
2. THE `/book` command SHALL display the full cost summary; THE `/confirm` command SHALL trigger the Booking_Service booking sequence.
3. THE `/confirm` command SHALL be rejected if `/book` has not been sent for the same plan within the preceding 30-minute session window; WHEN `/confirm` is sent without a preceding `/book` in the same 30-minute session, THE Telegram bot SHALL respond with a specific error message explaining that `/book <plan_id>` must be sent first within a 30-minute session window before `/confirm` can be accepted.
4. THE Booking_Service booking logic SHALL be identical for Telegram-initiated and web-initiated bookings; no separate booking code path SHALL be introduced.
5. THE `consent_log.channel` SHALL be recorded as `telegram` for Telegram-initiated bookings.
