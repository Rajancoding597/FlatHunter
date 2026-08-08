# FlatHunter V0 — Database Design

## 1. Database principles

Use Supabase PostgreSQL as the system of record.

Important design rules:

- Canonical fields used by deterministic logic are normal relational columns.
- Flexible, unexpected listing information is preserved in JSONB.
- Raw source references are retained.
- Unknown values use `NULL`, not `false`, unless the source explicitly establishes false.
- Logical conversations are independent of communication channel.
- Search-specific match/qualification information should not be stored directly on listings.

## 2. Suggested enums

### user_role

- `RENTER`
- `ADMIN`

### search_status

- `ACTIVE`
- `PAUSED`
- `CLOSED`

### listing_type

- `ENTIRE_PROPERTY`
- `PRIVATE_ROOM`
- `SHARED_ROOM`

### content_type

- `PROPERTY_LISTING`
- `RENTER_REQUIREMENT`
- `UNKNOWN`

### availability_status

- `UNKNOWN`
- `AVAILABLE`
- `UNAVAILABLE`
- `STALE`

### preference_importance

- `REQUIRED`
- `PREFERRED`
- `DOES_NOT_MATTER`

### match_status

- `REJECTED`
- `POSSIBLE_MATCH`
- `STRONG_MATCH`
- `NEEDS_QUALIFICATION`
- `QUALIFIED`
- `SKIPPED`

### conversation_status

- `APPROVED_FOR_CONTACT`
- `CONTACTED`
- `AWAITING_REPLY`
- `QUALIFYING`
- `ESCALATED_TO_RENTER`
- `QUALIFIED`
- `READY_FOR_SCHEDULING`
- `NO_RESPONSE`
- `CLOSED`

### visit_status

- `PROPOSED`
- `AWAITING_RENTER_CONFIRMATION`
- `CONFIRMED`
- `CANCELLED`
- `COMPLETED`

### contact_channel_type

- `WHATSAPP`
- `EMAIL`
- `PHONE`
- `TELEGRAM`

### ingestion_status

- `COLLECTING_INFO`
- `EXTRACTING`
- `NEEDS_REVIEW`
- `COLLECTING_MEDIA`
- `READY_FOR_APPROVAL`
- `APPROVED`
- `REJECTED`
- `FAILED`

## 3. Tables

## 3.1 users

Suggested fields:

```text
id UUID PK
telegram_user_id BIGINT UNIQUE NOT NULL
telegram_username TEXT NULL
display_name TEXT NULL
role user_role NOT NULL DEFAULT RENTER
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

V0 rule: one active search maximum per user, enforced in application logic and optionally by partial unique index.

## 3.2 search_sessions

```text
id UUID PK
user_id UUID FK users
status search_status NOT NULL
city TEXT NOT NULL DEFAULT 'Hyderabad'
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
started_at TIMESTAMPTZ NULL
paused_at TIMESTAMPTZ NULL
closed_at TIMESTAMPTZ NULL
```

## 3.3 search_requirements

One row per search is sufficient for V0.

```text
id UUID PK
search_id UUID UNIQUE FK search_sessions
listing_types listing_type[] NOT NULL
preferred_locations TEXT[] NOT NULL
acceptable_locations TEXT[] NOT NULL DEFAULT '{}'
excluded_locations TEXT[] NOT NULL DEFAULT '{}'
work_location TEXT NULL
target_rent INTEGER NOT NULL
max_rent INTEGER NOT NULL
preferred_move_in_date DATE NULL
latest_move_in_date DATE NULL
preferred_property_configurations TEXT[] NULL
core_preferences JSONB NOT NULL DEFAULT '{}'
additional_preferences JSONB NOT NULL DEFAULT '{}'
scoring_weights JSONB NOT NULL DEFAULT '{}'
raw_requirement_text TEXT NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

`core_preferences` may contain canonical preference values and importance, for example:

```json
{
  "attached_bathroom": {"value": true, "importance": "REQUIRED"},
  "furnishing": {"value": "FULLY_FURNISHED", "importance": "PREFERRED"},
  "car_parking": {"value": true, "importance": "DOES_NOT_MATTER"},
  "max_deposit": {"value": 60000, "importance": "REQUIRED"},
  "brokerage": {"value": "AVOID", "importance": "PREFERRED"}
}
```

`additional_preferences` preserves free-form requirements not yet promoted to canonical fields.

## 3.4 renter_availability

```text
id UUID PK
user_id UUID FK users
search_id UUID NULL FK search_sessions
timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata'
general_windows JSONB NOT NULL
one_off_overrides JSONB NOT NULL DEFAULT '[]'
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Example `general_windows`:

```json
[
  {"days":["MON","TUE","WED","THU","FRI"],"start":"19:00","end":"22:00"},
  {"days":["SAT"],"start":"11:00","end":"20:00"}
]
```

## 3.5 ingestion_sessions

```text
id UUID PK
admin_user_id UUID FK users
mode TEXT NOT NULL -- SINGLE or BULK
status ingestion_status NOT NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
completed_at TIMESTAMPTZ NULL
```

## 3.6 ingestion_inputs

```text
id UUID PK
ingestion_session_id UUID FK ingestion_sessions
group_key TEXT NULL -- useful for bulk item grouping
input_type TEXT NOT NULL -- TEXT, IMAGE, DOCUMENT, ADMIN_NOTE
telegram_file_id TEXT NULL
telegram_file_unique_id TEXT NULL
text_content TEXT NULL
is_information_bearing BOOLEAN NOT NULL DEFAULT TRUE
sort_order INTEGER NOT NULL DEFAULT 0
created_at TIMESTAMPTZ NOT NULL
```

For single-property mode, all information inputs share one logical group.

For bulk mode, each screenshot should receive its own group key/draft.

## 3.7 listing_drafts

```text
id UUID PK
ingestion_session_id UUID FK ingestion_sessions
group_key TEXT NULL
content_type content_type NOT NULL
canonical_payload JSONB NOT NULL
extracted_context JSONB NOT NULL DEFAULT '{}'
conflicts JSONB NOT NULL DEFAULT '[]'
extraction_status TEXT NOT NULL
model_metadata JSONB NOT NULL DEFAULT '{}'
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Draft data should not participate in renter matching until approved.

## 3.8 listings

Core suggested fields:

```text
id UUID PK
listing_type listing_type NOT NULL
city TEXT NOT NULL
locality TEXT NOT NULL
location_text TEXT NULL
landmark TEXT NULL
property_configuration TEXT NULL
room_occupancy TEXT NULL
rent INTEGER NOT NULL
maintenance INTEGER NULL
deposit INTEGER NULL
brokerage INTEGER NULL
currency TEXT NOT NULL DEFAULT 'INR'
available_from DATE NULL
availability_status availability_status NOT NULL DEFAULT UNKNOWN
last_verified_at TIMESTAMPTZ NULL
furnishing TEXT NULL
attached_bathroom BOOLEAN NULL
car_parking BOOLEAN NULL
bike_parking BOOLEAN NULL
balcony BOOLEAN NULL
pets_allowed BOOLEAN NULL
power_backup BOOLEAN NULL
gated_community BOOLEAN NULL
extracted_context JSONB NOT NULL DEFAULT '{}'
source_summary TEXT NULL
created_from_draft_id UUID NULL FK listing_drafts
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Important semantics:

- `attached_bathroom = NULL` means unknown.
- `attached_bathroom = FALSE` means explicitly absent.
- `brokerage = 0` means explicitly no brokerage.
- `brokerage = NULL` means unknown.

## 3.9 listing_sources

Retain original source references.

```text
id UUID PK
listing_id UUID FK listings
source_type TEXT NOT NULL
telegram_file_id TEXT NULL
telegram_file_unique_id TEXT NULL
raw_text TEXT NULL
source_url TEXT NULL
metadata JSONB NOT NULL DEFAULT '{}'
created_at TIMESTAMPTZ NOT NULL
```

## 3.10 listing_media

For renter-facing property photos.

```text
id UUID PK
listing_id UUID FK listings
telegram_file_id TEXT NOT NULL
telegram_file_unique_id TEXT NULL
media_type TEXT NOT NULL DEFAULT 'PHOTO'
sort_order INTEGER NOT NULL DEFAULT 0
caption TEXT NULL
created_at TIMESTAMPTZ NOT NULL
```

V0 property media is not automatically analyzed.

## 3.11 contacts

```text
id UUID PK
listing_id UUID FK listings
name TEXT NULL
role TEXT NULL -- OWNER/BROKER/CURRENT_TENANT/UNKNOWN
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

## 3.12 contact_channels

```text
id UUID PK
contact_id UUID FK contacts
type contact_channel_type NOT NULL
value TEXT NOT NULL
explicit BOOLEAN NOT NULL DEFAULT FALSE
is_usable BOOLEAN NOT NULL DEFAULT TRUE
metadata JSONB NOT NULL DEFAULT '{}'
created_at TIMESTAMPTZ NOT NULL
```

A phone number should not automatically be recorded as WhatsApp unless explicitly stated or confirmed by a supported resolver.

## 3.13 matches

A listing may be a strong match for one renter and rejected for another.

```text
id UUID PK
search_id UUID FK search_sessions
listing_id UUID FK listings
status match_status NOT NULL
fit_score NUMERIC(5,2) NULL
information_completeness NUMERIC(5,2) NULL
hard_rejection_reasons JSONB NOT NULL DEFAULT '[]'
positive_reasons JSONB NOT NULL DEFAULT '[]'
missing_information JSONB NOT NULL DEFAULT '[]'
soft_context_evaluation JSONB NOT NULL DEFAULT '{}'
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
UNIQUE(search_id, listing_id)
```

## 3.14 conversations

```text
id UUID PK
search_id UUID FK search_sessions
listing_id UUID FK listings
contact_id UUID FK contacts
status conversation_status NOT NULL
active_channel_id UUID NULL FK contact_channels
outreach_approved_at TIMESTAMPTZ NULL
last_message_at TIMESTAMPTZ NULL
follow_up_count INTEGER NOT NULL DEFAULT 0
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

## 3.15 messages

```text
id UUID PK
conversation_id UUID FK conversations
channel_type contact_channel_type NOT NULL
direction TEXT NOT NULL -- INBOUND/OUTBOUND
external_message_id TEXT NULL
text TEXT NOT NULL
raw_payload JSONB NOT NULL DEFAULT '{}'
parsed_facts JSONB NOT NULL DEFAULT '{}'
created_at TIMESTAMPTZ NOT NULL
```

Consider a uniqueness rule on `(channel_type, external_message_id)` when integration provides a stable external ID.

## 3.16 outreach_attempts

```text
id UUID PK
conversation_id UUID FK conversations
contact_channel_id UUID FK contact_channels
attempt_type TEXT NOT NULL -- INITIAL/FOLLOW_UP
status TEXT NOT NULL -- PENDING/SENT/FAILED/NO_RESPONSE
sent_at TIMESTAMPTZ NULL
failure_reason TEXT NULL
created_at TIMESTAMPTZ NOT NULL
```

## 3.17 visits

```text
id UUID PK
search_id UUID FK search_sessions
listing_id UUID FK listings
contact_id UUID FK contacts
status visit_status NOT NULL
proposed_start TIMESTAMPTZ NULL
confirmed_start TIMESTAMPTZ NULL
location_text TEXT NULL
notes TEXT NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
confirmed_at TIMESTAMPTZ NULL
cancelled_at TIMESTAMPTZ NULL
```

## 3.18 agent_jobs

Simple DB-backed work queue.

```text
id UUID PK
job_type TEXT NOT NULL
status TEXT NOT NULL -- PENDING/RUNNING/SUCCEEDED/FAILED
payload JSONB NOT NULL
run_after TIMESTAMPTZ NOT NULL
attempts INTEGER NOT NULL DEFAULT 0
last_error TEXT NULL
locked_at TIMESTAMPTZ NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

## 3.19 model_calls

Useful for development/evaluation.

```text
id UUID PK
provider TEXT NOT NULL
model TEXT NOT NULL
task TEXT NOT NULL
input_reference JSONB NOT NULL DEFAULT '{}'
input_tokens INTEGER NULL
output_tokens INTEGER NULL
latency_ms INTEGER NULL
success BOOLEAN NOT NULL
error TEXT NULL
created_at TIMESTAMPTZ NOT NULL
```

## 4. Relationships summary

```text
users
  -> search_sessions
       -> search_requirements
       -> matches -> listings
       -> conversations -> messages
       -> visits

listings
  -> listing_sources
  -> listing_media
  -> contacts -> contact_channels

admins/users
  -> ingestion_sessions
       -> ingestion_inputs
       -> listing_drafts
            -> approved listing
```

## 5. Index recommendations

At minimum:

- `users.telegram_user_id`
- `search_sessions(user_id, status)`
- `listings(city, locality, listing_type, availability_status)`
- `listings(rent)`
- `matches(search_id, status)`
- `matches(listing_id)`
- `conversations(status, last_message_at)`
- `agent_jobs(status, run_after)`

Later add GIN indexes for JSONB only when query patterns justify them.

## 6. Schema evolution principle

When a flexible attribute becomes important to matching/qualification across many listings, promote it from `extracted_context` into a canonical column and backfill where possible.

Do not attempt to predict every future property field in V0.
