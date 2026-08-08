# FlatHunter V0 — Domain Model & Business Rules

## 1. Core domain concepts

### Renter

A Telegram user seeking a rental property.

### Search

A persistent renter intent. V0 permits one active search per renter.

### Requirement

A renter constraint or preference used for matching and qualification.

### Listing

An approved unit of rental supply.

### Match

The evaluation of one listing against one search.

### Qualification

The process of resolving important unknown facts and confirming that no hard renter requirement is violated.

### Conversation

A logical owner/broker/current-tenant outreach thread associated with one renter search and listing.

### Visit

A proposed or confirmed property-viewing appointment.

## 2. Listing types

### `ENTIRE_PROPERTY`

The renter is taking the whole advertised property.

Examples:

- Entire 2BHK
- Entire 3BHK

### `PRIVATE_ROOM`

One private bedroom is available inside a larger property, including replacement cases.

Example:

> Private room available in an existing 3BHK.

### `SHARED_ROOM`

The renter occupies a shared bedroom/space with one or more people.

### Demand-only content

A post from another renter looking for property is `RENTER_REQUIREMENT`, not inventory.

## 3. Requirement importance

### `REQUIRED`

An explicit violation rejects the listing.

Unknown does not reject; it creates a qualification gap.

### `PREFERRED`

Affects score but should not reject the listing.

### `DOES_NOT_MATTER`

Ignored in matching.

## 4. Unknown-value rule

This is a foundational rule.

For canonical booleans:

```text
TRUE  = explicitly present/allowed
FALSE = explicitly absent/not allowed
NULL  = unknown/not established
```

Never interpret omission as false.

Examples:

- Post does not mention parking -> `car_parking = NULL`
- Post says “no car parking” -> `car_parking = FALSE`

## 5. Canonical vs flexible data

### Canonical data

Used by deterministic business logic.

Examples:

- Listing type
- Location
- Rent
- Maintenance
- Deposit
- Brokerage
- Move-in date
- Furnishing
- Attached bathroom
- Parking

### Flexible context

All other useful facts extracted by the LLM.

Examples:

- Society name
- Room size description
- Appliances
- Flatmate professions
- Lock-in period
- Cook/maid details
- Natural light
- Nearby landmarks
- Society amenities

Flexible context may inform optional LLM reasoning but may not silently override canonical facts.

## 6. Information authority

When sources conflict, use this default precedence:

1. Explicit admin correction
2. Explicit later verified contact response
3. Explicit original listing statement
4. LLM inference

Important unresolved canonical conflicts are escalated to admin.

## 7. Search lifecycle

### `ACTIVE`

- Evaluate current inventory
- Evaluate newly approved listings
- Permit outreach/qualification

### `PAUSED`

- Preserve search and matches
- Stop new monitoring/outreach

### `CLOSED`

- Search is finished/abandoned
- No future matching or outreach

## 8. Listing availability lifecycle

### `UNKNOWN`

Availability not recently confirmed.

### `AVAILABLE`

Explicitly confirmed recently.

### `UNAVAILABLE`

Explicitly confirmed gone/occupied/withdrawn or manually marked unavailable.

### `STALE`

Information is too old to confidently present as current without revalidation.

V0 starting rule: approximately 7 days since last reliable verification, configurable.

## 9. Stale-listing behavior

A stale listing may remain in the database.

Do not delete it automatically.

If it appears attractive for a renter:

- Revalidate before treating it as a strong actionable opportunity.

## 10. Matching gate rules

Hard-reject only when there is an explicit violation.

Typical gates:

- Wrong listing type
- Excluded location
- Rent above hard maximum
- Required feature explicitly absent
- Move-in timing explicitly incompatible with a hard deadline
- Listing `UNAVAILABLE`

Unknown values do not hard-reject.

## 11. Qualification rules

Qualification is specific to a renter/listing pair.

A listing does not need every database field populated to become qualified.

A match may be considered qualified when:

- Availability is confirmed
- No hard renter requirement is known to be violated
- All renter-required unknown fields that matter are resolved
- Core financial terms are sufficiently known for the renter to make a visit decision

## 12. Qualification question priority

Recommended order:

1. Availability
2. Hard requirement gaps
3. Core financial gaps (rent if uncertain, deposit, brokerage)
4. High-weight preferences
5. Other useful clarifications

Stop early if a hard contradiction appears.

## 13. Outreach autonomy

Before first contact with a lead:

- Renter approval required.

After approval, agent may:

- Ask factual qualification questions
- Clarify responses
- Answer known renter facts
- Follow up once after no response

Agent must not invent renter decisions.

## 14. Escalation rules

Escalate when:

- Contact proposes rent above hard max
- New brokerage or fee requires acceptance
- Move-in date requires a renter compromise
- Lock-in or deposit exceeds a stated boundary
- Contact asks for information the renter has not authorized/provided
- Contact asks for payment/financial commitment
- Scheduling requires committing to a specific time

## 15. Contact-channel rules

The LLM extracts all explicit channels.

Channel choice is deterministic application logic.

Preferred conceptual order:

1. Explicit WhatsApp contact when supported by integration/policy
2. Email
3. Explicit Telegram contact where technically usable
4. Phone-only/manual fallback

Do not send the same initial outreach through all channels simultaneously.

## 16. Conversation rules

- One logical conversation may span channel attempts.
- Store every outbound/inbound message.
- No reply does not mean unavailable.
- One polite follow-up is permitted in V0.
- After follow-up with no response, conversation may become `NO_RESPONSE`.

## 17. Scheduling rules

- Scheduling begins after qualification.
- General renter availability may be collected lazily at first scheduling need.
- Agent may negotiate candidate windows.
- Exact time requires renter confirmation before final commitment.
- Basic rescheduling and cancellation are supported.

## 18. Property media rules

### Information-bearing images

May be sent to the LLM.

### Property photos

Stored for renter viewing and not analyzed in V0.

The admin explicitly chooses which stage an image belongs to.

## 19. Admin ingestion rules

### Single mode

Many information inputs -> one property draft.

### Bulk mode

One screenshot -> one property draft.

### Approval

A draft becomes searchable inventory only after admin approval.

## 20. Anti-overengineering rules

When uncertain whether to implement an advanced feature in V0, ask:

> Does this directly help demonstrate requirement -> qualified flat -> visit?

If not, defer it.
