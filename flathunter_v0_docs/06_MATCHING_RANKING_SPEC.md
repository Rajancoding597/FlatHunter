# FlatHunter V0 — Matching & Ranking Specification

## 1. Purpose

The matching engine should identify which listings are worth renter attention and qualification. It should be deterministic, explainable, and robust to incomplete listings.

## 2. Matching pipeline

```text
Listing + Search Requirements
        |
        v
Availability eligibility
        |
        v
Hard-constraint evaluation
        |
        +-- explicit violation -> REJECTED
        |
        v
Deterministic core scoring
        |
        v
Information completeness calculation
        |
        v
Optional flexible-context reasoning
        |
        v
Match classification + reasons + gaps
```

## 3. Hard constraints

A hard constraint may reject only on explicit evidence.

Examples:

- Desired listing type is `PRIVATE_ROOM`, listing is `ENTIRE_PROPERTY`
- Listing rent exceeds renter hard maximum
- Listing locality is explicitly excluded
- Required car parking is explicitly `FALSE`
- Latest acceptable move-in date is before listing availability
- Listing is `UNAVAILABLE`

Unknown fields must not reject.

## 4. Default scoring categories

Initial default weights:

| Category | Weight |
|---|---:|
| Location fit | 30 |
| Budget fit | 25 |
| Move-in compatibility | 15 |
| Property/room preference fit | 10 |
| Amenities/preferences | 10 |
| Financial terms | 10 |
| **Total** | **100** |

These weights are defaults, not immutable rules.

## 5. Personalized weights

The requirement parser may infer high-level importance statements from renter language.

Example:

> I would rather pay 3k more than live far from Gachibowli.

Possible normalized weights:

```json
{
  "location": 40,
  "budget": 15,
  "move_in": 15,
  "property": 10,
  "amenities": 10,
  "financial_terms": 10
}
```

Rules:

- Weights must sum to 100 after normalization.
- LLM may propose weights/importance; deterministic code validates/clamps them.
- Do not allow soft factors to overwhelm hard constraints.

## 6. Location scoring

V0 does not need route-time calculations.

Suggested locality-based scoring within the location bucket:

- Preferred locality -> 100% of location points
- Acceptable locality -> 70% of location points
- Other Hyderabad locality -> configurable low score or 0
- Excluded locality -> hard reject

Later commute intelligence may replace/augment locality buckets.

## 7. Budget scoring

Store both target rent and hard maximum.

Suggested behavior:

- `rent <= target_rent` -> full budget points
- `target_rent < rent <= max_rent` -> gradually decline toward a floor
- `rent > max_rent` -> hard reject

Example linear interpolation:

```text
budget_fraction = 1 - alpha * ((rent - target) / (max - target))
```

Choose a sensible floor at max rent, for example 0.4-0.6 of budget points, then tune from real data.

Do not over-optimize formula in V0; consistency matters more than mathematical sophistication.

### Maintenance

Possible V0 rule:

- Use `effective_monthly_cost = rent + maintenance` when maintenance is known and clearly mandatory.
- If maintenance is unknown, do not assume zero.

## 8. Move-in scoring

Model at least:

- Preferred move-in date
- Latest acceptable move-in date, if renter expresses one

Behavior:

- Explicitly later than hard deadline -> reject
- Near preferred date -> high score
- Slightly earlier/later within tolerance -> small reduction
- Unknown availability date -> no fit penalty, but completeness decreases and qualification gap is added

## 9. Property/room fit

Listing type itself is primarily a gate.

Within surviving listings, property fit may include:

- Preferred property configuration
- Private/shared occupancy details
- Relevant room-type preferences

## 10. Amenities/preferences scoring

Examples:

- Attached bathroom
- Furnishing
- Parking
- Balcony
- Pets

Rules:

- `REQUIRED` + explicit violation -> reject
- `PREFERRED` + satisfied -> add its share of category score
- `PREFERRED` + explicit violation -> lose its share
- Unknown -> do not treat as false; reduce completeness and possibly add qualification gap

## 11. Financial terms scoring

Examples:

- Brokerage
- Deposit

Rules:

- Hard maximum deposit explicitly violated -> reject if marked `REQUIRED`
- “Avoid brokerage” preference may reduce financial score if brokerage exists
- Unknown deposit/brokerage should not automatically reduce fit; it should reduce completeness

## 12. Information completeness

Fit and completeness are separate.

### Fit score

“How attractive does this property appear based on known facts?”

### Information completeness

“How much of the information important to this renter is known?”

Suggested calculation:

1. Build a set of fields relevant to this renter/search.
2. Assign each field an information importance weight.
3. Count explicit known values as complete.
4. Unknown values contribute 0 to completeness.

Example:

```text
Relevant facts:
- availability
- rent
- location
- move-in date
- attached bathroom
- parking
- brokerage
- deposit
```

If 5 of 8 weighted facts are known, completeness may be around 60-70% depending weights.

## 13. Missing information

The engine should emit explicit gaps.

Example:

```json
[
  {"field":"car_parking","importance":"REQUIRED","priority":1},
  {"field":"brokerage","importance":"PREFERRED","priority":2},
  {"field":"deposit","importance":"PREFERRED","priority":2}
]
```

Qualification service consumes this list.

## 14. Flexible-context reasoning

The LLM may evaluate renter free-form preferences against `extracted_context`.

Example renter preference:

> I would love a quiet society with good natural light.

Example context:

```json
{
  "room_description": "east-facing room with lots of sunlight",
  "society": "quiet gated community"
}
```

Expected output should be structured:

```json
{
  "matched": ["natural light", "quiet environment"],
  "conflicted": [],
  "unknown": []
}
```

Rules:

- Flexible-context reasoning may affect only a limited soft-preference component.
- It may not override canonical hard facts.
- It may not convert weak inference into canonical truth.

## 15. Match classification

Initial conceptual rules:

### `REJECTED`

Any hard violation.

### `NEEDS_QUALIFICATION`

Promising fit, but important required facts remain unknown or listing is stale/needs availability confirmation.

### `STRONG_MATCH`

High fit, no hard violations, enough information known to justify renter attention/outreach.

### `POSSIBLE_MATCH`

Survives hard constraints but score is moderate.

Exact thresholds should be configuration values and tuned with real Hyderabad data.

Starting example only:

```text
< 70     POSSIBLE_MATCH or ignore from proactive alerts
70-84    POSSIBLE_MATCH
85+      STRONG_MATCH if sufficiently complete
85+ with important unknowns -> NEEDS_QUALIFICATION
```

## 16. Explanations

Do not show only a score.

Store reason codes and render user-facing explanations such as:

```text
+ Preferred location
+ Rs 2,000 below target budget
+ Available before preferred move-in
+ Fully furnished
? Parking not confirmed
? Deposit not mentioned
```

Prefer deterministic reason generation for canonical facts. LLM may polish wording if desired.

## 17. Re-evaluation triggers

Re-run matching when:

- New listing is approved
- Search starts
- Search requirements change
- Contact reply changes canonical listing facts
- Availability status changes
- Admin edits canonical listing data

Use idempotent upsert on `(search_id, listing_id)`.
