# FlatHunter V0 — Telegram Bot UX Specification

## 1. UX principles

- Conversation first, forms second.
- Ask only for information needed now.
- Use buttons for irreversible/important actions.
- Keep summaries compact.
- Show reasons, not just scores.
- Do not force renters through a long questionnaire.
- Keep admin operations phone-friendly.

## 2. Renter commands

Suggested minimal commands:

- `/start`
- `/search` — show current search/home
- `/requirements` — show current requirements
- `/matches` — show current matches
- `/help`

Most renter interaction should happen through buttons and natural language rather than memorized commands.

## 3. Admin commands

- `/admin`
- `/addlisting`
- `/bulkadd`
- `/doneinfo`
- `/done`
- `/cancel`

Optional later:

- `/listing <id>`
- `/inventory`
- `/review`

## 4. Renter onboarding screen

Example:

```text
Hi! I can help you find a rental in Hyderabad.

Tell me what you're looking for in your own words. Mention things like area, budget, room/flat type, move-in date, and anything important to you.
```

## 5. Requirement follow-ups

If listing type is missing:

```text
Are you looking for:
[Entire Flat]
[Private Room]
[Shared Room]
```

If budget is missing:

```text
What rent are you comfortable with, and what's the most you'd pay for a really good match?
```

If location is missing:

```text
Which Hyderabad areas do you prefer? You can also mention areas that are acceptable but not ideal.
```

Avoid asking about parking, balcony, pets, etc. unless the renter mentions them or the bot offers one optional enrichment question.

## 6. Requirement confirmation card

```text
Your search

Type: Private room
Preferred: Gachibowli, Kondapur
Acceptable: Madhapur
Target rent: Rs 20,000
Maximum rent: Rs 23,000
Move-in: around Sep 1
Required: Attached bathroom
Preferred: Furnished

[Start Search]
[Change Requirements]
```

## 7. Active search home

```text
Search is active.
I'll check newly added listings automatically.

[View Matches]
[Edit Requirements]
[Pause Search]
[End Search]
```

## 8. Match card

```text
Strong match — Kondapur
Private room in 3BHK
Rs 21,000 + Rs 1,000 maintenance

Fit: 89/100
Known info: 72%

+ Preferred location
+ Below target budget
+ Fully furnished
+ Available before move-in
? Deposit unknown
? Parking unknown

[Contact Them]
[View Details]
[Skip]
```

If media exists, send listing photos as an album immediately before/after the details card.

## 9. Outreach escalation card

Example:

```text
The owner is asking Rs 25,000.
Your stated maximum is Rs 23,000.

[Offer Rs 23,000]
[Continue Anyway]
[Drop Property]
```

The application should translate button selection into deterministic workflow actions.

## 10. Scheduling card

```text
This property is qualified and ready for a visit.
Ravi can show it at:

[Sat 12:30 PM]
[Sat 5:00 PM]
[None Work]
```

After renter selects:

```text
I'll confirm Sat 12:30 PM with Ravi.
```

Only after contact confirmation:

```text
Visit confirmed

Sat, Aug 15 — 12:30 PM
Kondapur
Private room in 3BHK
Contact: Ravi

[View Property]
[Reschedule]
[Cancel Visit]
```

## 11. Admin home

`/admin`

```text
FlatHunter Admin

[Add Listing]
[Bulk Add]
[Review Queue]
[Recent Listings]
[Failed Extractions]
[Inventory Stats]
```

## 12. Single-property ingestion UX

### Start

```text
/addlisting
```

Bot:

```text
Send anything I should READ to understand this property:
- listing screenshots
- chat screenshots
- copied text
- documents
- your notes/corrections

All of these will be treated as ONE property.
Send /doneinfo when finished.
```

### Information stage

Admin sends one or more inputs.

The bot may acknowledge count without processing each immediately:

```text
Added 3 information inputs.
Send more, or /doneinfo when finished.
```

### Extraction stage

After `/doneinfo`:

```text
Processing listing information...
```

Then show conflicts if needed.

### Conflict example

```text
I found conflicting rent information:

A: Rs 22,000
B: Rs 24,000

[Use Rs 22,000]
[Use Rs 24,000]
[Enter Manually]
```

### Draft review

```text
Property draft

Type: Private room in 3BHK
Location: Kondapur
Rent: Rs 24,000
Maintenance: Rs 1,000
Deposit: Rs 50,000
Available: Sep 1
Furnishing: Fully furnished
Attached bath: Yes
Car parking: Unknown

Contact:
Ravi — WhatsApp +91...

Extra details:
- Cook available
- Washing machine
- Balcony

[Edit]
[Continue to Photos]
[Reject]
```

### Property photo stage

```text
Upload photos you want renters to see.
These photos will be stored, not analyzed in V0.

Send /done when finished.
[Skip Photos]
```

### Final approval

```text
Ready to save this listing?

[Approve & Save]
[Edit]
[Reject]
```

## 13. Conversational admin edit

After tapping `Edit`:

```text
Tell me what should change.
```

Admin:

> Deposit is 60k, and bike parking is available.

Bot shows the changed fields and returns to review.

## 14. Bulk ingestion UX

`/bulkadd`

```text
Bulk mode: each screenshot will be treated as a DIFFERENT property.
Send screenshots, then /done.
```

After processing:

```text
Bulk import
10 processed
8 ready
1 needs clarification
1 failed

[Review Next]
```

Review card:

```text
Property 3 of 10
Kondapur
Private room in 3BHK
Rs 21,500
Attached bath: Yes
WhatsApp found
Deposit: Unknown

[Approve]
[Edit]
[Reject]
```

## 15. Failed extraction UX

```text
I couldn't confidently extract this listing.

[Retry]
[Edit Manually]
[Reject]
```

## 16. Inventory status UX

Simple admin view:

```text
Inventory

Total: 187
Available: 62
Unknown: 89
Stale: 21
Unavailable: 15
Added today: 14
```

## 17. Listing maintenance UX

A listing detail should allow:

- `Mark Available`
- `Mark Unavailable`
- `Edit`
- `Add Photos`
- `View Raw Source`

## 18. Telegram UX non-goals

Do not build:

- Complex nested forms
- Huge command surface
- Spreadsheet-like editing in chat
- Multi-page webviews for V0
- Fine-grained user settings screens
