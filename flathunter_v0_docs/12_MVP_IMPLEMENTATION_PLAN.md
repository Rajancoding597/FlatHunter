# FlatHunter V0 — MVP Implementation Plan

## 1. Implementation philosophy

Build vertical slices that are demoable independently.

Do not implement every future integration before proving the core workflow.

The primary sequence is:

> Requirements -> inventory -> matching -> qualification -> visit

## 2. Milestone 0 — repository and infrastructure skeleton

### Deliverables

- Python project initialized
- Dependency management configured
- `.env.example`
- Supabase project connected
- Initial migration framework
- aiogram bot responds to `/start`
- Admin ID recognition works
- `/health` endpoint works
- Basic logging configured

### Exit criteria

- App starts locally with one command.
- Bot can distinguish admin from renter.
- Database connectivity test passes.

## 3. Milestone 1 — renter requirement collection

### Build

- User/search tables
- Requirement schemas
- Requirement extraction LLM contract
- Requirement follow-up logic
- Requirement summary
- `Start Search`
- Search states `ACTIVE/PAUSED/CLOSED`
- Requirement edit/patch flow

### Exit criteria

A renter can naturally say:

> Private room, Gachibowli/Kondapur, around 20k max 23k, Sep 1, attached bath required.

and the system stores a validated active search.

## 4. Milestone 2 — admin single-property ingestion

### Build

- `/admin`
- `/addlisting`
- durable ingestion session
- information input collection
- `/doneinfo`
- listing extraction contract
- canonical + flexible context draft
- contradiction review
- conversational admin edits
- property media stage
- `Approve & Save`

### Exit criteria

Admin can take a real Hyderabad post screenshot plus notes and create one approved listing with contacts and photos.

## 5. Milestone 3 — bulk ingestion

### Build

- `/bulkadd`
- one screenshot -> one draft
- independent extraction
- review queue
- approve/edit/reject each draft

### Exit criteria

Admin can ingest a batch of screenshots without mixing their property data.

## 6. Milestone 4 — deterministic matching

### Build

- Hard-constraint evaluator
- Default scoring weights
- Personalized weight normalization
- Fit score
- Information completeness
- Missing information generation
- Reason codes
- Match persistence
- `LISTING_CREATED` matching job
- `SEARCH_STARTED` matching job

### Exit criteria

Approved listings are evaluated automatically against active searches, and the renter can view explainable matches.

## 7. Milestone 5 — active search monitoring

### Build

- New listing automatically checks all active searches
- Search updates trigger re-evaluation
- Listing updates trigger match updates
- Simple notification policy

### Exit criteria

A renter can start a search, leave, and later receive a meaningful match when admin approves new inventory.

## 8. Milestone 6 — outreach simulation before real automation

Before fighting external channel APIs, validate qualification intelligence.

### Build

- `Contact Them` approval
- conversation creation
- generate initial outreach message
- admin/developer can manually send message
- paste/submit reply back into system
- reply parsing
- listing fact updates
- next-question generation
- escalation logic

### Exit criteria

Using manual transport, the entire qualification conversation can progress correctly from first contact to qualified/rejected.

This milestone is critical because it proves the product logic independent of channel integration.

## 9. Milestone 7 — automated email outreach

### Build

- Email adapter
- Dedicated mailbox integration
- outbound send
- inbound reply correlation
- conversation message persistence
- one follow-up policy

### Exit criteria

After renter approval, an email-capable property lead can be qualified without manual copy/paste.

## 10. Milestone 8 — scheduling

### Build

- Renter availability parsing/storage
- Proposed time parsing
- Compatible slot filtering
- Exact renter confirmation
- Visit persistence
- Visit confirmation message
- Basic reschedule/cancel

### Exit criteria

A qualified property can become a confirmed visit without manual scheduling coordination.

## 11. Milestone 9 — WhatsApp adapter exploration/integration

### Build only after core workflow is working

- Verify current official WhatsApp Business API requirements
- Implement channel adapter
- Handle templates/session constraints as needed
- Add WhatsApp to channel selector

### Exit criteria

At least one supported WhatsApp outreach flow works through the same logical conversation system without changing core qualification logic.

If WhatsApp integration is blocked by account/policy/cost constraints, V0 can still be demonstrated with email/manual fallback.

## 12. Milestone 10 — hardening and developer evaluation

### Build

- Hyderabad extraction evaluation set
- Matching unit tests
- State-transition tests
- Idempotency checks
- Failure/retry tests
- Logging cleanup
- Setup documentation

### Exit criteria

A new developer/agent can clone, configure, run, and execute the core demo without undocumented manual steps.

## 13. Recommended first demo scenario

Use one renter and 20-50 curated Hyderabad listings.

Demo:

1. Renter describes requirement in Telegram.
2. Admin uploads several real listing screenshots.
3. Listing extraction is reviewed and approved.
4. A strong match appears automatically.
5. Renter taps `Contact Them`.
6. Qualification gathers missing deposit/brokerage/parking information.
7. Property remains compatible.
8. Owner proposes a viewing time.
9. Renter confirms.
10. Visit is marked confirmed.

## 14. Suggested development order within each milestone

For every feature:

1. Define Pydantic/domain schema.
2. Add migration if required.
3. Implement pure business logic.
4. Add unit tests.
5. Implement service/repository layer.
6. Add Telegram handler/UI.
7. Test manually in Telegram.
8. Add integration test for the happy path.

## 15. Stop conditions for scope creep

Do not pause core implementation to build:

- Better scraping
- Fancy admin dashboard
- Multiple LLM providers
- Vector search
- Sophisticated fraud detection
- Route optimization
- Multi-city support

unless the current vertical slice cannot progress without it.
