# FlatHunter V0 — Technical Implementation Specification

## 1. Runtime stack

Recommended V0 stack:

- Python 3.12+
- aiogram for Telegram bot
- FastAPI for HTTP/API surface
- Pydantic v2 for contracts and validation
- Supabase Python client and/or async PostgreSQL client
- Supabase PostgreSQL
- Gemini as initial LLM provider
- httpx for external HTTP integrations
- pytest for testing

## 2. Repository structure

Suggested layout:

```text
flathunter/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── telegram/
│   │   ├── bot.py
│   │   ├── renter_handlers.py
│   │   ├── admin_handlers.py
│   │   ├── callbacks.py
│   │   ├── keyboards.py
│   │   └── states.py
│   ├── requirements/
│   │   ├── service.py
│   │   ├── schemas.py
│   │   └── prompts.py
│   ├── ingestion/
│   │   ├── service.py
│   │   ├── schemas.py
│   │   └── conflict_rules.py
│   ├── listings/
│   │   ├── service.py
│   │   └── repository.py
│   ├── matching/
│   │   ├── engine.py
│   │   ├── scoring.py
│   │   └── reasons.py
│   ├── qualification/
│   │   ├── service.py
│   │   └── actions.py
│   ├── scheduling/
│   │   ├── service.py
│   │   └── availability.py
│   ├── communications/
│   │   ├── base.py
│   │   ├── email.py
│   │   └── whatsapp.py
│   ├── llm/
│   │   ├── base.py
│   │   ├── gemini.py
│   │   ├── contracts.py
│   │   └── prompts/
│   ├── jobs/
│   │   ├── worker.py
│   │   ├── handlers.py
│   │   └── repository.py
│   ├── db/
│   │   ├── client.py
│   │   ├── repositories/
│   │   └── models.py
│   └── common/
│       ├── enums.py
│       ├── errors.py
│       └── time.py
├── migrations/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── evals/
├── samples/
│   ├── listings/
│   └── expected/
├── scripts/
├── .env.example
├── pyproject.toml
└── README.md
```

## 3. Configuration

Use environment variables for secrets and runtime settings.

Example `.env.example`:

```text
TELEGRAM_BOT_TOKEN=
ADMIN_TELEGRAM_IDS=
SUPABASE_URL=
SUPABASE_SERVICE_KEY=
GEMINI_API_KEY=
FLATHUNTER_DEFAULT_CITY=Hyderabad
FLATHUNTER_DEFAULT_TIMEZONE=Asia/Kolkata
LISTING_STALE_AFTER_DAYS=7
FOLLOW_UP_AFTER_HOURS=24
```

Communication-channel credentials should be added only when that adapter is implemented.

## 4. Application startup

Development startup can run:

1. FastAPI app
2. aiogram long-polling bot
3. background worker loop

These may run in one process initially or as separate local processes sharing the same database.

Recommended development simplicity:

```text
python -m app.main
```

where the app starts bot + worker tasks.

As complexity grows, split worker into a second process without changing domain code.

## 5. Telegram architecture

### Handlers

Handlers should:

- Authenticate role
- Parse Telegram update shape
- Call application service
- Render application result

Handlers should not contain:

- SQL
- Match formulas
- LLM prompts
- State-transition business logic

### Callback data

Use compact, structured callback IDs.

Example:

```text
match:contact:<match_id>
match:skip:<match_id>
listing:approve:<draft_id>
visit:confirm:<visit_id>:<proposal_id>
```

Validate entity ownership/role on every callback.

## 6. Database access

Use repository/service boundaries to keep SQL out of Telegram handlers.

Example:

```python
class ListingRepository:
    async def get(self, listing_id: UUID) -> Listing: ...
    async def create_from_draft(self, draft_id: UUID) -> Listing: ...
    async def update_canonical(self, listing_id: UUID, patch: dict) -> Listing: ...
```

Prefer transactions for operations that create multiple related rows, such as approving a listing.

## 7. LLM integration

### Provider abstraction

Keep it intentionally small.

```python
class LLMProvider(Protocol):
    async def generate_structured(...): ...
    async def generate_text(...): ...
```

### Structured parsing

All critical LLM outputs map to Pydantic models.

The application should never trust raw JSON strings directly.

### Retry policy

- Retry invalid structured output once when likely recoverable.
- Do not infinite-retry.
- Escalate ingestion failures to admin review.

## 8. Ingestion implementation

### Single mode

Maintain a durable ingestion session in the DB.

Information inputs:

- Telegram text -> store text
- Screenshot/document -> store Telegram file IDs + temporary bytes when model call requires them
- Admin notes -> store as explicit high-authority text input

After `/doneinfo`:

- Download needed information files
- Call listing extraction
- Validate result
- Store listing draft
- Resolve critical conflicts
- Proceed to media stage

### Media stage

Property photos:

- Save Telegram file IDs to session/draft media
- Do not send to model
- Attach to listing only after approval

### Approval

Use a transaction to:

- Create listing
- Create sources
- Create contacts/channels
- Create media rows
- Mark draft/session approved
- Insert `LISTING_CREATED` job

## 9. Matching implementation

Core API idea:

```python
class MatchResult(BaseModel):
    status: MatchStatus
    fit_score: float | None
    completeness: float | None
    hard_rejections: list[Reason]
    positives: list[Reason]
    missing: list[MissingField]


def evaluate_match(requirements, listing) -> MatchResult:
    ...
```

Keep pure scoring functions side-effect free so they are easy to unit test.

## 10. Background jobs

Use `agent_jobs` table.

Worker loop concept:

```python
while True:
    job = await claim_next_job()
    if not job:
        await asyncio.sleep(POLL_INTERVAL)
        continue

    try:
        await dispatch(job)
        await mark_succeeded(job)
    except RetryableError as exc:
        await reschedule(job, exc)
    except Exception as exc:
        await mark_failed(job, exc)
```

Use database locking/atomic update to avoid two workers claiming the same job later.

## 11. Suggested job types

- `LISTING_CREATED`
- `LISTING_UPDATED`
- `SEARCH_STARTED`
- `SEARCH_UPDATED`
- `SEND_OUTREACH`
- `PROCESS_CONTACT_REPLY`
- `CHECK_FOLLOW_UPS`
- `CHECK_STALE_LISTINGS`
- `SEND_RENTER_NOTIFICATION`

## 12. Communication adapter interface

Keep minimal:

```python
class CommunicationAdapter(Protocol):
    channel_type: ContactChannelType

    async def send(self, destination: str, message: str, context: dict) -> SendResult:
        ...
```

Inbound handling can be adapter-specific.

Do not design a generalized omnichannel platform in V0.

## 13. Email implementation

Initial email adapter should support:

- Dedicated mailbox
- Send message
- Capture provider message/thread ID
- Poll or webhook inbound replies
- Correlate replies to logical conversation

Exact provider library/auth choice can be selected during implementation.

## 14. WhatsApp implementation boundary

Do not block core V0 on WhatsApp.

When implemented:

- Use the official business API
- Verify current platform requirements
- Keep templates/session-window details inside adapter/service layer
- Core qualification should remain channel-independent

## 15. Time handling

Store all timestamps as timezone-aware `TIMESTAMPTZ`.

Default renter timezone for Hyderabad V0: `Asia/Kolkata`.

Convert to local time only for display and parsing.

## 16. Error handling

Define domain exceptions such as:

- `EntityNotFound`
- `InvalidStateTransition`
- `LLMValidationError`
- `CommunicationSendError`
- `UnauthorizedAdminAction`
- `ConflictNeedsAdminReview`

Telegram layer converts exceptions into user-friendly messages.

## 17. Logging

Log structured operational events:

- ingestion session started/completed
- extraction success/failure
- listing approved
- match evaluated
- outreach sent/failed
- conversation state change
- visit confirmed/cancelled
- job failed

Never log API keys or database secrets.

## 18. Migrations

Use SQL migrations committed to the repository.

Supabase CLI migrations are a good fit, but the exact migration tooling may be chosen during project setup.

Every schema change should be reproducible from source control.

## 19. API endpoints

FastAPI is included for future integrations and health/diagnostics.

V0 minimum endpoints may be only:

- `GET /health`
- future webhook endpoints for communication channels

Do not build a public REST API solely because FastAPI exists.

## 20. Local development workflow

1. Create Telegram bot.
2. Create Supabase project.
3. Apply migrations.
4. Configure `.env`.
5. Run bot with long polling.
6. Add test admin Telegram ID.
7. Use real/synthetic Hyderabad sample listings.
8. Run tests before prompt/schema changes.

## 21. Production-readiness items intentionally deferred

- Horizontal scaling
- Distributed locks
- Full observability stack
- Message queue infrastructure
- Autoscaling workers
- Central object storage migration
- Fine-grained RBAC
- Production privacy policy implementation
- Rate-limit orchestration across multiple LLM providers
