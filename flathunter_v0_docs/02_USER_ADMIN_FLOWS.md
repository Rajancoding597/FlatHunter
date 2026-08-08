# FlatHunter V0 — User & Admin Flows

## 1. Renter onboarding and requirement collection

### Entry

```text
/start
  -> identify Telegram user
  -> create/find renter profile
  -> check for existing active search
```

### New renter

Bot prompt:

> Tell me what you are looking for. You can mention area, budget, flat/room type, move-in date, and anything important to you.

The renter may answer in one or multiple messages.

The LLM extracts structured requirements after each meaningful turn.

### Required information before starting a search

- Listing type
- Preferred/acceptable location(s)
- Target budget + hard max budget
- Move-in timing

If any are missing, the bot asks focused follow-up questions.

### Requirement confirmation

Bot displays a concise summary:

```text
What: Private room
Preferred: Gachibowli, Kondapur
Acceptable: Madhapur
Target: Rs 20,000
Maximum: Rs 23,000
Move-in: around Sep 1
Required: attached bathroom
Preferred: furnished
```

Actions:

- `Start Search`
- `Change Requirements`

### Start search

On confirmation:

- Search state becomes `ACTIVE`
- Existing inventory is evaluated
- Future listings trigger matching automatically

## 2. Search management

### Active search home

Suggested actions:

- `View Requirements`
- `Edit Requirements`
- `View Matches`
- `Pause Search`
- `End Search`

### Edit requirements

Renter may say naturally:

> Increase max budget to 25k and include Nanakramguda.

The LLM parses changes; code updates canonical requirement fields.

After update:

- Re-run relevant matching
- Notify renter if meaningful new matches appear

### Pause

`ACTIVE -> PAUSED`

No new listing matching/outreach should run for the paused search.

### Close

`ACTIVE|PAUSED -> CLOSED`

The system stops monitoring inventory for that search.

## 3. Match presentation

A renter should see why a property is promising.

Example:

```text
Strong match — Kondapur
Private room in 3BHK
Rent: Rs 21,000
Fit: 89/100
Information completeness: 72%

Why it matches:
+ Preferred location
+ Below target budget
+ Available before move-in
+ Fully furnished

Need to confirm:
? Parking
? Brokerage
? Deposit
```

Actions:

- `Contact Them`
- `View Details`
- `Skip`

If property photos exist, send them as a media group or subsequent gallery message.

## 4. Outreach approval

When renter taps `Contact Them`:

1. Check that a usable contact channel exists.
2. Create/activate a property conversation.
3. Select best supported channel according to communication policy.
4. Send first outreach.
5. Move conversation to `AWAITING_REPLY`.

If no usable automated channel exists, the bot may show a prepared message and mark the lead as requiring manual contact.

## 5. Qualification flow

### First question

Normally confirm availability first.

If contact says unavailable:

- Mark listing `UNAVAILABLE`
- Mark match rejected/closed for all active searches as appropriate
- End qualification politely

If available:

- Update availability state
- Determine important missing requirements for this renter

### Ask only relevant gaps

Example gaps:

- Deposit
- Brokerage
- Parking
- Attached bathroom

Questions should be grouped sensibly, not dumped as a long interrogation.

### After every reply

1. Parse reply with LLM into structured facts.
2. Validate structured output.
3. Update canonical listing data where facts are explicit.
4. Re-run hard-constraint checks.
5. If a hard violation is discovered, stop qualification for that renter.
6. Otherwise, determine the next missing fact.

### Escalation

If contact asks something outside stored renter boundaries:

```text
Owner asks for Rs 26k.
Your stated maximum is Rs 23k.

[Offer Rs 23k]
[Continue Anyway]
[Drop Property]
```

## 6. Scheduling flow

### Trigger

Property must be qualified for the renter.

### Renter availability

If no availability has been stored yet, ask:

> When are you generally available for property visits?

Parse natural language into availability windows.

### Contact negotiation

Agent asks for a time window compatible with renter availability.

If contact replies with one or more candidate times:

1. Parse candidate times.
2. Filter against renter availability.
3. Present compatible options to renter.

### Renter confirmation

Only after renter chooses an exact time:

- Confirm with property contact
- Mark visit `CONFIRMED`
- Send renter the visit summary

### Reschedule

Renter may request rescheduling. Agent re-enters slot negotiation and requires new exact confirmation.

### Cancel

Renter requests cancellation -> system confirms intent -> notifies property contact -> visit becomes `CANCELLED`.

## 7. Admin authentication flow

Admin is identified deterministically by Telegram user ID.

Normal renters must not gain admin features by issuing admin commands.

Suggested implementation:

```text
if telegram_user_id in ADMIN_TELEGRAM_IDS:
    allow admin routes
else:
    deny/ignore admin routes
```

This may later move to a database role field.

## 8. Admin home flow

`/admin`

Suggested actions:

- `Add Listing`
- `Bulk Add`
- `Review Queue`
- `Recent Listings`
- `Failed Extractions`
- `Inventory Stats`

## 9. Admin single-property ingestion

### Start

`/addlisting`

Create ingestion session with state `COLLECTING_INFO`.

Bot:

> Send anything I should read to understand this property: screenshots, copied text, chat screenshots, documents, or notes. Send /doneinfo when finished.

### Information collection

All incoming information-bearing inputs belong to the same property draft.

Admin may add corrections such as:

> Rent is actually 24k, not 23k.

Admin corrections should receive highest authority for canonical facts.

### Finish information

`/doneinfo`

System:

1. Sends information-bearing inputs to extraction pipeline.
2. Produces canonical fields + flexible context.
3. Detects critical contradictions.
4. Escalates contradictions if needed.
5. Presents draft summary.

### Property photo stage

Bot:

> Do you have property photos to attach? These will be stored for renters and will not be analyzed in V0.

Actions:

- `Upload Photos`
- `Skip Photos`

If uploading, subsequent image messages are stored as property media, not sent to the LLM.

### Review

Show extracted draft.

Actions:

- `Approve & Save`
- `Edit`
- `Reject`

### Edit

Admin may correct conversationally:

> Deposit is 50k and car parking is available.

The LLM converts that into a patch. Code validates and applies the patch to the draft.

### Approval

`EXTRACTED_DRAFT -> APPROVED`

Then:

- Create listing rows
- Create contact rows/channels
- Create media rows
- Trigger `LISTING_CREATED`
- Evaluate against active searches

## 10. Admin bulk ingestion

### Start

`/bulkadd`

Rule:

> One uploaded information screenshot = one property draft.

### Processing

Each screenshot is independently extracted.

The bot reports totals:

```text
10 processed
8 ready for review
1 needs clarification
1 failed
```

### Review

Review each property one by one during development.

Actions:

- `Approve`
- `Edit`
- `Reject`

### Multi-property screenshot edge case

If one screenshot clearly contains multiple distinct properties, do not silently split unless product logic explicitly permits it.

Preferred admin prompt:

```text
This screenshot appears to contain 3 properties.
[Create 3 Drafts]
[Treat as One]
[Skip]
```

## 11. Admin listing maintenance

Admin should be able to retrieve a listing by ID or recent-list view and:

- Mark available
- Mark unavailable
- Edit canonical details
- Add property photos
- View raw source
- Review current contacts

A conversational update such as:

> P123 rent changed to 23k.

may be supported if listing identity is unambiguous.

## 12. Error flows

### Extraction failure

- Keep ingestion session/draft for review
- Do not create production listing row
- Offer retry or manual edit

### Invalid structured LLM output

- Retry once if safe
- If still invalid, mark `NEEDS_REVIEW`

### Duplicate-looking listing

V0 may optionally warn based on simple deterministic signals such as same phone + same locality + similar rent. Do not block creation automatically.
