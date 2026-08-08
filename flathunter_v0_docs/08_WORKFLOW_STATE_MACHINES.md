# FlatHunter V0 — Workflow & State Machine Specification

## 1. Purpose

Persistent state machines prevent the LLM from inventing workflow behavior and make the system recoverable after restart.

All durable states belong in PostgreSQL.

## 2. Search state machine

```text
DRAFT (optional transient concept)
   |
   v
ACTIVE <------ resume ------ PAUSED
   |                         ^
   | pause                   |
   +-------------------------+
   |
   | close
   v
CLOSED
```

### ACTIVE

Allowed actions:

- Evaluate listings
- Notify renter of matches
- Approve/start outreach
- Continue qualification/scheduling

### PAUSED

- No new inventory monitoring
- Preserve matches/conversations
- Existing confirmed visits may still remain valid

### CLOSED

- No new matching/outreach
- Historical data retained

## 3. Ingestion state machine — single property

```text
COLLECTING_INFO
      |
      | /doneinfo
      v
EXTRACTING
      |
      +--> FAILED
      |
      v
NEEDS_REVIEW <---- conflict/edit ----+
      |                               |
      | resolved                      |
      v                               |
COLLECTING_MEDIA ---------------------+
      |
      | done/skip
      v
READY_FOR_APPROVAL
      |          |
 approve        reject
      |          |
      v          v
APPROVED      REJECTED
```

Implementation may collapse `NEEDS_REVIEW` and `READY_FOR_APPROVAL` if no conflicts exist, but semantic distinctions should remain clear.

## 4. Bulk ingestion state model

A bulk session contains multiple draft groups.

Session-level status may remain simple:

```text
COLLECTING_INFO -> EXTRACTING -> NEEDS_REVIEW -> APPROVED/COMPLETED
```

Each child draft independently becomes:

```text
EXTRACTED -> NEEDS_REVIEW -> APPROVED | REJECTED | FAILED
```

One screenshot maps to one draft by default.

## 5. Listing availability state machine

```text
          confirm available
UNKNOWN ----------------------> AVAILABLE
  |                                |
  | age beyond freshness           | age beyond freshness
  v                                v
STALE <------------------------ AVAILABLE
  |                                |
  | confirm available              | explicit unavailable
  +-----------> AVAILABLE          v
                               UNAVAILABLE

UNKNOWN -- explicit unavailable --> UNAVAILABLE
STALE   -- explicit unavailable --> UNAVAILABLE
```

Admin may manually transition to `AVAILABLE` or `UNAVAILABLE` when appropriate.

## 6. Match state lifecycle

Matching is derived and may be recomputed.

```text
REJECTED
POSSIBLE_MATCH
NEEDS_QUALIFICATION
STRONG_MATCH
QUALIFIED
SKIPPED
```

Suggested transitions:

```text
POSSIBLE_MATCH -> STRONG_MATCH
POSSIBLE_MATCH -> NEEDS_QUALIFICATION
STRONG_MATCH -> NEEDS_QUALIFICATION
NEEDS_QUALIFICATION -> QUALIFIED
any actionable state -> REJECTED when new hard contradiction appears
any actionable state -> SKIPPED when renter explicitly skips
```

Re-evaluation can move a match between categories as listing/search facts change.

## 7. Conversation state machine

```text
APPROVED_FOR_CONTACT
        |
        v
CONTACTED
        |
        v
AWAITING_REPLY
    |           |
 reply       follow-up due
    |           |
    v           v
QUALIFYING   AWAITING_REPLY
    |           |
    |        no response after allowed follow-up
    |           v
    |       NO_RESPONSE
    |
    +--> ESCALATED_TO_RENTER
    |          |
    |          | renter decision
    |          v
    |       QUALIFYING
    |
    +--> QUALIFIED
             |
             v
     READY_FOR_SCHEDULING
             |
             v
           CLOSED
```

A hard contradiction may close the conversation for this renter early.

## 8. Qualification action loop

At each inbound reply:

```text
Parse reply
   |
   v
Update explicit facts
   |
   v
Re-run hard constraints
   |
   +-- violation --> reject match + close politely
   |
   v
Does reply require renter decision?
   |
   +-- yes --> ESCALATED_TO_RENTER
   |
   v
Important missing facts remain?
   |
   +-- yes --> choose next question -> send -> AWAITING_REPLY
   |
   v
Mark match QUALIFIED
```

## 9. Follow-up state behavior

V0 allows one polite follow-up.

Conceptual logic:

```text
initial message sent
  -> no reply by configured due time
  -> if follow_up_count == 0: send follow-up
  -> increment follow_up_count
  -> if no reply after second due time: NO_RESPONSE
```

No response must not change listing to `UNAVAILABLE`.

## 10. Scheduling / visit state machine

```text
PROPOSED
    |
    v
AWAITING_RENTER_CONFIRMATION
    |              |
 confirm         reject/none fit
    |              |
    v              +--> negotiate another proposal -> PROPOSED
CONFIRMED
    |     |
 cancel reschedule
    |     |
    v     v
CANCELLED  PROPOSED (new candidate time)
    |
    +------------------------------

CONFIRMED -> COMPLETED (after visit / renter marks done)
```

For rescheduling, implementations may keep the same visit record with updated proposal history or create a new proposal record; choose one approach consistently.

## 11. Background job state machine

```text
PENDING
   |
   v
RUNNING
 |     |
 |     +--> FAILED (attempts remain)
 |               |
 |               +--> PENDING with backoff if retryable
 v
SUCCEEDED
```

Workers should lock jobs transactionally or with an equivalent claim mechanism.

## 12. Important transition triggers

### `LISTING_CREATED`

- Evaluate against all `ACTIVE` searches.

### `SEARCH_STARTED`

- Evaluate against approved eligible inventory.

### `SEARCH_UPDATED`

- Re-evaluate relevant inventory.

### `LISTING_UPDATED`

- Re-evaluate existing matches.

### `CONTACT_REPLY_RECEIVED`

- Parse facts and continue qualification.

### `VISIT_SLOT_SELECTED`

- Confirm with contact before setting visit `CONFIRMED`.

## 13. Idempotency rules

Every transition handler should verify current state.

Examples:

- Re-clicking `Approve Listing` must not create duplicate listings.
- Re-clicking `Contact Them` must not send duplicate initial outreach.
- Duplicate webhook/poll messages must not create duplicate inbound messages.
- Confirming an already-confirmed visit should not send multiple confirmations.

## 14. LLM boundary

The LLM may propose parsed intent or structured facts, but it may not directly set persistent workflow states.

State transitions are application decisions validated against allowed transitions.
