# FlatHunter V0 - Core Demo Alignment Implementation Plan

## 1. Purpose

This plan brings the current implementation into alignment with the authoritative FlatHunter V0 product, workflow, matching, and technical specifications.

The delivery target is a dependable **core demo**:

```text
Renter describes requirements
    -> confirms and starts one active search
    -> receives useful matches from approved inventory
    -> approves contact for a promising property
    -> FlatHunter simulates qualification using relevant questions
    -> owner reply updates the listing and re-runs matching
    -> renter confirms a compatible visit time
```

The project is still pre-stability, so the development Supabase database may be reset before the corrected initial schema is applied. Production migration compatibility is not a constraint for this plan.

## 2. Scope and Functional Outcomes

### In scope

- Durable renter search lifecycle: draft/confirmed requirement profile, ACTIVE, PAUSED, CLOSED, resume, edit, and close.
- Admin single-listing and bulk ingestion with source preservation, review, corrections, approval, contacts, and property media.
- Deterministic matching that preserves unknown values and generates explainable match results.
- Reliable background-job execution for search start, new/updated listing, and updated search events.
- Renter match cards and first-outreach approval.
- Simulated property-side qualification, reply parsing, re-matching, and visit scheduling.
- Offline automated tests and fixture-based LLM contract evaluation.

### Explicitly deferred

- Live email send/IMAP reply automation, WhatsApp, SMS, MTProto outreach, or browser automation.
- Web frontend/admin dashboard, listing scraping, route planning, calendar integrations, multi-city support, and multi-search support.
- Flexible-context LLM ranking beyond storing extracted context. It remains an optional later enhancement after deterministic matching is tested.

### User-visible result

An admin can add a Hyderabad listing with screenshots/text, correct its extracted facts, attach property photos, and approve it. A renter can start a search, receive only useful property cards, approve outreach, observe a simulated qualification exchange, and confirm a compatible visit. The bot never treats missing information as a negative fact or contacts a property without renter approval.

## 3. Current Misalignments Being Fixed

| Area | Current behavior | Corrected behavior |
|---|---|---|
| Search start | Creates an ACTIVE search directly from FSM data and has no durable idempotency key. | Persist a confirmed search snapshot; atomically activate once and enqueue one versioned matching job. |
| Core requirements | Move-in is optional and a missing max budget becomes `999999`. | Require listing type, location, max budget, and move-in date/window; never invent an unlimited budget. |
| Ingestion media | Every image is sent to the LLM; no property-media stage exists. | Information-bearing images go to extraction; property photos are stored as media and are not analyzed in V0. |
| Listing approval | Retains only a few fields and drops contacts/context/source data. | Persist canonical listing fields, `extracted_context`, source records, contacts, contact channels, and media. |
| Availability | Every approved listing becomes `AVAILABLE`. | Default to `UNKNOWN`; only explicit source/reply evidence may set `AVAILABLE`. |
| Bulk mode | Splits text with `---` and ignores the required image grouping rule. | Create one draft per information image/input group and provide per-draft review/approval. |
| Matching | Uses partial weights, omits mandatory maintenance, and stores plain-string gaps. | Apply the documented hard-gate, known-fact scoring, completeness, structured gap, explanation, and classification pipeline. |
| Job execution | Claims non-atomically, ignores `run_after`, and has no deduplication/versioning. | Atomically claim due jobs, retry safely, and use unique event keys/version snapshots. |
| Outreach | Generates a message marked as Telegram but never sends through a selected property channel. | Record renter approval, choose a stored supported/simulated channel, persist attempts/messages, and clearly label simulation. |
| Qualification | Updates listing facts but does not re-run matching or prioritize gaps. | Re-run the shared matcher after every parsed reply; close conflicts, ask the next relevant gap, or mark qualified. |
| Scheduling | Saves availability but does not compare it; confirmation writes a literal string. | Compare proposed slots to structured availability and persist the confirmed timestamp only after renter confirmation. |
| Testing | Scratch test calls the live database. | Add isolated unit, integration, Telegram-flow, and LLM-fixture tests under `tests/`. |

## 4. Technical Design

### 4.1 Data model and database reset

Update `migrations/001_initial_schema.sql` and reset the development database before applying it.

Add or normalize the following concepts:

- `search_sessions`: a persistent pre-activation requirement snapshot/reference, `version`, `started_at`, `last_activated_at`, `paused_at`, and `closed_at`.
- `search_requirements`: canonical requirement data, validated max/target rent, move-in window, requirement importance, flexible preferences, and normalized scoring weights.
- `listings`: all supported canonical fields, availability freshness, `extracted_context`, and a version increment on material changes.
- `listing_sources`, `listing_media`, `contacts`, and `contact_channels`: retain every approved listing's raw source and usable contact information.
- `listing_drafts`: canonical payload, extracted context, conflicts, extraction metadata, and explicit review status.
- `matches`: evaluation versions, structured hard failures, structured qualification gaps, positive reasons, and deterministic score breakdown.
- `agent_jobs`: `idempotency_key`, `run_after`, lock ownership/time, attempt count, error data, and terminal status.
- Notification/event deduplication storage or a unique idempotency key on notification jobs.

Use database constraints and unique indexes for one active renter search, one match per search/listing/version policy, and one event/job per idempotency key. Treat database constraints as the final guard against duplicate Telegram updates or worker retries.

### 4.2 Renter search lifecycle

Refactor requirement collection so the LLM only extracts structured data and natural-language follow-up text. Deterministic validation decides whether a requirement profile can start:

```text
Required: listing type, preferred/acceptable location, maximum budget, move-in date/window
Optional: furnishing, parking, bathroom, brokerage, deposit, flatmate and flexible preferences
```

Implementation approach:

1. Persist the confirmed profile before showing the start action; callback payload is `search:start:<search_id>`.
2. On callback, resolve Telegram user, verify ownership, validate core data, and atomically transition the referenced search to ACTIVE.
3. Increment `search.version` for a material requirement update and insert/reuse `MATCH_ACTIVE_SEARCH:<search_id>:<version>:SEARCH_STARTED`.
4. Acknowledge immediately in Telegram. The worker performs the inventory evaluation asynchronously.
5. Add `/mysearch`, `/pause`, `/resume`, `/editsearch`, and `/cancel_search` handlers with explicit state transition validation.
6. Requirement edits update the existing active search, increment its version, and queue a rematch. Paused searches do not receive new-listing notifications; resume queues a catch-up match job.

### 4.3 Admin ingestion and listing approval

Implement the documented two-stage single-listing flow:

```text
/addlisting
  -> collect text/information screenshots
  -> /doneinfo
  -> extract structured listing draft
  -> resolve important conflicts and admin corrections
  -> collect/skip property photos
  -> review draft
  -> approve, edit, or reject
```

Technical approach:

- Store Telegram `file_id` and `file_unique_id` for all uploads. Do not store base64 image bodies in `text_content`.
- Mark inputs as information-bearing or media. Only information-bearing images are passed to the multimodal LLM.
- Expand the extraction contract to include canonical listing fields, contacts with channel type/explicitness, `content_type`, raw confidence/conflicts, and flexible context.
- Reject demand-only (`RENTER_REQUIREMENT`) and unsupported/unknown content from inventory.
- Implement deterministic conflict detection for important canonical fields. Admin corrections have highest authority and write an explicit canonical patch.
- On approval, copy every supported canonical field to `listings`, create normalized source/contact/channel/media records, retain flexible context, and set availability to explicit source truth or `UNKNOWN`.
- Queue `LISTING_CREATED` only after the complete listing transaction succeeds.

For `/bulkadd`, each information screenshot is one draft by default. Text may be grouped only through explicit admin grouping. Review and approval remains per draft; multiple listings within one screenshot require a deliberate admin choice rather than silent inference.

### 4.4 Deterministic matching engine

Refactor `MatchingEngine` into pure, testable evaluation steps with no database writes during evaluation:

1. Check listing eligibility: approved, Hyderabad, supported listing type, and not explicitly unavailable.
2. Build renter-relevant factors from the requirement profile.
3. Apply hard constraints only on explicit contradictions: type, excluded/required locality, known monthly cost above maximum, late availability, required preference mismatch, and required financial cap breach.
4. Calculate known monthly cost as `rent + mandatory_maintenance` only when maintenance is explicitly known and mandatory. Unknown maintenance is a gap, not zero.
5. Score only known applicable factors using the documented default weights: location 30, budget 25, move-in 15, property 10, amenities 10, financial terms 10. Normalize by the known-factor weight total.
6. Calculate renter-specific information completeness independently using required facts and preferences; availability `UNKNOWN`/`STALE` is incomplete.
7. Generate structured qualification gaps containing field, importance, priority, and question intent. Priority order is availability, required unknowns, hard-boundary financial/date facts, then preferences.
8. Persist an explainable result: classification, score, completeness, hard failures, gap list, positive reasons, and score breakdown.

Classify a match as:

- `REJECTED` for any explicit hard conflict.
- `NEEDS_QUALIFICATION` when core evidence or important facts are unknown, including stale/unknown availability.
- `STRONG_MATCH` only for high fit, sufficient completeness, fresh confirmed availability, and no important unresolved gaps.
- `POSSIBLE_MATCH` for non-rejected listings below the strong threshold.

Run the same evaluator after search changes, listing corrections/updates, and every qualification reply. No qualification-specific scoring logic is allowed.

### 4.5 Jobs, match presentation, and notification policy

Use a simple database-backed worker, retaining the modular-monolith design:

- Claim one due `PENDING` job atomically. Lock it before processing and only mark it succeeded when the handler completes.
- Respect `run_after`; retry transient failures with bounded attempts and a retry time; persist terminal failures for admin visibility.
- Implement handlers for `MATCH_ACTIVE_SEARCH`, `LISTING_CREATED`, `LISTING_UPDATED`, `SEARCH_UPDATED`, `SEND_INITIAL_RESULTS`, and `SEND_RENTER_NOTIFICATION`.
- Use unique idempotency keys for matching runs and renter notifications so duplicate callbacks, duplicate listing approval, and worker retries do not send duplicate alerts.
- Initial matching persists all evaluations, ranks actionable results, sends one summary, and sends the best useful cards.
- Newly approved inventory notifies immediately for strong matches and high-fit qualification-needed matches. Lower-priority candidates are stored without noisy notifications.

Match cards must show deterministic explanations, price, locality, listing type, known positives, unresolved important facts, stored photos when available, and `Contact Them`/`Skip` actions.

### 4.6 Simulated outreach, qualification, and scheduling

The core demo does not send real property-side messages. It must still model the real workflow accurately.

- `Contact Them` verifies the renter owns the search and the match remains actionable. It creates/reuses one logical conversation, records `outreach_approved_at`, and chooses the best stored usable channel using the documented priority.
- Generate the initial question, persist an outbound `messages` row and an `outreach_attempts` row with a clearly simulated state. Never label this as a Telegram cold message.
- `/sim_reply <conversation_id> <text>` feeds a simulated property reply into the same inbound-processing path intended for future email adapters.
- Reply parsing extracts only explicit facts, updates canonical listing fields, records parsed facts, and re-runs the shared matcher.
- A hard conflict closes the conversation. Remaining gaps generate the next question in priority order. A sufficiently resolved listing transitions to `QUALIFIED` then `READY_FOR_SCHEDULING`.
- Renter availability is parsed to normalized timezone-aware windows. Proposed owner slots are parsed, checked deterministically against these windows, and shown to the renter only when compatible or with an explicit incompatibility/escalation message.
- Confirmation changes a visit from `AWAITING_RENTER_CONFIRMATION` to `CONFIRMED` and copies the actual proposed timestamp into `confirmed_start`. Decline cancels or starts rescheduling; it does not silently commit a time.

The existing email adapter remains behind an interface and is not used for initial outreach until the simulated workflow passes its acceptance tests.

### 4.7 Reliability, observability, and configuration

- Define a provider interface and validate `LLM_PROVIDER` against supported values at startup. Fail with a clear configuration error rather than silently choosing Gemini for an unknown value.
- Replace blocking `time.sleep` calls in async providers with `asyncio.sleep`.
- Repair startup logging issues and use structured application logs for worker/job transitions.
- Persist model-call metadata required for debugging state-changing LLM operations, while avoiding raw secret data.
- Keep LLM outputs Pydantic-validated before they mutate any database record.

## 5. Delivery Phases

### Phase 1 - Foundation and reset

1. Back up any sample data needed for testing, reset development Supabase, and apply the corrected initial migration.
2. Update Pydantic models, enums, repository helpers, configuration validation, and fixture factories to match the schema.
3. Introduce pure service boundaries for matching evaluation and explicit state-transition helpers.

**Outcome:** the application has a coherent data contract and can be tested without live Telegram or LLM calls.

### Phase 2 - Search and matching vertical slice

1. Build durable search confirmation/start callbacks and lifecycle commands.
2. Implement versioned jobs, atomic worker claiming, eligibility filtering, deterministic matching, match upserts, and notification deduplication.
3. Deliver initial search summary and explanation-based property cards.

**Outcome:** a renter can start one valid search and receive deterministic results from existing approved inventory.

### Phase 3 - Correct ingestion inventory

1. Implement source/media separation, draft extraction/review/correction, and full normalized approval.
2. Implement per-image bulk draft creation and per-draft approval.
3. Trigger re-evaluation only after a listing is fully approved or materially updated.

**Outcome:** every approved listing is usable inventory with preserved source evidence, correct canonical facts, contacts, and optional photos.

### Phase 4 - Qualification and visit demo

1. Implement renter-approved simulated outreach, contact-channel selection, conversation state transitions, and reply processing.
2. Re-match after every parsed fact and ask only the next relevant qualification gap.
3. Implement availability capture, proposed-time compatibility checks, renter confirmation, decline, and correct visit persistence.

**Outcome:** the complete core demo can reach a confirmed visit without live external property-side messaging.

### Phase 5 - Hardening and evaluation

1. Add offline test suites and representative JSON/text/image fixture metadata.
2. Run the manual Telegram demo script against a reset development database.
3. Fix discovered state/idempotency defects before considering live email automation.

**Outcome:** the demo is repeatable, explainable, and protected against duplicate updates and unknown-value regressions.

## 6. Test Strategy and Acceptance Gates

### Unit tests

- Hard constraints: wrong type, excluded locality, above-max cost, late move-in, unavailable listing, required true/false/unknown fields, brokerage/deposit caps.
- Scoring: preferred vs acceptable location, target vs max budget, maintenance treatment, unknown-factor normalization, preferred mismatch, and known positive preference scores.
- Completeness/gaps: availability freshness, required versus preferred weights, unrelated fields excluded, and priority ordering.
- State transitions: invalid transitions rejected; duplicate start, approval, notification, and job retry remain idempotent.

### Integration tests

- Approving a draft preserves sources, context, contacts/channels, and media; its availability remains unknown unless confirmed.
- Search start queues exactly one matching job and persists/upserts evaluations.
- Listing/search/reply updates re-evaluate the affected match.
- Conversation and visit records follow permitted transitions and retain correct timestamps.

### Telegram-flow tests

- Requirement follow-up requests only the missing core field.
- Start callback verifies ownership and cannot create a duplicate active run.
- Match card contact action requires a current actionable match.
- Visit confirmation is offered only after a compatible proposal and writes the proposed timestamp.

### LLM contract evaluation

- Maintain versioned fixtures for Hyderabad listing text/screenshots, renter requirements, admin corrections, property replies, and visit availability.
- Assert Pydantic-valid output, no fabricated canonical facts, correct `UNKNOWN` preservation, and expected canonical extraction for high-severity fields.
- Run evaluation manually or in CI with a provider only when credentials are intentionally supplied; all normal tests remain offline.

### Core-demo completion criteria

1. Reset database setup and test bootstrap are documented and reproducible.
2. One renter starts, pauses, resumes, edits, and closes exactly one active search correctly.
3. Admin approval produces a complete, matchable listing with a usable simulated contact path.
4. Every match outcome is explainable from persisted deterministic facts.
5. Unknown required information routes to qualification rather than rejection.
6. Duplicate Telegram updates and worker retries do not duplicate state-changing work or notifications.
7. A simulated owner reply can produce a confirmed visit only after renter approval of a compatible time.

## 7. Post-Core-Demo Follow-up

After the acceptance gates pass, add live email send/receive behind the established communication adapter, then evaluate WhatsApp separately. Do not start those integrations until the simulated qualification workflow, state transitions, and data quality are demonstrably stable.
