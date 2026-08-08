# FlatHunter V0 — Project Context

## Purpose

FlatHunter is a Telegram-first rental-search concierge for Hyderabad. Its purpose is to reduce the repetitive, high-friction work of flat hunting: explaining requirements, reviewing noisy listings, filtering poor matches, contacting owners/brokers/current tenants, collecting missing facts, and coordinating visits.

The V0 product does **not** attempt to scrape the web automatically. Property inventory is supplied by an admin through Telegram using screenshots, copied text, chat screenshots, notes, and optional property photos.

## Product promise

The product should move a renter from:

> “I need a place to live.”

Toward:

> “Here are a few properties genuinely worth visiting, with the relevant facts already checked.”

The product optimizes for **requirement → qualified property → visit**, not for the number of listings shown.

## First market

- Market: Hyderabad, India
- Currency: INR
- Architecture: city-agnostic where practical
- V0 inventory: manually supplied Hyderabad rental listings
- Supported rental types:
  - Entire property
  - Private room / replacement
  - Shared room
- Demand-only posts are recognized but are not stored as property inventory.

## Primary roles

### Renter

Uses the Telegram bot to:

- Describe rental requirements naturally
- Review the parsed requirement summary
- Start, pause, modify, or close one active search
- Review promising matches
- Approve first outreach to a new lead
- Answer escalated questions that exceed previously stated preferences or constraints
- Confirm, reschedule, or cancel visits

### Admin

Uses the same Telegram bot, identified by Telegram user ID, to:

- Add one listing from multiple information inputs
- Bulk-add listings where one screenshot represents one property
- Add property photos that are stored but not analyzed in V0
- Review extracted property information
- Resolve important contradictions
- Correct extracted fields conversationally
- Approve/reject listing drafts
- Mark listings available/unavailable
- Review failed or uncertain ingestion attempts

## Core design principles

1. **Specific product, generic architecture.** V0 is Hyderabad-focused, but core domain concepts should not be Hyderabad-specific.
2. **Code decides what; the LLM understands and phrases.** Hard constraints, state transitions, scoring, job execution, and persistence are deterministic. The LLM handles unstructured understanding and natural-language generation.
3. **Unknown is not false.** If a required field is unknown, the listing remains a candidate and generates a qualification task. Only explicit contradiction should reject it.
4. **Canonical + flexible storage.** Frequently used fields are normalized columns. All other useful extracted details are preserved in flexible JSONB context.
5. **Admin approval before listing publication in V0.** Extraction should not silently publish inventory.
6. **First outreach requires renter approval.** After approval, the system may gather facts autonomously within known renter boundaries.
7. **Do not invent renter decisions.** Anything outside stored constraints/preferences is escalated.
8. **Do not overbuild V0.** Prefer one Python application, one bot, one database, one initial LLM provider, and simple background jobs.

## V0 architecture summary

```text
Telegram
  |
  v
FlatHunter Bot (aiogram)
  |
  v
Single Python Application
  |-- renter requirement flow
  |-- admin ingestion flow
  |-- matching engine
  |-- qualification workflow
  |-- scheduling workflow
  |-- background job worker
  |-- communication adapters
  |
  +--> Supabase/PostgreSQL
  +--> Initial LLM provider (Gemini)
  +--> Email outreach initially
  +--> WhatsApp adapter later in V0/post-V0 depending integration readiness
```

## Technology decisions

- Language: Python 3.12+
- Telegram framework: aiogram
- API/web framework: FastAPI
- Validation: Pydantic
- Database: Supabase PostgreSQL
- Flexible attributes: PostgreSQL JSONB
- Initial model provider: Gemini through a small provider interface
- Matching: deterministic Python
- State machines: deterministic Python + database states
- Development hosting: local laptop using Telegram long polling
- Background work: simple DB-backed jobs / async worker; no Celery/Redis in V0
- Listing photos: store/reuse Telegram file identifiers initially where practical

## Explicit V0 non-goals

Do not implement unless a later decision explicitly changes scope:

- Automated Facebook scraping
- General web scraping
- Web admin panel
- Multiple active renter searches per user
- Multi-provider model router
- Vector database
- Agent frameworks such as CrewAI/AutoGen/LangGraph
- Microservices
- Kafka
- Redis/Celery
- Kubernetes
- Automatic visual analysis of normal flat photos
- Sophisticated duplicate detection
- Google/Outlook Calendar integration
- Visit route optimization
- Full negotiation agent
- Autonomous payments or deposits
- Complex privacy/PII abstraction layer
- Production-grade multi-city location intelligence

## Documents to read next

A coding agent should read these in order:

1. `01_PRD.md`
2. `05_DOMAIN_MODEL_BUSINESS_RULES.md`
3. `02_USER_ADMIN_FLOWS.md`
4. `04_DATABASE_DESIGN.md`
5. `06_MATCHING_RANKING_SPEC.md`
6. `07_LLM_SPEC_PROMPT_CONTRACTS.md`
7. `08_WORKFLOW_STATE_MACHINES.md`
8. `09_COMMUNICATION_OUTREACH_SPEC.md`
9. `10_TELEGRAM_BOT_UX_SPEC.md`
10. `03_SYSTEM_ARCHITECTURE.md`
11. `11_TECHNICAL_IMPLEMENTATION_SPEC.md`
12. `12_MVP_IMPLEMENTATION_PLAN.md`
13. `13_TESTING_LLM_EVALUATION_PLAN.md`
14. `14_FUTURE_BACKLOG.md`

## Definition of V0 success

V0 is successful when a renter can:

1. Describe requirements in Telegram.
2. Start an active Hyderabad search.
3. Receive matches from admin-supplied inventory.
4. Understand why a property is or is not a good fit.
5. Approve outreach to a promising lead.
6. Have missing facts gathered through the workflow.
7. Receive a qualified property and confirm a visit slot.

A polished scraper, web UI, or multi-agent framework is **not** required to prove the product.
