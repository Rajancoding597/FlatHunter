# FlatHunter V0 — Product Requirements Document

## 1. Overview

FlatHunter is a Telegram-first rental-search concierge for Hyderabad. It reduces the manual effort involved in flat hunting by understanding renter intent, matching it against curated listing inventory, qualifying promising leads, and coordinating property visits.

V0 deliberately avoids automated scraping. An admin supplies inventory through Telegram. This lets the team validate the intelligence, workflow, matching, and conversation experience before investing in source acquisition automation.

## 2. Problem statement

Flat hunting commonly requires repeated effort across fragmented sources:

- Browsing noisy rental groups and portals
- Interpreting inconsistent posts
- Repeatedly explaining requirements
- Checking whether listings are still available
- Asking the same questions about rent, deposit, brokerage, parking, furnishing, and move-in date
- Following up with owners/brokers/tenants
- Coordinating property visits
- Physically visiting poor-fit options due to incomplete information

Existing listing products optimize primarily for search and lead generation. FlatHunter should optimize for **qualified visit generation**.

## 3. Target user

### Primary renter

A Hyderabad renter looking for:

- An entire flat
- A private room in an existing flat
- A shared room

The renter values reduced search fatigue and is willing to describe requirements once, then let the system continue monitoring inventory.

### Admin

A trusted operator who supplies listings and reviews extraction quality during development.

## 4. Product goals

V0 must:

1. Collect renter requirements conversationally through Telegram.
2. Store one active search per renter.
3. Allow admin-only listing ingestion through Telegram.
4. Extract structured listing data from screenshots/text/documents/notes.
5. Preserve both canonical fields and flexible extracted context.
6. Match listings against active searches using deterministic rules and scoring.
7. Keep unknown information as qualification gaps rather than treating it as failure.
8. Require renter approval before first outreach to a lead.
9. Support automated fact-gathering after outreach approval.
10. Escalate decisions that exceed known renter boundaries.
11. Coordinate a visit without committing the renter until the exact time is confirmed.
12. Store listing photos and show them to renters without analyzing them in V0.

## 5. Non-goals

V0 does not need:

- Automated social-media scraping
- Web UI/admin website
- Automatic phone calling
- General-purpose negotiation
- Payments/deposits
- Multiple simultaneous active searches per renter
- Route optimization
- Calendar integrations
- Production-grade anti-fraud/scam scoring
- Advanced image-quality/property-condition inference
- Multi-city UX
- Fully autonomous outreach across every communication platform

## 6. Core renter experience

### 6.1 Requirement collection

The renter may describe their need naturally, for example:

> Looking for a private room in Gachibowli or Kondapur. Madhapur is okay too. Around 20k, max 23k. Move-in around September 1. Attached bathroom is compulsory; furnished would be great.

The system should extract the core requirements, summarize them, and ask only important follow-up questions.

### 6.2 Mandatory search requirements

Before a search can start, the system should know:

- Desired listing type
- Preferred and/or acceptable locations
- Target rent and hard maximum rent
- Move-in timing

City is implicitly Hyderabad in V0.

### 6.3 Optional requirement enrichment

The renter may specify:

- Workplace/important location
- Excluded locations
- Property configuration preferences
- Furnishing
- Attached bathroom
- Car/bike parking
- Balcony
- Brokerage tolerance
- Maximum deposit
- Pet preferences
- Flatmate preferences
- Smoking-related preferences
- Other free-form preferences

Each meaningful preference may be tagged as:

- `REQUIRED`
- `PREFERRED`
- `DOES_NOT_MATTER`

### 6.4 Search behavior

Each renter has at most one active search in V0.

Search states:

- `ACTIVE`
- `PAUSED`
- `CLOSED`

When a search starts:

1. Existing approved inventory is evaluated.
2. Future approved listings automatically trigger matching against active searches.
3. Editing requirements causes relevant inventory to be re-evaluated.

## 7. Listing inventory experience

### 7.1 Supported listing types

- `ENTIRE_PROPERTY`
- `PRIVATE_ROOM`
- `SHARED_ROOM`

### 7.2 Unsupported inventory content

Demand-only posts such as “Looking for a room in Kondapur” are recognized as `RENTER_REQUIREMENT` and excluded from property inventory.

### 7.3 Listing availability states

- `UNKNOWN`
- `AVAILABLE`
- `UNAVAILABLE`
- `STALE`

Newly approved listings default to `UNKNOWN` unless the source provides a recent explicit availability statement that the application chooses to accept as evidence.

V0 may use a simple 7-day freshness rule. Listings without recent verification become `STALE`.

## 8. Admin ingestion requirements

### 8.1 Single-property mode

Admin starts one ingestion session and may submit:

- Information screenshots
- Chat screenshots
- Copied text
- Documents
- Manual notes/corrections

All information inputs in that session describe one property.

After information extraction, the admin may optionally attach property photos. These photos are stored but not analyzed by the LLM in V0.

### 8.2 Bulk mode

Admin may start a bulk session where:

> One uploaded screenshot = one property draft.

Each draft is processed independently and reviewed individually during development.

### 8.3 Contradiction handling

Important contradictory canonical facts should be escalated to the admin instead of guessed.

Examples:

- Rent differs across screenshots
- Deposit differs
- Locality differs
- Availability dates conflict
- Contact numbers conflict
- Listing type/configuration conflicts

Admin corrections outrank original listing content for canonical storage.

## 9. Matching requirements

### 9.1 Hard constraints

Explicit violations of hard requirements reject a listing for that search.

Examples:

- Listing type mismatch
- Rent above hard maximum
- Explicitly unavailable before required move-in deadline
- Required parking explicitly absent
- Excluded location

### 9.2 Unknowns

Unknown required fields do **not** reject the listing.

They create qualification gaps.

### 9.3 Scoring

Surviving listings receive:

- `fit_score`
- `information_completeness`

Default scoring should prioritize:

- Location
- Budget
- Move-in compatibility
- Property/room preferences
- Amenities
- Financial terms

Weights may be adjusted based on renter statements such as “location matters more than price.”

### 9.4 Match statuses

At minimum:

- `REJECTED`
- `POSSIBLE_MATCH`
- `STRONG_MATCH`
- `NEEDS_QUALIFICATION`

## 10. Outreach and qualification

### 10.1 First outreach approval

The system should not initiate contact with a new lead until the renter approves.

### 10.2 After approval

The system may autonomously:

- Check availability
- Ask about missing property facts
- Answer factual questions using known renter requirements
- Clarify details
- Follow up once after no response

### 10.3 Escalation

The system must ask the renter when a contact requests a decision outside known boundaries, such as:

- Rent above hard maximum
- New brokerage expectation
- Different move-in terms
- Lock-in decision not previously specified
- Personal information not already authorized
- Any financial commitment

### 10.4 Qualification definition

A property is qualified for a specific renter when:

- Availability is confirmed
- No known hard requirement is violated
- Important required unknowns are resolved
- Core financial terms are sufficiently known

Qualification is search-specific; the system should not ask irrelevant questions merely to fill database fields.

## 11. Scheduling requirements

Scheduling begins only after qualification.

The renter provides general availability when scheduling first becomes necessary.

The agent may negotiate windows with the property contact, but the exact slot must be confirmed by the renter before final commitment.

Visit states should support:

- Proposed
- Awaiting renter confirmation
- Confirmed
- Cancelled
- Completed

Basic rescheduling should be supported.

## 12. Media requirements

Two categories of images exist:

### Information-bearing images

Processed by the LLM because they contain facts to extract.

### Property photos

Stored and shown to renters, but not analyzed in V0.

## 13. Success metrics

Primary product metric:

> Number of qualified property visits generated from active searches.

Useful supporting metrics:

- Listings ingested
- Extraction approval rate
- Strong matches created
- Outreach approvals
- Contact response rate
- Listings qualified
- Visits proposed
- Visits confirmed
- Search-to-visit conversion

## 14. Acceptance criteria for V0

V0 is ready for internal use when:

- Admin can ingest and approve real Hyderabad listings from Telegram.
- Renter requirements can be captured naturally and persisted.
- Approved listings are matched against active searches.
- Match explanations identify positive factors and missing information.
- A renter can approve outreach.
- A contact reply can update property facts.
- The workflow can continue asking relevant missing questions.
- A qualified property can progress into visit scheduling.
- The renter must confirm the final visit slot before commitment.
