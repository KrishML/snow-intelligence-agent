# Snow Agent: Action Plan to Fully Functional and Foolproof

Prepared 20 November 2026 from PROJECT\_OVERVIEW.md. Effort is given in weekend-sized chunks (W = roughly 6 to 8 focused hours). All estimates are rough.

## 1. Goal and definition of done

**Fully functional** means: (1) every Friday you receive a top 5 that you have validated against real conditions; (2) chat produces a plan with real, bookable flight and hotel prices; (3) a booking completes end to end, first in sandbox, then as one real low-value booking; (4) the system runs unattended on real infrastructure.

**Foolproof** means: no silent failures, every external dependency has a fallback or a loud alert, money can only move through the consent path, and every failure mode has a test, a runbook entry or a kill switch.

## 2. Findings that drive this plan

| ID | Finding | Severity | Fixed in |
| --- | --- | --- | --- |
| F1 | Amadeus Self-Service was shut down in July 2026; chat flight and hotel search depend on it | Blocker | M3 |
| F2 | Forecast score is 0-30 (spec says 25), so components sum to 105 and the cap hides it | High | M1 |
| F3 | Sports open-run gate is not enforced; no lift or run data is collected | High | M1 |
| F4 | Nothing has run on real PostgreSQL; migrations 0003-0006 only rendered as SQL; tests use SQLite | High | M0 |
| F5 | Depth and temperature may be read at the wrong elevation; modelled depth is unvalidated against reality | High | M1 |
| F6 | Chat needs a flight search credential, but the firewall forbids Duffel keys in the chat environment | High | M3 |
| F7 | Duffel may not cover the low-cost carriers that dominate London weekend routes; holds only work on some offers | High | M3 |
| F8 | No hotel booking provider, no payment UI, no booking UI | Blocker for booking | M3, M4 |
| F9 | Reddit, RSS, official reports, webcams and vision are only tested with mocks; 0 of 70 webcam URLs | Medium | M2 |
| F10 | trip\_feedback\_embeddings is never written | Medium | M5 |
| F11 | Static bearer tokens, no real login, no CORS or production origin | Medium | M6, M7 |
| F12 | No deployment, monitoring or missed-run alarm | Medium | M7 |

## 3. Execution order

Critical path: **M0 -> M1 (starts the 4-week validation clock) -> M3 -> M4**. M2, M5 and M6 run alongside. M7 starts once M4 works in sandbox.

| When | Focus |
| --- | --- |
| W1 (this weekend) | M0: real infrastructure baseline |
| W2-W5 | M1 validation runs each weekend; M3 spike and provider rework; M2 sources |
| W5-W9 | M4 booking completion; M5 chat quality; M6 product gaps |
| W8-W12 | M7 production readiness; real-money pilot booking |

The season runs to March, so the first real booking should land around January if this pace holds.

## M0. Real infrastructure baseline (1 W)

| ID | Task | Done when |
| --- | --- | --- |
| T0.1 | Fill .env and .env.booking; docker compose up; confirm migrate-on-start applies 0001-0006 on real PostgreSQL | /ready is green on both services |
| T0.2 | Fix anything migrations 0003-0006 break; add a CI job running them on a real pgvector pg16 service | migrate up and down clean in CI |
| T0.3 | Add PostgreSQL-backed tests for what SQLite cannot prove: consent\_log append-only trigger, booking state-machine trigger, partial unique index (one non-failed attempt per plan), pgvector similarity queries, ivfflat index creation | all pass in CI |
| T0.4 | Write scripts/smoke\_live.py that calls each real client once (Open-Meteo, OpenAI chat and embeddings, Telegram, SendGrid) | prints pass or fail per client |
| T0.5 | Seed resorts, trigger a manual run, read error\_log line by line | one complete run on PostgreSQL; Telegram top 5 received |

**Exit gate:** a real run, real delivery, CI green.

## M1. Score correctness and validation (starts W2, runs 4+ weekends)

| ID | Task | Done when |
| --- | --- | --- |
| T1.1 | Set forecast maximum to 25, rebalance brackets, stop relying on the 100 cap | hypothesis property test: best inputs give exactly 100 and nothing exceeds 100 uncapped |
| T1.2 | Query Open-Meteo at a per-resort elevation (mid-mountain for sports, village for tourism); store elevation\_used\_m on each score | base versus mid values differ sensibly for a few test resorts |
| T1.3 | Make the open-run gate tri-state: open, closed, unknown. Closed excludes; unknown passes but is flagged and loses points | no resort silently passes without a status |
| T1.4 | Add season\_open and season\_close to resorts as a cheap proxy for lifts not yet running | out-of-season resorts excluded with a stated reason |
| T1.5 | Review the 3°C rule: use elevation-adjusted temperature, make thresholds configurable, log near misses | excluded-by-temperature list reviewed by hand once |
| T1.6 | Move thresholds and weights into a versioned scoring config; store score\_version on each snow\_scores row | old runs stay interpretable |
| T1.7 | Confirm historical reliability weights recent winters more heavily, as the spec says | unit test on a fabricated five-year series |
| T1.8 | Build the validation harness: a validation\_log table plus scripts/validate\_week.py. Each Monday record per top-10 resort what actually happened (resort-reported depth, lifts open, your webcam judgement or trip experience) | one row per resort per week |
| T1.9 | Track metrics: depth within 15 cm of reported in at least 70 percent of cases; zero no-snow resorts in any top 5 (false positives matter most); top-5 overlap with your manual best 5 | metrics script prints a weekly table |

**Exit gate:** four consecutive weekends meeting the targets, thresholds tuned, every change recorded under a new score\_version. Do not ship booking features to real money until this passes.

## M2. Evidence sources made real (W3-W5, parallel)

| ID | Task | Done when |
| --- | --- | --- |
| T2.1 | Confirm you can obtain Reddit read credentials; if not, drop Reddit and rely on RSS and official reports (the neutral fallback exists) | decision D5 logged |
| T2.2 | Record real RSS, Reddit and official-report responses as fixtures; write parser contract tests against them | tests fail when a format changes |
| T2.3 | Add a source canary: if a source yields zero usable items for many resorts in one run, send a partial-failure alert naming the source | alert fires in a forced test |
| T2.4 | Official report sources for the top 25 sports resorts first, with a per-resort adapter where the page is structured | data/report\_sources.json covers 25 resorts |
| T2.5 | Choose 10 to 15 tourism RSS feeds and set TOURISM\_RSS\_FEEDS | every tourism region has at least one feed |
| T2.6 | Webcam URLs for the top 20 resorts only; check each loads headless | data/webcams.json has 20 entries, all fetch |
| T2.7 | Label about 60 webcam images and run scripts/eval\_webcam.py | agreement is at least 85 percent, or the prompt is revised |
| T2.8 | Treat all scraped text as untrusted: structured output for the summariser, cap input length, never pass model output into tool arguments, test with injection strings inside a fake Reddit post | injection test passes |
| T2.9 | Restrict webcam and report fetching to allowlisted https hosts and block private IP ranges (SSRF) | localhost and metadata IPs blocked in tests |
| T2.10 | Show per-source health (success rate per run) on the dashboard | visible panel or banner |

**Exit gate:** at least 80 percent of passing resorts have one real non-neutral signal, and webcam accuracy meets the target.

## M3. Travel providers rework (W2-W4, critical path for booking)

| ID | Task | Done when |
| --- | --- | --- |
| T3.1 | One-day spike: with a Duffel test token, price London to your top destination airports (for example KRK, GVA, INN, SZG, TRN). Note which airlines return, whether low-cost carriers appear, and which offers support hold | written coverage matrix |
| T3.2 | Decide the flights model from the matrix. Mode A: bookable in the app. Mode B: assisted, where the plan carries a deep link and you book on the airline site and record the reference | decision D1 logged |
| T3.3 | Spike Duffel Stays or another bookable hotel API for your destinations; if unavailable or thin, hotels use assisted mode with deep links | decision D2 logged |
| T3.4 | Resolve the firewall conflict. Preferred: a separate Duffel organisation with no payment method, used only for search, so a leaked key cannot book. Stricter: a small search-only proxy holding the credential and exposing offer search endpoints only | decision D3 logged; isolation tests updated |
| T3.5 | Replace search\_flights\_readonly with Duffel offer requests; store offer id, expiry and price in the plan; keep GBP-only and excluded-airline filters | tool returns real offers |
| T3.6 | Delete the Amadeus client, env vars and docs; update isolation.py startup guards and tests | a grep finds no Amadeus references |
| T3.7 | Handle expiry: offers expire, so re-price at booking time and show any change before consent | test with an expired offer |
| T3.8 | Add the score-before-search re-check at booking time: refetch live snow for the resort and warn if it now fails the gate | test with a degraded resort |

**Exit gate:** a chat session produces a plan whose flight (and hotel, in Mode A) can be priced in the sandbox.

## M4. Booking completion (W5-W9)

| ID | Task | Done when |
| --- | --- | --- |
| T4.1 | Stripe: create customer, SetupIntent, Payment Element UI; store only the PaymentMethod id | a test card saves |
| T4.2 | Handle strong customer authentication: for UK and EU cards do the authorisation on-session with 3D Secure in the UI when the button is pressed, not off-session afterwards | 3DS test card completes |
| T4.3 | Adapt the sequence to provider reality: where an offer cannot be held, authorise the card (manual capture), create the order, capture on success, void on failure; keep hold-first only where supported | state machine handles both paths, tested |
| T4.4 | Booking UI: plan summary with itemised cost and price-lock countdown, a Book Now button showing the total, live status, booking history | Playwright test end to end against fakes |
| T4.5 | Replace the static bearer in the browser with a session login that the booking service verifies; no long-lived secret in frontend code | token absent from the bundle |
| T4.6 | Idempotency keys on every provider call and on the confirm endpoint so double-clicks and retries cannot double-book | duplicate-submit test |
| T4.7 | Reconciliation job: every few minutes scan bookings stuck in a non-terminal state, retry only safe releases, mark failed\_manual\_review and alert | simulated crash mid-sequence is recovered or flagged |
| T4.8 | Webhooks: handle Stripe and Duffel async events (payment status, order changes, airline schedule changes) | signed-webhook tests |
| T4.9 | Failure-injection suite: fail or time out each step in turn; assert state, rollback order and notifications | every step covered |
| T4.10 | Spend controls: per-booking cap, daily cap, minimum days to departure, and a BOOKING\_ENABLED kill switch | each tested |
| T4.11 | Hotel booking per D2, or the assisted flow where you confirm an external booking and attach the reference | plan shows bookable or assisted, with reasons |
| T4.12 | Sandbox rehearsal with Duffel and Stripe test mode, including declined card, 3DS failure, expired price lock and cancelled offer | checklist signed off |
| T4.13 | Real-money pilot: one low-value booking you would take or can refund; rehearse the cancel and refund path first | confirmation received, refund drill done |

**Exit gate:** sandbox rehearsal complete and one real booking succeeded. Keep Telegram booking disabled until four web bookings.

## M5. Chat, memory and agent quality (parallel, W4-W8)

| ID | Task | Done when |
| --- | --- | --- |
| T5.1 | Trip feedback capture: a Telegram prompt after the return date plus a short web form; write to trip\_feedback\_embeddings | retrieval returns it |
| T5.2 | Refresh memory retrieval when a new resort or topic is mentioned, not only on the first message | test with a topic shift |
| T5.3 | Retrieval evaluation: 20 queries with expected chunks; record the hit rate | baseline recorded |
| T5.4 | Agent golden tests: 30 scripted conversations with a real model on a small budget (filter then plan, invalid resort, stale cache, tool failure, over budget) | assertions on tool calls pass |
| T5.5 | Adversarial tests: asking it to book, to reveal keys, to ignore rules, plus injection hidden in tool results | no booking path, no leak |
| T5.6 | Cost guards: per-session token cap, daily OpenAI spend cap with alert, rate limits on chat endpoints | tested |
| T5.7 | WebSocket resilience: reconnect and resume a session from Redis history | forced-disconnect test |

## M6. Product completeness (W5-W9, parallel)

- Telegram link screen using the existing link-code API
- Plan history page and booking pages (from M4)
- Proper empty, loading and error states everywhere
- Real login for the single user, with logout
- Production origin: choose a reverse proxy or CORS rules
- Accessibility pass and mobile layout check

## M7. Production readiness (W8-W12)

| ID | Task | Done when |
| --- | --- | --- |
| T7.1 | Hosting decision D4. For one user, a single VPS or Railway or Fly style host running Docker Compose with automatic TLS is enough; move to ECS only if you need it | decision logged |
| T7.2 | Managed or backed-up PostgreSQL with point-in-time recovery; run a restore drill | restore tested |
| T7.3 | Secrets in the platform secret store, not in repo or host files; separate secrets for the booking service; rotation checklist | isolation checks pass in production |
| T7.4 | CI pipeline: lint, type check, backend tests with PostgreSQL, frontend tests, Playwright, dependency and container scanning, secret scanning pre-commit | required to merge |
| T7.5 | Dead-man switch for the Friday run: ping a heartbeat monitor on completion and alert if nothing arrives by 18:30 UTC | fires in a forced miss |
| T7.6 | Error tracking and metrics: run duration, source success rates, OpenAI spend, booking outcomes | dashboard exists |
| T7.7 | Uptime check on /ready for both services | alert tested |
| T7.8 | Runbook, one page each: run failed, source down, booking stuck, key leaked, provider down, how to flip each kill switch | written |
| T7.9 | Security pass: rate limiting, security headers, CORS, log redaction for tokens and payment ids, Playwright sandbox flags, pinned dependencies | checklist done |
| T7.10 | Retention jobs match the spec (snapshots 90 days, sessions 24 hours, booking and consent records 7 years) and add a delete-my-data path | jobs run |

## 4. Decisions you need to make

| ID | Decision | Needed by |
| --- | --- | --- |
| D1 | Flights: bookable in the app (Mode A) or assisted with deep links (Mode B), based on the Duffel coverage matrix | end of T3.1 |
| D2 | Hotels: bookable provider or assisted deep links | end of T3.3 |
| D3 | Chat search credential: separate unfunded Duffel organisation, or a search-only proxy | before T3.5 |
| D4 | Hosting target for production | before M7 |
| D5 | Keep or drop Reddit as a source | end of T2.1 |
| D6 | Telegram booking stays off until four successful web bookings | already decided |

## 5. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Duffel lacks the low-cost carriers you actually fly | Mode B assisted flights; the planning value of the app is unaffected |
| Reddit credentials unavailable | Drop Reddit; neutral fallback plus RSS and official reports |
| Score does not match reality | Do not build further on it; tune in M1 before any booking work reaches real money |
| Modelled snow depth is coarse for steep terrain | Elevation handling plus the weekly validation log; compare against resort-reported depth |
| A bug moves real money | Sandbox rehearsal, one low-value pilot, spend caps, idempotency keys, kill switch |
| Scrapers break silently when pages change | Fixture contract tests plus the source canary alert |
| Run silently does not happen on a Friday | Dead-man switch heartbeat alert |

## 6. Go-live checklist

- [ ] Four validated weekends meeting the M1 targets
- [ ] Score weights sum to 100 without relying on the cap
- [ ] Open-run gate tri-state in place and tested
- [ ] CI runs migrations and trigger tests on real PostgreSQL
- [ ] Amadeus fully removed; flights and hotels on the decided providers
- [ ] Firewall isolation tests pass with the final credential layout
- [ ] Booking sandbox rehearsal complete, including every failure case
- [ ] Idempotency, reconciliation and webhooks working
- [ ] Spend caps and BOOKING\_ENABLED kill switch tested
- [ ] Heartbeat alert for the Friday run firing in a drill
- [ ] Backup restore drill done
- [ ] Runbook written; secrets rotated and stored in a secret manager
- [ ] One low-value real booking completed and one refund drill done
