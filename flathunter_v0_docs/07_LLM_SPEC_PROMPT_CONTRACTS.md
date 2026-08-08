# FlatHunter V0 — LLM Specification & Prompt Contracts

## 1. LLM role in the system

The LLM is used where input or output is naturally unstructured.

The LLM should **not** own workflow state, scoring math, persistence, or hard business rules.

Core rule:

> Code decides what should happen. The LLM understands unstructured information and expresses actions naturally.

## 2. V0 provider strategy

Use a small provider interface and one initial implementation.

Suggested interface:

```python
class LLMProvider(Protocol):
    async def generate_text(self, *, task: str, prompt: str) -> str: ...
    async def generate_structured(
        self,
        *,
        task: str,
        prompt: str,
        response_model: type[BaseModel],
        images: list[bytes] | None = None,
    ) -> BaseModel: ...
```

Initial provider: Gemini.

Provider routing/fallback is deferred.

## 3. General LLM contract rules

Every structured task should:

1. Use an explicit Pydantic response model.
2. Distinguish explicit facts from unknowns.
3. Avoid inventing missing values.
4. Preserve unexpected useful information in flexible fields.
5. Return conflict information when sources disagree.
6. Avoid making business decisions the application should make.
7. Be validated before persistence.

If structured validation fails:

- Retry once with validation feedback where appropriate.
- Otherwise mark the task/draft for review.

## 4. Contract A — renter requirement extraction

### Purpose

Convert renter natural language into structured search requirements and updates.

### Input

- Current requirement draft, if any
- Latest renter message(s)
- Hyderabad V0 context

### Required output shape

```python
class PreferenceValue(BaseModel):
    value: Any
    importance: Literal["REQUIRED", "PREFERRED", "DOES_NOT_MATTER"]

class RequirementExtraction(BaseModel):
    listing_types: list[Literal["ENTIRE_PROPERTY", "PRIVATE_ROOM", "SHARED_ROOM"]] | None
    preferred_locations: list[str]
    acceptable_locations: list[str]
    excluded_locations: list[str]
    work_location: str | None
    target_rent: int | None
    max_rent: int | None
    preferred_move_in_date: date | None
    latest_move_in_date: date | None
    preferred_property_configurations: list[str]
    core_preferences: dict[str, PreferenceValue]
    additional_preferences: dict[str, Any]
    inferred_priority_notes: list[str]
    missing_required_inputs: list[str]
```

### Important behavior

- “Around 20k, max 23k” -> target 20000, max 23000.
- “Gachibowli/Kondapur preferred; Madhapur okay” -> preferred vs acceptable split.
- “Attached bathroom compulsory” -> `REQUIRED`.
- “Furnished would be nice” -> `PREFERRED`.
- Do not force user to specify every optional preference.

### The LLM must not

- Start/pause/close the search
- Decide a listing match
- Convert uncertainty into hard facts

## 5. Contract B — requirement update/patch

### Purpose

Apply conversational changes to an existing requirement record.

Example:

> Increase max budget to 25k and add Nanakramguda.

### Output

Return a patch, not a full replacement where possible.

```python
class RequirementPatch(BaseModel):
    set_fields: dict[str, Any]
    add_items: dict[str, list[Any]]
    remove_items: dict[str, list[Any]]
    notes: list[str]
```

Application code validates that resulting requirements remain internally consistent.

## 6. Contract C — listing extraction

### Purpose

Convert one property input group into canonical listing facts, contacts, conflicts, and flexible context.

### Inputs

May include:

- Information screenshots
- Copied text
- Chat screenshots
- Admin notes

Property gallery photos are **not** inputs for this contract in V0.

### Output structure

```python
class ChannelExtraction(BaseModel):
    type: Literal["WHATSAPP", "EMAIL", "PHONE", "TELEGRAM"]
    value: str
    explicit: bool

class ContactExtraction(BaseModel):
    name: str | None
    role: Literal["OWNER", "BROKER", "CURRENT_TENANT", "UNKNOWN"]
    channels: list[ChannelExtraction]

class ConflictItem(BaseModel):
    field: str
    values: list[Any]
    evidence_summary: list[str]
    critical: bool

class CanonicalListingExtraction(BaseModel):
    listing_type: Literal["ENTIRE_PROPERTY", "PRIVATE_ROOM", "SHARED_ROOM"] | None
    city: str | None
    locality: str | None
    location_text: str | None
    landmark: str | None
    property_configuration: str | None
    room_occupancy: str | None
    rent: int | None
    maintenance: int | None
    deposit: int | None
    brokerage: int | None
    available_from: date | None
    furnishing: str | None
    attached_bathroom: bool | None
    car_parking: bool | None
    bike_parking: bool | None
    balcony: bool | None
    pets_allowed: bool | None
    power_backup: bool | None
    gated_community: bool | None

class ListingExtraction(BaseModel):
    content_type: Literal["PROPERTY_LISTING", "RENTER_REQUIREMENT", "UNKNOWN"]
    canonical: CanonicalListingExtraction
    contacts: list[ContactExtraction]
    additional_attributes: dict[str, Any]
    conflicts: list[ConflictItem]
    extraction_notes: list[str]
```

### Extraction policy

- Extract as much renter-relevant information as possible.
- Unexpected useful facts belong in `additional_attributes`.
- Missing facts remain `null`.
- Do not infer WhatsApp merely from the presence of a phone number unless explicit evidence exists.
- Preserve distinctions such as total-flat rent vs room rent.
- Detect whether the content is supply or another renter's demand.

## 7. Contract D — admin correction parsing

### Purpose

Turn admin natural-language corrections into a controlled patch to a listing draft.

Example:

> Deposit is 50k, not 44k, and parking is available.

Output:

```python
class ListingDraftPatch(BaseModel):
    canonical_updates: dict[str, Any]
    context_updates: dict[str, Any]
    conflict_resolutions: dict[str, Any]
    notes: list[str]
```

Application code applies only allowed fields.

## 8. Contract E — contradiction detection

Listing extraction may already emit conflicts, but a deterministic/LLM-assisted validation pass may focus on critical canonical contradictions.

Critical fields include:

- Listing type
- Locality
- Rent
- Deposit
- Brokerage
- Available-from date
- Property configuration
- Contact number/email

The model should not force a resolution when evidence is genuinely contradictory.

## 9. Contract F — contact reply parsing

### Purpose

Extract factual updates and requests from owner/broker/current-tenant replies.

Example input:

> Yes available. Deposit is 60k, no brokerage. Car parking isn't there though. Can they move in by Aug 25?

Output:

```python
class ReplyFact(BaseModel):
    field: str
    value: Any
    explicit: bool = True

class ContactReplyParse(BaseModel):
    facts: list[ReplyFact]
    availability_confirmed: bool | None
    questions_for_renter: list[str]
    proposed_times: list[datetime]
    intent: str | None
    ambiguity_notes: list[str]
```

### Important behavior

- Extract explicit facts only.
- Separate factual updates from questions/requests requiring renter decisions.
- Do not decide whether a compromise is acceptable.

## 10. Contract G — next-message generation

### Purpose

Convert an application-decided action into natural language.

Application input example:

```json
{
  "action": "ASK_MISSING_FIELDS",
  "fields": ["deposit", "brokerage"],
  "context": {
    "property": "private room in Kondapur"
  }
}
```

LLM output:

> Great, thanks. Could you also confirm the security deposit and whether there is any brokerage?

### Rules

- Do not add questions not requested by application logic.
- Do not claim facts not supplied.
- Keep messages concise and human.
- Be transparent that the message is sent by an automated assistant if product policy requires it.

## 11. Contract H — soft preference context evaluation

### Purpose

Compare free-form renter preferences with listing `extracted_context`.

Output:

```python
class SoftContextEvaluation(BaseModel):
    matched: list[str]
    conflicted: list[str]
    unknown: list[str]
    evidence: list[str]
```

This output may influence only the configured soft-preference part of scoring.

It must not mutate canonical fields.

## 12. Contract I — renter availability parsing

### Example

> Weekdays after 7, Saturday after 11, Sunday anytime.

Output:

```python
class AvailabilityWindow(BaseModel):
    days: list[str]
    start: time
    end: time | None

class AvailabilityParse(BaseModel):
    timezone: str
    windows: list[AvailabilityWindow]
    one_off_constraints: list[dict[str, Any]]
```

Default timezone in Hyderabad V0: `Asia/Kolkata` unless explicitly different.

## 13. Contract J — proposed time parsing

### Example

> Saturday 11, 1 or 5 works. Sunday afternoon also okay.

Return normalized candidate slots/windows with timezone.

Application code determines which options actually fit renter availability.

## 14. Prompt development guidelines

Prompts should include:

- Task purpose
- Canonical schema definition
- Unknown-value rule
- No-invention rule
- Explicit examples for room vs whole-flat ambiguity
- Hyderabad context where useful
- Instruction to preserve unexpected attributes
- Instruction to report conflicts

Avoid giant universal prompts. Each contract should be task-specific.

## 15. Model observability

Record for each model call:

- Provider
- Model
- Task name
- Success/failure
- Latency
- Token usage if available
- Referenced entity IDs

Do not require raw full prompt storage if it creates unnecessary sensitivity; development logging can be configurable.

## 16. Evaluation requirement

Any change to listing extraction or requirement extraction prompts should be tested against the curated Hyderabad evaluation set before being accepted.
