# FlatHunter V0 — Testing & LLM Evaluation Plan

## 1. Goals

Testing should answer two different questions:

1. Is the deterministic application logic correct?
2. Are LLM extraction/interpretation contracts reliable enough on real Hyderabad rental data?

These need different test strategies.

## 2. Test layers

### Unit tests

Pure logic:

- Hard constraints
- Scoring
- Completeness
- Missing-information generation
- State transitions
- Availability compatibility
- Channel selection

### Integration tests

Boundaries:

- Supabase repositories
- Telegram handler -> service flow
- LLM provider structured call
- Email adapter
- Background worker job execution

### End-to-end tests

Representative renter/admin journeys.

### LLM evaluation tests

Curated screenshot/text fixtures with expected structured results.

## 3. Matching unit test matrix

At minimum test:

### Listing type

- Exact type -> survives
- Wrong type -> reject

### Budget

- Below target -> full budget score
- Between target/max -> partial score
- Above max -> reject
- Unknown maintenance -> not assumed zero

### Location

- Preferred -> high score
- Acceptable -> partial score
- Excluded -> reject

### Required boolean preference

- true -> survives
- false -> reject
- null -> needs qualification, not reject

### Preferred boolean preference

- true -> score benefit
- false -> score loss only
- null -> no false assumption; completeness reduction

### Move-in

- compatible -> score
- explicit hard deadline violation -> reject
- unknown -> completeness gap

## 4. Information completeness tests

Verify that:

- Unknown required facts reduce completeness.
- Unknown facts do not automatically reduce fit.
- Irrelevant optional fields do not affect completeness.
- Required fields can receive higher information weights.

## 5. State transition tests

Test valid and invalid transitions for:

- Search
- Ingestion
- Listing availability
- Conversation
- Visit
- Background job

Examples:

- Cannot confirm a cancelled visit without rescheduling flow.
- Re-approving an approved draft must be idempotent.
- A closed search should not receive new-listing matching.

## 6. Telegram flow tests

Use handler/service tests for:

- New renter `/start`
- Requirement follow-up
- Start search
- Admin authorization
- Single ingestion session grouping
- Bulk one-image-per-property grouping
- `/doneinfo`
- Admin correction
- Approve/reject
- Match `Contact Them`
- Visit confirmation callback

## 7. Hyderabad LLM evaluation dataset

Create a repository dataset:

```text
samples/
  listings/
    listing_001.png
    listing_002.png
    listing_003.txt
    ...
  expected/
    listing_001.json
    listing_002.json
    listing_003.json
```

Start with 20-30 diverse examples, then grow toward 100+.

## 8. Listing dataset diversity

Include:

- Entire flat
- Private room/replacement
- Shared room
- Renter-demand post that must be rejected from inventory
- Rent + maintenance separated
- Deposit/brokerage variations
- Phone-only contact
- Explicit WhatsApp contact
- Email contact
- Multiple contact numbers
- Furnishing details
- Attached bathroom explicit yes/no/unknown
- Parking explicit yes/no/unknown
- Multiple screenshots for one property
- Admin correction overriding source
- Contradictory rent/deposit
- Hyderabad locality abbreviations/landmarks
- Noisy spelling/grammar
- Posts mixing English and local/common shorthand where encountered

## 9. Expected extraction evaluation

For canonical fields, compare expected vs actual.

Metrics:

- Exact match accuracy by field
- Null/unknown correctness
- Listing-type accuracy
- Rent accuracy
- Contact-channel accuracy
- Content-type accuracy
- Conflict-detection accuracy

Do not use one overall score only; field-level failures matter differently.

## 10. High-severity extraction errors

Treat these as especially important:

- Whole-flat rent mistaken for room rent
- Room rent mistaken for full-flat rent
- Wrong listing type
- Wrong locality
- Rent digit/order-of-magnitude error
- Wrong contact number/email
- Inventing WhatsApp from a phone-only listing
- Treating unknown required feature as false
- Missing a clear contradiction

## 11. Requirement extraction dataset

Create renter utterance fixtures.

Examples:

> Gachibowli or Kondapur, Madhapur is okay, 20k ideal but 23 max, attached bath must.

> Need 2BHK for me and my friend, under 45k, anywhere close to Financial District.

> I care more about commute than rent and can stretch for the right place.

Expected fields should include:

- listing type
- location buckets
- budget target/max
- move-in timing
- preference importance
- priority notes/weights

## 12. Reply parsing evaluation

Create contact replies such as:

> Available, deposit 60k, zero brokerage, no car parking.

Expected explicit facts must be correct.

Also test questions:

> Can they move in on Aug 20 instead?

This should be recognized as a renter decision request, not automatically accepted.

## 13. Scheduling parsing evaluation

Examples:

> Saturday 11, 1 or 5 works.

> Sunday afternoon only.

> Tomorrow after 7.

Expected output must use correct timezone/date context.

## 14. Prompt regression process

When changing a prompt:

1. Run current evaluation set.
2. Compare field-level regressions.
3. Inspect high-severity errors manually.
4. Accept change only if it improves or preserves important metrics.
5. Add new failure examples to dataset.

## 15. Model-provider comparison

Provider/model benchmarking is not needed before V0 works.

When comparing later:

- Use the same evaluation fixtures.
- Compare accuracy first.
- Then latency/cost/rate limits.
- Do not switch based only on anecdotal examples.

## 16. End-to-end scenarios

### Scenario A — successful private-room match

- Renter creates search
- Admin adds listing
- Strong match appears
- Outreach approved
- Contact confirms facts
- Qualified
- Visit confirmed

### Scenario B — hard rejection during qualification

- Parking initially unknown
- Renter requires parking
- Contact says no parking
- Match becomes rejected
- No more unnecessary questions

### Scenario C — unavailable listing

- Strong match
- Contact says already rented
- Listing becomes unavailable
- Conversation closes

### Scenario D — escalation

- User max 23k
- Owner asks 25k
- Bot escalates
- No automatic acceptance

### Scenario E — stale revalidation

- Old listing is attractive
- Availability rechecked before presenting as actionable

## 17. Manual QA checklist before demo

- Bot restarts without losing durable state
- Admin-only commands are protected
- Duplicate callbacks do not duplicate writes
- Unknown values display as unknown
- Photos are not accidentally sent to extraction model
- New approved listing triggers matching
- Match reasons are understandable
- No unsupported promises are made in outreach
- Visit cannot become confirmed without renter approval
