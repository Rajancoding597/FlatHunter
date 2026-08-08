# FlatHunter V0 — System Architecture

## 1. Architectural objective

FlatHunter V0 should be easy to build, inspect, and modify. The architecture should support the complete vertical slice without introducing unnecessary distributed-system complexity.

The application is a **modular monolith**: one Python codebase with clearly separated domain modules.

## 2. High-level architecture

```text
                          TELEGRAM
                             |
                  +----------+-----------+
                  |                      |
              Renter UX               Admin UX
                  |                      |
                  +----------+-----------+
                             |
                             v
                    FlatHunter Bot Layer
                         (aiogram)
                             |
                             v
                    Application Services
     +-----------------------+-------------------------+
     |                       |                         |
Requirements          Ingestion/Listing          Search/Matching
     |                       |                         |
     +-----------------------+-------------------------+
                             |
                   Workflow / State Layer
        +--------------------+--------------------+
        |                    |                    |
Qualification          Scheduling          Background Jobs
        |                    |                    |
        +--------------------+--------------------+
                             |
             +---------------+---------------+
             |                               |
             v                               v
        Supabase/Postgres                LLM Provider
             |                               |
             |                               v
             |                           Gemini V0
             |
             +---- Communication adapters
                    |-- Email first
                    |-- WhatsApp when ready
                    |-- Telegram renter-side
```

## 3. Component responsibilities

### 3.1 Telegram layer

Responsibilities:

- Receive commands/messages/media
- Distinguish admin vs renter routes
- Maintain short conversational UI state
- Render summaries, buttons, and property galleries
- Convert Telegram callbacks into application commands

The Telegram layer should not contain matching logic or business rules.

### 3.2 Requirement service

Responsibilities:

- Maintain renter requirement drafts
- Call the LLM to interpret natural language
- Validate and normalize extracted requirements
- Determine mandatory missing fields
- Produce requirement summaries
- Apply renter-requested modifications

### 3.3 Ingestion service

Responsibilities:

- Create ingestion sessions
- Group information inputs
- Distinguish information-bearing inputs from property media
- Call listing extraction LLM contract
- Detect contradictions
- Apply admin corrections
- Produce draft listings
- Persist approved listings

### 3.4 Listing service

Responsibilities:

- Read/write canonical listing records
- Manage availability state
- Manage contacts and contact channels
- Manage flexible extracted context
- Manage property media
- Expose listing summaries to matching/Telegram layers

### 3.5 Matching service

Responsibilities:

- Enforce hard constraints
- Calculate deterministic fit score
- Calculate information completeness
- Determine match classification
- Generate deterministic reason codes
- Optionally call soft-context LLM reasoning
- Create/update match records

### 3.6 Qualification service

Responsibilities:

- Determine missing required/important facts for a renter/listing pair
- Manage qualification conversation state
- Parse inbound contact replies with the LLM
- Update explicit property facts
- Stop on hard contradiction
- Escalate renter decisions outside known boundaries
- Mark a match qualified when criteria are satisfied

### 3.7 Scheduling service

Responsibilities:

- Parse renter availability
- Parse proposed slots from contact replies
- Filter compatible slots deterministically
- Require renter confirmation before commitment
- Create/reschedule/cancel visit records

### 3.8 Communication service

Responsibilities:

- Select best supported contact channel
- Send outbound messages
- Store channel attempts and message history
- Receive/poll inbound responses where integration supports it
- Keep one logical conversation independent of channel

V0 should start with a minimal interface rather than an elaborate communication framework.

### 3.9 LLM service

Responsibilities:

- Expose a provider-neutral interface
- Handle structured generation
- Enforce Pydantic validation
- Record model call metadata
- Retry invalid outputs according to policy

V0 implementation: Gemini.

### 3.10 Background worker

Responsibilities:

- Consume DB-backed jobs
- Run new-listing matching
- Run search re-evaluation
- Check follow-up due times
- Run staleness checks
- Perform communication polling if required

Avoid Redis/Celery in V0.

## 4. Key event flows

### 4.1 Listing approved

```text
Admin approves draft
  -> listing persisted
  -> LISTING_CREATED job inserted
  -> worker loads ACTIVE searches
  -> matching service evaluates listing per search
  -> match records created/updated
  -> renter notification created if policy says so
```

### 4.2 Search started

```text
Renter confirms requirements
  -> search ACTIVE
  -> SEARCH_STARTED job inserted
  -> worker loads approved inventory
  -> matching service evaluates listings
  -> best candidates surfaced
```

### 4.3 Outreach approved

```text
Renter taps Contact Them
  -> create logical conversation
  -> resolve contact channel
  -> generate first message
  -> send
  -> store message/outreach attempt
  -> conversation AWAITING_REPLY
```

### 4.4 Contact reply received

```text
Inbound reply
  -> store raw message
  -> LLM reply parsing
  -> validate extracted facts
  -> update explicit canonical facts
  -> re-evaluate hard constraints
  -> choose next qualification action
  -> generate/send next message or escalate renter
```

### 4.5 Visit confirmed

```text
Qualified match
  -> gather renter availability if needed
  -> contact proposes slot(s)
  -> parse and filter slots
  -> renter chooses exact slot
  -> confirm with contact
  -> visit CONFIRMED
```

## 5. Data ownership

PostgreSQL is the source of truth for:

- Users
- Search sessions
- Requirements
- Listings
- Contacts
- Match results
- Conversation state
- Messages
- Visits
- Jobs
- Ingestion sessions/drafts

The LLM is never the source of truth.

## 6. State ownership

Persistent workflow state belongs in the database, not in process memory.

Telegram finite-state-machine state may be used for short interaction routing, but any state required to recover after restart should be persisted.

## 7. File/media approach

### V0

Use Telegram file identifiers for property media where practical.

Store metadata in `listing_media`.

Information-bearing screenshots may also retain Telegram file IDs and/or be downloaded temporarily for model input depending provider SDK needs.

### Later

Migrate/copy media to Supabase Storage or another object store if retention, portability, or external delivery requires it.

## 8. Reliability approach

V0 should favor idempotent handlers.

Examples:

- Processing `LISTING_CREATED` twice should not duplicate matches.
- Receiving the same inbound message twice should not duplicate extracted updates.
- Visit confirmation callbacks should check current state before changing it.

Use database unique constraints and idempotency keys where practical.

## 9. Security scope for V0

Minimum required controls:

- Telegram user ID allowlist or admin role for admin commands
- Secrets in environment variables, never committed
- Database service credentials restricted to backend
- Avoid exposing internal IDs unnecessarily in user-facing text

Production-grade privacy and policy layers are deferred, but the code should not intentionally log secrets or credentials.

## 10. Deployment evolution

### Development

```text
Laptop
  |-- aiogram long polling
  |-- FastAPI process
  |-- background worker
  +--> Supabase
  +--> LLM API
```

### Later deployment

Move the same modular monolith to a persistent host/container. Webhooks may replace long polling if desired.

No architectural rewrite should be required.
