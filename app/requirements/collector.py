'''Deterministic, canonical renter requirement collection primitives.'''

from __future__ import annotations

import calendar
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone as datetime_timezone, tzinfo
from enum import StrEnum
from html import escape
from typing import Any, Optional
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

from app.common.enums import ListingType, PreferenceImportance
from app.requirements.schemas import (
    PreferenceValue,
    RequirementChangeOperation,
    RequirementExtractionResponse,
)


class CollectionMode(StrEnum):
    HYBRID = 'HYBRID'
    GUIDED = 'GUIDED'


CONFIGURATION_ANSWERED_MARKER = '__flathunter_configuration_answered'


class CollectionControlIntent(StrEnum):
    SHOW_SUMMARY = 'SHOW_SUMMARY'
    FINISH = 'FINISH'
    CANCEL = 'CANCEL'
    HELP = 'HELP'


class RequirementField(StrEnum):
    RENTAL_ARRANGEMENT = 'listing_types'
    HOME_CONFIGURATIONS = 'preferred_property_configurations'
    PREFERRED_LOCATIONS = 'preferred_locations'
    ACCEPTABLE_LOCATIONS = 'acceptable_locations'
    EXCLUDED_LOCATIONS = 'excluded_locations'
    BUDGET = 'budget'
    MOVE_IN_TIMING = 'move_in_timing'
    WORK_LOCATION = 'work_location'
    CORE_PREFERENCES = 'core_preferences'
    ADDITIONAL_PREFERENCES = 'additional_preferences'


class ConflictResolution(StrEnum):
    USE_PROPOSED = 'USE_PROPOSED'
    KEEP_CURRENT = 'KEEP_CURRENT'
    ADD_PROPOSED = 'ADD_PROPOSED'


class MoveInWindow(BaseModel):
    preferred_move_in_date: date
    latest_move_in_date: Optional[date] = None


class ParsedBudget(BaseModel):
    target_rent: Optional[int] = None
    max_rent: int = Field(gt=0)

    @field_validator('target_rent')
    @classmethod
    def positive_target(cls, value):
        if value is not None and value <= 0:
            raise ValueError('target rent must be positive')
        return value


class RequirementPatchOperation(BaseModel):
    field: RequirementField
    operation: RequirementChangeOperation
    value: Any = None


class RequirementTurnPatch(BaseModel):
    operations: list[RequirementPatchOperation] = Field(default_factory=list)


class PendingRequirementConflict(BaseModel):
    field: RequirementField
    current_value: Any = None
    proposed_value: Any = None
    operation_index: int
    staged_patch: RequirementTurnPatch
    reason: str
    confirmed_operation_indexes: list[int] = Field(default_factory=list)


class RenterRequirementDraft(BaseModel):
    listing_types: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    acceptable_locations: list[str] = Field(default_factory=list)
    excluded_locations: list[str] = Field(default_factory=list)
    work_location: Optional[str] = None
    target_rent: Optional[int] = None
    max_rent: Optional[int] = None
    preferred_move_in_date: Optional[date] = None
    latest_move_in_date: Optional[date] = None
    preferred_property_configurations: list[str] = Field(default_factory=list)
    configuration_answered: bool = False
    core_preferences: dict[str, PreferenceValue] = Field(default_factory=dict)
    additional_preferences: dict[str, str] = Field(default_factory=dict)

    @field_validator(
        'listing_types',
        'preferred_locations',
        'acceptable_locations',
        'excluded_locations',
        'preferred_property_configurations',
        mode='before',
    )
    @classmethod
    def list_defaults(cls, value):
        return list(value or [])

    @field_validator('core_preferences', mode='before')
    @classmethod
    def preference_defaults(cls, value):
        return dict(value or {})

    @classmethod
    def from_requirements(
        cls,
        requirements: RequirementExtractionResponse | dict | None,
    ) -> 'RenterRequirementDraft':
        if requirements is None:
            return cls()
        if hasattr(requirements, 'model_dump'):
            values = requirements.model_dump(mode='json')
        else:
            values = dict(requirements)
        allowed = set(cls.model_fields)
        filtered = {key: value for key, value in values.items() if key in allowed}
        additional = dict(filtered.get('additional_preferences') or {})
        marker_answered = str(
            additional.pop(CONFIGURATION_ANSWERED_MARKER, '')
        ).casefold() == 'true'
        filtered['additional_preferences'] = additional
        if 'configuration_answered' not in filtered:
            configurations = filtered.get('preferred_property_configurations') or []
            listing_types = filtered.get('listing_types') or []
            filtered['configuration_answered'] = marker_answered or bool(configurations) or (
                bool(listing_types)
                and ListingType.ENTIRE_PROPERTY.value not in listing_types
            )
        return cls(**filtered)

    def missing_required_fields(self) -> list[RequirementField]:
        return missing_required_fields(self)

    def to_requirement_dict(self) -> dict[str, Any]:
        values = self.model_dump(
            mode='json',
            exclude={'configuration_answered'},
        )
        additional = dict(values.get('additional_preferences') or {})
        if self.configuration_answered:
            additional[CONFIGURATION_ANSWERED_MARKER] = 'true'
        else:
            additional.pop(CONFIGURATION_ANSWERED_MARKER, None)
        values['additional_preferences'] = additional
        return values

    def to_extraction_response(self) -> RequirementExtractionResponse:
        values = self.to_requirement_dict()
        complete = not self.missing_required_fields()
        return RequirementExtractionResponse(
            is_complete=complete,
            follow_up_question=None,
            conversational_summary=None,
            **values,
        )


class RequirementMergeResult(BaseModel):
    draft: RenterRequirementDraft
    changed_fields: list[RequirementField] = Field(default_factory=list)
    pending_conflict: Optional[PendingRequirementConflict] = None

    @property
    def made_progress(self) -> bool:
        return bool(self.changed_fields)


class CollectionProgress(BaseModel):
    mode: CollectionMode = CollectionMode.HYBRID
    no_progress_count: int = 0
    field_failure_count: int = 0
    last_signature: Optional[str] = None
    last_prompt: Optional[str] = None


REQUIRED_PROMPTS = {
    RequirementField.RENTAL_ARRANGEMENT: (
        'Are you looking for an entire flat/property, a private room, or a shared room?'
    ),
    RequirementField.HOME_CONFIGURATIONS: (
        'Which home configurations work for you, such as 1BHK, 2BHK, or Any?'
    ),
    RequirementField.PREFERRED_LOCATIONS: (
        'Which Hyderabad areas would you prefer? You can list more than one.'
    ),
    RequirementField.BUDGET: (
        'What is the maximum monthly rent you are comfortable with?'
    ),
    RequirementField.MOVE_IN_TIMING: (
        'When would you like to move in?'
    ),
}


_TERMINAL_FINISH_RE = re.compile(
    r"(?:[,;]?\s*(?:and\s+)?(?:"
    r"that(?:'|\u2019)?s\s+(?:all|it)|that\s+is\s+(?:all|it)|"
    r"nothing\s+else|all\s+good|skip\s+preferences|"
    r"start\s+searching|search\s+now|"
    r"do(?:n't|\s+not)\s+ask\s+anything\s+else"
    r"))[\s.!?]*$",
    flags=re.IGNORECASE,
)

_TERMINAL_SUMMARY_RE = re.compile(
    r'(?:[,;]?\s*(?:and\s+)?(?:'
    r'show(?:\s+me)?\s+(?:everything|what\s+you\s+have|my\s+requirements?)|'
    r'give\s+me\s+(?:my\s+)?(?:updated\s+|final\s+)?requirements?'
    r'))[\s.!?]*$',
    flags=re.IGNORECASE,
)


def split_terminal_finish_phrase(text: str) -> tuple[str, bool]:
    """Separate a terminal start/done instruction from preceding facts."""
    normalized = _normalize(text)
    if normalized in {
        'done', 'finish', 'start', 'start searching', 'search now',
        'thats all', 'that is all', 'thats it', 'that is it',
        'nothing else', 'all good', 'skip', 'skip preferences',
        'no', 'no thanks',
    }:
        return '', True
    match = _TERMINAL_FINISH_RE.search(text or '')
    if not match:
        return (text or '').strip(), False
    return (text or '')[:match.start()].rstrip(' ,;.-'), True


def split_terminal_summary_phrase(text: str) -> tuple[str, bool]:
    '''Separate a terminal summary request from preceding requirement facts.'''
    match = _TERMINAL_SUMMARY_RE.search(text or '')
    if not match:
        return (text or '').strip(), False
    return (text or '')[:match.start()].rstrip(' ,;.-'), True


def strip_routed_non_requirement_clauses(text: str) -> str:
    '''Remove deterministic bot-control clauses before slot parsing.'''
    cleaned = text or ''
    patterns = (
        r'\bshow(?:\s+me)?\s+(?:everything|what\s+you\s+have|my\s+requirements?)\b',
        r'\bgive\s+me\s+(?:my\s+)?(?:updated\s+|final\s+)?requirements?\b',
        r'\b(?:pause|resume|unpause)\s+(?:my\s+|the\s+)?(?:search|alerts|notifications)\b',
        r'\b(?:show|list|see)\s+(?:me\s+)?(?:my\s+)?(?:matches|results|properties)\b',
        r'\b(?:show|check)\s+(?:me\s+)?(?:my\s+)?search\s+(?:status|progress)\b',
    )
    for pattern in patterns:
        cleaned = re.sub(pattern, ' ', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r'^\s*(?:hi|hello|hey)(?:\s+there|\s+bot|\s+flathunter)?[!,.]*\s*$',
        ' ',
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r'^\s*(?:help|what\s+can\s+you\s+do|how\s+can\s+you\s+help\s+me)[!?.]*\s*$',
        ' ',
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r'^\s*(?:and|then)\b', ' ', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\b(?:and|then)\s*$', ' ', cleaned, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', cleaned).strip(' ,;.-')


def detect_collection_control_intents(text: str) -> list[CollectionControlIntent]:
    normalized = _normalize(text)
    _, has_terminal_finish = split_terminal_finish_phrase(text)
    intents: list[CollectionControlIntent] = []
    if (
        'requirement' in normalized
        or 'criteria' in normalized
        or re.search(r'what .*\b(have|know|collected|saved|captured)\b', normalized)
        or re.search(r'\b(show|give) me (everything|what you have)', normalized)
    ):
        intents.append(CollectionControlIntent.SHOW_SUMMARY)
    if (
        normalized in {
            'done', 'finish', 'start', 'start searching', 'search now',
            'thats all', 'that is all', 'thats it', 'that is it',
            'nothing else', 'all good', 'skip', 'skip preferences',
            'no', 'no thanks',
        }
        or 'dont ask anything else' in normalized
        or has_terminal_finish
    ):
        intents.append(CollectionControlIntent.FINISH)
    if re.search(r'\b(cancel|discard|stop)\b.*\b(search|setup|requirements?)\b', normalized):
        intents.append(CollectionControlIntent.CANCEL)
    if normalized in {'help', 'what can you do', 'how can you help me'}:
        intents.append(CollectionControlIntent.HELP)
    return list(dict.fromkeys(intents))


def missing_required_fields(draft: RenterRequirementDraft) -> list[RequirementField]:
    missing: list[RequirementField] = []
    if not draft.listing_types:
        missing.append(RequirementField.RENTAL_ARRANGEMENT)
    if (
        ListingType.ENTIRE_PROPERTY.value in draft.listing_types
        and not draft.configuration_answered
    ):
        missing.append(RequirementField.HOME_CONFIGURATIONS)
    if not (draft.preferred_locations or draft.acceptable_locations):
        missing.append(RequirementField.PREFERRED_LOCATIONS)
    if draft.max_rent is None or draft.max_rent <= 0:
        missing.append(RequirementField.BUDGET)
    if not (draft.preferred_move_in_date or draft.latest_move_in_date):
        missing.append(RequirementField.MOVE_IN_TIMING)
    return missing


def next_required_field(draft: RenterRequirementDraft) -> Optional[RequirementField]:
    missing = missing_required_fields(draft)
    return missing[0] if missing else None


def next_requirement_prompt(
    draft: RenterRequirementDraft,
    mode: CollectionMode = CollectionMode.HYBRID,
) -> str:
    field = next_required_field(draft)
    if field is None:
        return 'Your required details are complete. Please review them before starting.'
    prompt = REQUIRED_PROMPTS[field]
    if mode == CollectionMode.GUIDED:
        return f'Let us use a quick guided answer. {prompt}'
    return prompt


def build_requirement_patch_prompt(
    draft: RenterRequirementDraft,
    latest_text: str,
    requested_field: Optional[RequirementField] = None,
    deterministic_patch: Optional[RequirementTurnPatch] = None,
) -> str:
    context = {
        'verified_current_requirements': draft.to_requirement_dict(),
        'configuration_answered': draft.configuration_answered,
        'requested_field': requested_field.value if requested_field else None,
        'latest_renter_message': latest_text[:2000],
        'already_extracted_deterministically': (
            deterministic_patch.model_dump(mode='json')
            if deterministic_patch
            else {'operations': []}
        ),
    }
    return (
        'Extract only explicit requirement changes from the latest renter message. '
        'Return RequirementTurnPatch. Do not repeat unchanged values, decide completion, '
        'generate a question, or use facts from outside the supplied JSON. Use ADD only '
        'for explicit additions, REPLACE for explicit only/instead/change-to wording, '
        'and SET for a newly supplied scalar value. Dates must be ISO YYYY-MM-DD. '
        'Do not repeat fields already extracted deterministically unless you are adding '
        'a distinct preference value that is not present there. '
        f'Input: {json.dumps(context, default=str, ensure_ascii=True)}'
    )


def combine_requirement_turn_patches(
    deterministic_patch: RequirementTurnPatch,
    extracted_patch: RequirementTurnPatch,
) -> RequirementTurnPatch:
    '''Combine independent LLM facts while keeping deterministic values authoritative.'''
    combined = [item.model_copy(deep=True) for item in deterministic_patch.operations]
    deterministic_fields = {item.field for item in deterministic_patch.operations}
    for item in extracted_patch.operations:
        if item.field not in deterministic_fields:
            combined.append(item.model_copy(deep=True))
            continue
        if item.field not in {
            RequirementField.CORE_PREFERENCES,
            RequirementField.ADDITIONAL_PREFERENCES,
        }:
            continue
        existing = next(entry for entry in combined if entry.field == item.field)
        if not isinstance(existing.value, dict) or not isinstance(item.value, dict):
            continue
        # An explicit deterministic interpretation wins on duplicate preference keys.
        existing.value = {**item.value, **existing.value}
    return RequirementTurnPatch(operations=combined)


def requirement_turn_needs_enrichment(
    text: str,
    deterministic_patch: RequirementTurnPatch,
) -> bool:
    '''Return true when a deterministic turn appears to contain an extra free-form fact.'''
    if not deterministic_patch.operations:
        return True
    normalized = _normalize(text)
    if re.search(r'\b(?:with|without)\s+[a-z]', normalized) and not re.search(
        r'\b(?:replace|change)\b.+\bwith\b', normalized,
    ):
        known_with_phrases = {
            'with parking', 'with furnished', 'with no brokerage', 'with metro',
        }
        if not any(phrase in normalized for phrase in known_with_phrases):
            return True
    return bool(re.search(
        r'\b(?:balcony|gym|lift|elevator|pet[- ]?friendly|power backup|gated|security|'
        r'vegetarian|non[- ]?smoking|attached bathroom|air conditioning|ac)\b',
        normalized,
    ))


def parse_rental_arrangements(text: str) -> list[str]:
    normalized = _normalize(text)
    found: list[str] = []
    private_room = bool(re.search(r'\b(private|single)\s+room\b', normalized))
    shared_room = bool(re.search(r'\b(shared|sharing)\s+room\b', normalized))
    entire_property = bool(
        re.search(r'\b(entire|full|whole)\s+(property|flat|apartment|home)\b', normalized)
        or re.search(r'\b(entire property|full property)\b', normalized)
    )
    if 'not the whole property' in normalized or 'not whole property' in normalized:
        entire_property = False
    if private_room:
        found.append(ListingType.PRIVATE_ROOM.value)
    if shared_room:
        found.append(ListingType.SHARED_ROOM.value)
    if entire_property:
        found.append(ListingType.ENTIRE_PROPERTY.value)
    return found


def parse_home_configurations(text: str) -> list[str]:
    normalized = _normalize(text)
    configurations: list[str] = []
    if re.search(r'\b(studio|1\s*rk)\b', normalized):
        configurations.append('1RK')
    for match in re.finditer(r'\b([1-9])\s*\+?\s*bhk\b', normalized):
        value = f'{match.group(1)}BHK'
        if value not in configurations:
            configurations.append(value)
    if re.search(r'\b4\s*\+\s*bhk\b', normalized):
        configurations = [item for item in configurations if item != '4BHK']
        configurations.append('4+BHK')
    return configurations


def parse_budget(text: str) -> Optional[ParsedBudget]:
    normalized = _normalize(text).replace(',', '')
    normalized = re.sub(r'\b[1-9]\s*\+?\s*(?:bhk|rk)\b', ' ', normalized)
    amount = r'(\d+(?:\.\d+)?)\s*(k|thousand|lakh|lac)?'
    range_match = re.search(amount + r'\s*(?:to|-)\s*' + amount, normalized)
    if range_match:
        first_number = float(range_match.group(1))
        second_number = float(range_match.group(3))
        first_unit = range_match.group(2)
        second_unit = range_match.group(4)
        if not first_unit and first_number < 1000 and second_unit:
            first_unit = second_unit
        if not second_unit and second_number < 1000 and first_unit:
            second_unit = first_unit
        target = _amount_to_rupees(range_match.group(1), first_unit)
        maximum = _amount_to_rupees(range_match.group(3), second_unit)
        if target and maximum and target >= 1000 and target <= maximum:
            return ParsedBudget(target_rent=target, max_rent=maximum)

    candidates = list(re.finditer(amount, normalized))
    for match in reversed(candidates):
        unit = match.group(2)
        number = float(match.group(1))
        has_budget_context = bool(
            unit
            or re.search(r'\b(budget|rent|maximum|max|under|upto|up to)\b', normalized)
            or normalized.strip() == match.group(0).strip()
        )
        if not has_budget_context:
            continue
        maximum = _amount_to_rupees(str(number), unit)
        if maximum and maximum >= 1000:
            return ParsedBudget(target_rent=maximum, max_rent=maximum)
    return None


def parse_requested_locations(text: str) -> list[str]:
    value = text.strip()
    # Strip preference clauses before interpreting a short guided answer as
    # an area. "Without parking" and "near metro" are not place names.
    value = re.sub(
        r'\b(?:i\s+)?(?:do not|dont)\s+need(?:\s+to\s+be)?\s+'
        r'(?:near\s+(?:the\s+)?metro|parking|furnished|furniture|brokerage)\b',
        ' ',
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r'\b(?:with|without|no)\s+'
        r'(?:parking|furnished|furniture|brokerage)\b'
        r'|\b(?:not\s+furnished|unfurnished|near\s+(?:the\s+)?metro)\b',
        ' ',
        value,
        flags=re.IGNORECASE,
    )
    replacement_match = re.search(
        r'\b(?:replace|change)\b.+?\b(?:with|to)\s+(.+)',
        value,
        flags=re.IGNORECASE,
    )
    if replacement_match:
        value = replacement_match.group(1)
    location_match = re.search(
        r'\b(?:in|at|near)\s+(.+?)(?=\b(?:under|max(?:imum)?|budget|rent|move|starting|tomorrow|entire|private|shared)\b|$)',
        value,
        flags=re.IGNORECASE,
    )
    if location_match:
        value = location_match.group(1)
    value = re.sub(r'\b(?:i am|im|i\'m)\s+looking\s+for\b', ' ', value, flags=re.IGNORECASE)
    value = re.sub(
        r'\b(?:add|also|include|replace|change|increase|decrease|set|remove|'
        r'delete|drop|clear|with|to|only|instead|location|area|areas|'
        r'preferred|please|budget|rent|maximum|max)\b',
        ' ',
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r'\b[1-9]\s*\+?\s*(?:bhk|rk)\b', ' ', value, flags=re.IGNORECASE)
    value = re.sub(r'(?:₹|rs\.?)?\s*\d+(?:\.\d+)?\s*(?:k|thousand|lakh|lac)?', ' ', value, flags=re.IGNORECASE)
    value = re.sub(
        r'\b(?:entire|full|whole)\s+(?:property|flat|apartment|home)\b|\b(?:private|single|shared|sharing)\s+room\b',
        ' ',
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r'\b(?:tomorrow|today|next month|first week of next month|starting week next month|within \w+ weeks?)\b',
        ' ',
        value,
        flags=re.IGNORECASE,
    )
    parts = re.split(r'\s*(?:,|/|\bor\b|\band\b)\s*', value, flags=re.IGNORECASE)
    ignored = {
        '', 'and', 'or', 'maximum', 'max', 'budget', 'rent', 'thats it',
        'that is it', 'nothing else', 'search', 'property', 'range', 'mind',
        'parking', 'furnished', 'brokerage', 'no brokerage', 'near metro',
        'i am not sure', 'im not sure', 'not sure', 'i dont know', 'dont know',
        'idk', 'anything', 'anywhere', 'whatever', 'no preference',
    }
    result: list[str] = []
    for part in parts:
        cleaned = re.sub(r'\s+', ' ', part).strip(' .,-')
        normalized = _normalize(cleaned)
        if (
            normalized in ignored
            or re.search(r'\b(?:not sure|dont know|no preference)\b', normalized)
            or re.match(
                r'^(?:i|im|maybe|not|no|yes|can|could|would|what|why|how)\b',
                normalized,
            )
            or len(normalized.split()) > 6
            or not cleaned
            or len(cleaned) > 80
        ):
            continue
        if not re.search(r'[a-zA-Z]', cleaned):
            continue
        title = ' '.join(word.upper() if word.isupper() else word.capitalize() for word in cleaned.split())
        if title.casefold() not in {item.casefold() for item in result}:
            result.append(title)
    return result


def parse_move_in_window(
    text: str,
    *,
    now: datetime | date | None = None,
    timezone: tzinfo | str = 'Asia/Kolkata',
) -> Optional[MoveInWindow]:
    local_date = _local_date(now, timezone)
    normalized = _normalize(text)
    if 'tomorrow' in normalized:
        move_date = local_date + timedelta(days=1)
        return MoveInWindow(
            preferred_move_in_date=move_date,
            latest_move_in_date=move_date,
        )
    if normalized in {'today', 'immediately', 'as soon as possible', 'asap'}:
        return MoveInWindow(
            preferred_move_in_date=local_date,
            latest_move_in_date=local_date,
        )
    next_year = local_date.year + (1 if local_date.month == 12 else 0)
    next_month = 1 if local_date.month == 12 else local_date.month + 1
    if re.search(r'\b(first|starting|start|beginning)\s+week(?:\s+of)?\s+next\s+month\b', normalized):
        first = date(next_year, next_month, 1)
        return MoveInWindow(
            preferred_move_in_date=first,
            latest_move_in_date=date(next_year, next_month, 7),
        )
    if re.search(r'\bnext\s+month\b', normalized):
        first = date(next_year, next_month, 1)
        last = date(next_year, next_month, calendar.monthrange(next_year, next_month)[1])
        return MoveInWindow(
            preferred_move_in_date=first,
            latest_move_in_date=last,
        )
    weeks = re.search(r'\bwithin\s+(\d+|one|two|three|four)\s+weeks?\b', normalized)
    if weeks:
        count = _word_number(weeks.group(1))
        if count:
            return MoveInWindow(
                preferred_move_in_date=local_date,
                latest_move_in_date=local_date + timedelta(weeks=count),
            )
    iso_dates = re.findall(r'\b\d{4}-\d{2}-\d{2}\b', normalized)
    if iso_dates:
        parsed = [date.fromisoformat(item) for item in iso_dates[:2]]
        return MoveInWindow(
            preferred_move_in_date=parsed[0],
            latest_move_in_date=parsed[-1],
        )
    return None


def parse_requirement_turn(
    text: str,
    *,
    requested_field: Optional[RequirementField] = None,
    now: datetime | date | None = None,
    timezone: tzinfo | str = 'Asia/Kolkata',
) -> RequirementTurnPatch:
    text, _ = split_terminal_finish_phrase(text)
    text, _ = split_terminal_summary_phrase(text)
    text = strip_routed_non_requirement_clauses(text)
    normalized = _normalize(text)
    question_like = (
        text.strip().endswith('?')
        or bool(re.match(
            r'^(?:what|why|how|is|are|does|do you|do i|should|would|could|can you)\b',
            normalized,
        ))
    )
    explicit_question_action = bool(re.search(
        r'\b(?:add|remove|delete|drop|set|change|update|make)\b',
        normalized,
    ))
    if question_like and not explicit_question_action:
        return RequirementTurnPatch()
    operations: list[RequirementPatchOperation] = []
    action_verbs = set(re.findall(
        r'\b(add|remove|delete|drop|clear|set|replace|change|update|increase|decrease)\b',
        normalized,
    ))
    if len(action_verbs) > 1 and ' and ' in normalized:
        # Different operations joined in one turn must be scoped per clause.
        # Defer these to the structured patch extractor rather than letting a
        # global verb accidentally control every deterministic field.
        return RequirementTurnPatch()
    explicit_replace = bool(
        re.search(r'\b(only|instead|replace|change|update|increase|decrease|set)\b', normalized)
        or 'not the whole property' in normalized
    )
    explicit_list_replace = bool(
        re.search(r'\b(only|instead|replace|change)\b', normalized)
        or 'not the whole property' in normalized
    )
    explicit_remove = bool(re.search(r'\b(remove|delete|drop|clear)\b', normalized))

    arrangements = parse_rental_arrangements(text)
    if arrangements:
        operations.append(RequirementPatchOperation(
            field=RequirementField.RENTAL_ARRANGEMENT,
            operation=(
                RequirementChangeOperation.REMOVE
                if explicit_remove
                else
                RequirementChangeOperation.REPLACE
                if explicit_list_replace
                else RequirementChangeOperation.SET
            ),
            value=arrangements,
        ))

    configurations = parse_home_configurations(text)
    config_any = (
        requested_field == RequirementField.HOME_CONFIGURATIONS
        and normalized in {'any', 'any bhk', 'does not matter', 'no preference'}
    )
    if configurations or config_any:
        operations.append(RequirementPatchOperation(
            field=RequirementField.HOME_CONFIGURATIONS,
            operation=(
                RequirementChangeOperation.REMOVE
                if explicit_remove
                else
                RequirementChangeOperation.REPLACE
                if explicit_list_replace
                else RequirementChangeOperation.ADD
                if re.search(r'\b(add|also|include|or)\b', normalized)
                else RequirementChangeOperation.SET
            ),
            value=configurations,
        ))

    budget = parse_budget(text)
    if budget:
        operations.append(RequirementPatchOperation(
            field=RequirementField.BUDGET,
            operation=(
                RequirementChangeOperation.REMOVE
                if explicit_remove
                else
                RequirementChangeOperation.REPLACE
                if explicit_replace
                else RequirementChangeOperation.SET
            ),
            value=budget.model_dump(),
        ))

    move_in = parse_move_in_window(text, now=now, timezone=timezone)
    if move_in:
        operations.append(RequirementPatchOperation(
            field=RequirementField.MOVE_IN_TIMING,
            operation=(
                RequirementChangeOperation.REMOVE
                if explicit_remove
                else
                RequirementChangeOperation.REPLACE
                if explicit_replace
                else RequirementChangeOperation.SET
            ),
            value=move_in.model_dump(mode='json'),
        ))

    should_parse_locations = (
        requested_field in {
            RequirementField.PREFERRED_LOCATIONS,
            RequirementField.ACCEPTABLE_LOCATIONS,
            RequirementField.EXCLUDED_LOCATIONS,
        }
        or bool(
            re.search(r'\b(?:in|at|near)\s+[a-z]', normalized)
            and not re.search(r'\bin\s+(?:the\s+)?(?:range|mind)\b', normalized)
            and not re.search(r'\bnear\s+(?:the\s+)?metro\b', normalized)
        )
        or bool(re.match(r'^\s*[a-z][a-z ]{1,40}\s+[1-9]\s*bhk\b', normalized))
        or bool(
            budget
            and re.search(r'\band\s+[a-z][a-z ]{1,80}$', normalized)
        )
        or bool(
            budget
            and re.search(
                r'^[a-z][a-z ]{1,80}\s+and\s+(?:rs\.?\s*)?\d',
                normalized,
            )
        )
        or bool(re.search(
            r'\b(?:add|also|include|replace|change(?:\s+to)?|only|instead)\s+'
            r'(?:area\s+|location\s+)?[a-z]',
            normalized,
        ))
    )
    if should_parse_locations:
        locations = parse_requested_locations(text)
        if locations:
            field = requested_field if requested_field in {
                RequirementField.ACCEPTABLE_LOCATIONS,
                RequirementField.EXCLUDED_LOCATIONS,
            } else RequirementField.PREFERRED_LOCATIONS
            operations.append(RequirementPatchOperation(
                field=field,
                operation=(
                    RequirementChangeOperation.REMOVE
                    if explicit_remove
                    else
                    RequirementChangeOperation.REPLACE
                    if explicit_list_replace
                    else RequirementChangeOperation.ADD
                    if re.search(r'\b(add|also|include|or)\b', normalized)
                    else RequirementChangeOperation.SET
                ),
                value=locations,
            ))

    preferences: dict[str, PreferenceValue] = {}
    removed_preferences: dict[str, PreferenceValue] = {}
    for key, pattern in {
        'parking': r'\bparking\b',
        'furnished': (
            r'\b(?:furnished|unfurnished|not furnished|without furniture|'
            r'dont need furnished|do not need furnished)\b'
        ),
        'no_brokerage': r'\b(no brokerage|without brokerage)\b',
        'near_metro': r'\b(near|close to)\s+(the\s+)?metro\b',
    }.items():
        if re.search(pattern, normalized):
            if key == 'parking' and re.search(
                r'(?:\b(?:without|no|dont need|do not need)\b.{0,20}\bparking\b|'
                r'\bparking\b.{0,20}\bnot required\b)',
                normalized,
            ) and not explicit_remove:
                removed_preferences[key] = PreferenceValue(
                    value=True,
                    importance=PreferenceImportance.PREFERRED,
                )
                continue
            if key == 'near_metro' and re.search(
                r'(?:\b(?:dont need|do not need|not near|away from)\b.{0,30}\bmetro\b|'
                r'\bmetro\b.{0,20}\bnot required\b)',
                normalized,
            ) and not explicit_remove:
                removed_preferences[key] = PreferenceValue(
                    value=True,
                    importance=PreferenceImportance.PREFERRED,
                )
                continue
            if key == 'furnished' and re.search(
                r'\b(?:dont need furnished|do not need furnished)\b',
                normalized,
            ) and not explicit_remove:
                removed_preferences[key] = PreferenceValue(
                    value=True,
                    importance=PreferenceImportance.PREFERRED,
                )
                continue
            preference_value = True
            if key == 'furnished' and re.search(
                r'\b(?:not furnished|unfurnished|without furniture|dont need furnished|do not need furnished)\b',
                normalized,
            ):
                preference_value = False
            preferences[key] = PreferenceValue(
                value=preference_value,
                importance=PreferenceImportance.PREFERRED,
            )
    if preferences:
        operations.append(RequirementPatchOperation(
            field=RequirementField.CORE_PREFERENCES,
            operation=(
                RequirementChangeOperation.REMOVE
                if explicit_remove
                else RequirementChangeOperation.ADD
            ),
            value={key: value.model_dump(mode='json') for key, value in preferences.items()},
        ))
    if removed_preferences:
        operations.append(RequirementPatchOperation(
            field=RequirementField.CORE_PREFERENCES,
            operation=RequirementChangeOperation.REMOVE,
            value={
                key: value.model_dump(mode='json')
                for key, value in removed_preferences.items()
            },
        ))
    if (
        ' and ' in normalized
        and len({item.field for item in operations}) > 1
        and action_verbs.intersection({
            'remove', 'delete', 'drop', 'clear', 'set', 'replace',
            'change', 'update', 'increase', 'decrease',
        })
    ):
        # A mutation verb attached to one clause must not leak into another
        # field. The structured extractor can scope these mixed clauses.
        return RequirementTurnPatch()
    return RequirementTurnPatch(operations=operations)


def validate_requirement_turn_patch_grounding(
    patch: RequirementTurnPatch,
    latest_text: str,
    *,
    now: datetime | date | None = None,
    timezone: tzinfo | str = 'Asia/Kolkata',
) -> RequirementTurnPatch:
    '''Reject schema-valid LLM facts that are absent from the latest renter turn.'''
    normalized = _normalize(latest_text)

    def values(value: Any) -> list[str]:
        if isinstance(value, dict):
            return [str(key) for key in value]
        if isinstance(value, (list, tuple, set)):
            return [str(item) for item in value]
        return [] if value is None else [str(value)]

    parsed_arrangements = set(parse_rental_arrangements(latest_text))
    parsed_configurations = set(parse_home_configurations(latest_text))
    parsed_budget = parse_budget(latest_text)
    parsed_move = parse_move_in_window(
        latest_text,
        now=now,
        timezone=timezone,
    )
    for operation in patch.operations:
        proposed = values(operation.value)
        grounded = True
        if operation.field == RequirementField.RENTAL_ARRANGEMENT:
            grounded = bool(proposed) and set(proposed).issubset(parsed_arrangements)
        elif operation.field == RequirementField.HOME_CONFIGURATIONS:
            grounded = (
                not proposed and bool(re.search(r'\bany\b|no preference', normalized))
            ) or set(proposed).issubset(parsed_configurations)
        elif operation.field in {
            RequirementField.PREFERRED_LOCATIONS,
            RequirementField.ACCEPTABLE_LOCATIONS,
            RequirementField.EXCLUDED_LOCATIONS,
            RequirementField.WORK_LOCATION,
        }:
            grounded = bool(proposed) and all(
                _normalize(item) in normalized for item in proposed
            )
        elif operation.field == RequirementField.BUDGET:
            payload = operation.value if isinstance(operation.value, dict) else {}
            grounded = bool(parsed_budget) and all(
                payload.get(key) in {None, getattr(parsed_budget, key)}
                for key in {'target_rent', 'max_rent'}
            )
        elif operation.field == RequirementField.MOVE_IN_TIMING:
            payload = operation.value if isinstance(operation.value, dict) else {}
            grounded = bool(parsed_move) and all(
                (
                    str(payload.get(key)) if payload.get(key) is not None else None
                ) == (
                    str(getattr(parsed_move, key))
                    if getattr(parsed_move, key) is not None
                    else None
                )
                for key in {'preferred_move_in_date', 'latest_move_in_date'}
            )
        elif operation.field in {
            RequirementField.CORE_PREFERENCES,
            RequirementField.ADDITIONAL_PREFERENCES,
        }:
            aliases = {
                'no_brokerage': 'brokerage',
                'near_metro': 'metro',
            }
            grounded = bool(proposed) and all(
                aliases.get(item.casefold(), item.casefold()).replace('_', ' ')
                in normalized
                for item in proposed
            )
        if operation.operation == RequirementChangeOperation.REMOVE:
            grounded = grounded and bool(re.search(
                r'\b(?:remove|delete|drop|clear|without|no|not|dont|do not)\b',
                normalized,
            ))
        elif operation.operation == RequirementChangeOperation.REPLACE:
            grounded = grounded and bool(re.search(
                r'\b(?:replace|change|update|only|instead|set)\b',
                normalized,
            ))
        if not grounded:
            raise ValueError(
                f'Unverified requirement patch field: {operation.field.value}'
            )
    return patch


def apply_requirement_patch(
    draft: RenterRequirementDraft,
    patch: RequirementTurnPatch,
    *,
    confirmed_operation_indexes: frozenset[int] = frozenset(),
) -> RequirementMergeResult:
    working = draft.model_copy(deep=True)
    changed_fields: list[RequirementField] = []
    for index, operation in enumerate(patch.operations):
        current = _field_value(working, operation.field)
        proposed = _coerce_operation_value(operation.field, operation.value)
        if (
            index not in confirmed_operation_indexes
            and (
                (
                    _has_value(current)
                    and operation.operation == RequirementChangeOperation.REMOVE
                )
                or (
                    _has_value(current)
                    and
                    operation.operation == RequirementChangeOperation.SET
                    and not _equivalent(current, proposed)
                    and operation.field in {
                        RequirementField.RENTAL_ARRANGEMENT,
                        RequirementField.HOME_CONFIGURATIONS,
                        RequirementField.PREFERRED_LOCATIONS,
                        RequirementField.ACCEPTABLE_LOCATIONS,
                        RequirementField.EXCLUDED_LOCATIONS,
                        RequirementField.BUDGET,
                        RequirementField.MOVE_IN_TIMING,
                    }
                )
                or _makes_preference_required(operation.field, current, proposed)
            )
        ):
            return RequirementMergeResult(
                draft=draft.model_copy(deep=True),
                pending_conflict=PendingRequirementConflict(
                    field=operation.field,
                    current_value=current,
                    proposed_value=proposed,
                    operation_index=index,
                    staged_patch=patch,
                    confirmed_operation_indexes=sorted(confirmed_operation_indexes),
                    reason=(
                        'Removing an existing criterion needs confirmation.'
                        if operation.operation == RequirementChangeOperation.REMOVE
                        else 'Making a preference mandatory needs confirmation.'
                        if _makes_preference_required(operation.field, current, proposed)
                        else 'A different value is already collected for this field.'
                    ),
                ),
            )
        before = working.model_dump(mode='json')
        _apply_operation(working, operation, proposed)
        if working.model_dump(mode='json') != before and operation.field not in changed_fields:
            changed_fields.append(operation.field)

    if (
        working.target_rent is not None
        and working.max_rent is not None
        and working.target_rent > working.max_rent
    ):
        raise ValueError('Target rent cannot be greater than maximum rent')
    if (
        working.preferred_move_in_date
        and working.latest_move_in_date
        and working.preferred_move_in_date > working.latest_move_in_date
    ):
        raise ValueError('Preferred move-in date cannot be after latest move-in date')
    return RequirementMergeResult(draft=working, changed_fields=changed_fields)


def resolve_requirement_conflict(
    draft: RenterRequirementDraft,
    conflict: PendingRequirementConflict,
    resolution: ConflictResolution,
) -> RequirementMergeResult:
    operations = list(conflict.staged_patch.operations)
    confirmed = set(conflict.confirmed_operation_indexes)
    if resolution == ConflictResolution.KEEP_CURRENT:
        operations.pop(conflict.operation_index)
        confirmed = {
            index - 1 if index > conflict.operation_index else index
            for index in confirmed
            if index != conflict.operation_index
        }
        return apply_requirement_patch(
            draft,
            RequirementTurnPatch(operations=operations),
            confirmed_operation_indexes=frozenset(confirmed),
        )
    if resolution == ConflictResolution.ADD_PROPOSED:
        selected = operations[conflict.operation_index].model_copy(
            update={'operation': RequirementChangeOperation.ADD},
        )
        operations[conflict.operation_index] = selected
        return apply_requirement_patch(
            draft,
            RequirementTurnPatch(operations=operations),
            confirmed_operation_indexes=frozenset(
                confirmed | {conflict.operation_index}
            ),
        )
    return apply_requirement_patch(
        draft,
        conflict.staged_patch,
        confirmed_operation_indexes=frozenset(
            confirmed | {conflict.operation_index}
        ),
    )


def collection_signature(
    draft: RenterRequirementDraft,
    requested_field: Optional[RequirementField] = None,
    pending_conflict: Optional[PendingRequirementConflict] = None,
) -> str:
    payload = {
        'draft': draft.to_requirement_dict(),
        'configuration_answered': draft.configuration_answered,
        'requested_field': requested_field.value if requested_field else None,
        'pending_conflict': (
            pending_conflict.model_dump(mode='json')
            if pending_conflict
            else None
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def advance_collection_progress(
    progress: CollectionProgress,
    draft: RenterRequirementDraft,
    *,
    requested_field: Optional[RequirementField] = None,
    pending_conflict: Optional[PendingRequirementConflict] = None,
    made_progress: bool,
    parser_failed: bool = False,
    next_prompt: Optional[str] = None,
) -> CollectionProgress:
    signature = collection_signature(draft, requested_field, pending_conflict)
    if made_progress:
        return CollectionProgress(
            mode=progress.mode,
            no_progress_count=0,
            field_failure_count=0,
            last_signature=signature,
            last_prompt=next_prompt,
        )
    repeated = progress.last_signature == signature or (
        next_prompt is not None
        and progress.last_prompt == next_prompt
    )
    no_progress = progress.no_progress_count + 1
    field_failures = progress.field_failure_count + 1
    mode = progress.mode
    if parser_failed or repeated or no_progress >= 2:
        mode = CollectionMode.GUIDED
    return CollectionProgress(
        mode=mode,
        no_progress_count=no_progress,
        field_failure_count=field_failures,
        last_signature=signature,
        last_prompt=next_prompt,
    )


def describe_requirement_changes(
    before: RenterRequirementDraft,
    after: RenterRequirementDraft,
) -> list[str]:
    changes: list[str] = []
    if before.listing_types != after.listing_types:
        changes.append(
            'Rental arrangement: ' + _display_list(after.listing_types)
        )
    if (
        before.preferred_property_configurations
        != after.preferred_property_configurations
        or before.configuration_answered != after.configuration_answered
    ):
        configuration = (
            _display_list(after.preferred_property_configurations)
            if after.preferred_property_configurations
            else 'Any'
        )
        changes.append('Home configuration: ' + configuration)
    if before.preferred_locations != after.preferred_locations:
        changes.append(
            'Preferred locations: ' + _display_list(after.preferred_locations)
        )
    if before.acceptable_locations != after.acceptable_locations:
        changes.append(
            'Also acceptable: ' + _display_list(after.acceptable_locations)
        )
    if before.max_rent != after.max_rent or before.target_rent != after.target_rent:
        if after.target_rent and after.max_rent and after.target_rent != after.max_rent:
            changes.append(
                f'Budget: ₹{after.target_rent:,} to ₹{after.max_rent:,} per month'
            )
        elif after.max_rent:
            changes.append(f'Maximum rent: ₹{after.max_rent:,} per month')
    if (
        before.preferred_move_in_date != after.preferred_move_in_date
        or before.latest_move_in_date != after.latest_move_in_date
    ):
        if (
            after.preferred_move_in_date
            and after.latest_move_in_date
            and after.preferred_move_in_date != after.latest_move_in_date
        ):
            changes.append(
                'Move-in: '
                f'{after.preferred_move_in_date.isoformat()} to '
                f'{after.latest_move_in_date.isoformat()}'
            )
        elif after.preferred_move_in_date:
            changes.append('Move-in: ' + after.preferred_move_in_date.isoformat())
    if before.core_preferences != after.core_preferences:
        changes.append(
            'Preferences: ' + _display_list(list(after.core_preferences))
        )
    return changes


def _field_value(draft: RenterRequirementDraft, field: RequirementField) -> Any:
    if field == RequirementField.BUDGET:
        return {
            'target_rent': draft.target_rent,
            'max_rent': draft.max_rent,
        }
    if field == RequirementField.MOVE_IN_TIMING:
        return {
            'preferred_move_in_date': (
                draft.preferred_move_in_date.isoformat()
                if draft.preferred_move_in_date
                else None
            ),
            'latest_move_in_date': (
                draft.latest_move_in_date.isoformat()
                if draft.latest_move_in_date
                else None
            ),
        }
    return getattr(draft, field.value)


def _coerce_operation_value(field: RequirementField, value: Any) -> Any:
    if field == RequirementField.BUDGET:
        if isinstance(value, ParsedBudget):
            return value.model_dump()
        if isinstance(value, (int, float, str)):
            amount = int(value)
            return {'target_rent': amount, 'max_rent': amount}
        return ParsedBudget(**dict(value or {})).model_dump()
    if field == RequirementField.MOVE_IN_TIMING:
        if isinstance(value, MoveInWindow):
            return value.model_dump(mode='json')
        return MoveInWindow(**dict(value or {})).model_dump(mode='json')
    if field in {
        RequirementField.RENTAL_ARRANGEMENT,
        RequirementField.HOME_CONFIGURATIONS,
        RequirementField.PREFERRED_LOCATIONS,
        RequirementField.ACCEPTABLE_LOCATIONS,
        RequirementField.EXCLUDED_LOCATIONS,
    }:
        values = value if isinstance(value, list) else [value]
        cleaned = [str(item).strip() for item in values if item not in (None, '')]
        if any(not item or len(item) > 80 for item in cleaned):
            raise ValueError('Requirement list values must be short non-empty text')
        if field == RequirementField.RENTAL_ARRANGEMENT:
            try:
                return [ListingType(item).value for item in cleaned]
            except ValueError as error:
                raise ValueError('Unsupported rental arrangement') from error
        if field == RequirementField.HOME_CONFIGURATIONS:
            allowed = {'1RK', '1BHK', '2BHK', '3BHK', '4BHK', '4+BHK'}
            normalized = [item.upper().replace(' ', '') for item in cleaned]
            if any(item not in allowed for item in normalized):
                raise ValueError('Unsupported home configuration')
            return normalized
        return cleaned
    if field == RequirementField.CORE_PREFERENCES:
        return {
            str(key): (
                item
                if isinstance(item, PreferenceValue)
                else PreferenceValue(**item)
                if isinstance(item, dict)
                else PreferenceValue(
                    value=item,
                    importance=PreferenceImportance.PREFERRED,
                )
            )
            for key, item in dict(value or {}).items()
        }
    if field == RequirementField.ADDITIONAL_PREFERENCES:
        return {
            str(key)[:80]: str(item)[:500]
            for key, item in dict(value or {}).items()
        }
    return value


def _apply_operation(
    draft: RenterRequirementDraft,
    operation: RequirementPatchOperation,
    proposed: Any,
) -> None:
    field = operation.field
    if field == RequirementField.BUDGET:
        if operation.operation == RequirementChangeOperation.REMOVE:
            draft.target_rent = None
            draft.max_rent = None
        else:
            draft.target_rent = proposed.get('target_rent') or proposed.get('max_rent')
            draft.max_rent = proposed.get('max_rent')
        return
    if field == RequirementField.MOVE_IN_TIMING:
        if operation.operation == RequirementChangeOperation.REMOVE:
            draft.preferred_move_in_date = None
            draft.latest_move_in_date = None
        else:
            preferred = proposed.get('preferred_move_in_date')
            latest = proposed.get('latest_move_in_date')
            draft.preferred_move_in_date = (
                date.fromisoformat(preferred)
                if isinstance(preferred, str)
                else preferred
            )
            draft.latest_move_in_date = (
                date.fromisoformat(latest)
                if isinstance(latest, str)
                else latest
            )
        return
    if field in {
        RequirementField.RENTAL_ARRANGEMENT,
        RequirementField.HOME_CONFIGURATIONS,
        RequirementField.PREFERRED_LOCATIONS,
        RequirementField.ACCEPTABLE_LOCATIONS,
        RequirementField.EXCLUDED_LOCATIONS,
    }:
        current = list(getattr(draft, field.value) or [])
        previously_entire = (
            field == RequirementField.RENTAL_ARRANGEMENT
            and ListingType.ENTIRE_PROPERTY.value in current
        )
        if operation.operation == RequirementChangeOperation.ADD:
            setattr(draft, field.value, _merge_lists(current, proposed))
        elif operation.operation == RequirementChangeOperation.REMOVE:
            removed = {str(item).casefold() for item in proposed}
            setattr(
                draft,
                field.value,
                [item for item in current if str(item).casefold() not in removed],
            )
        else:
            setattr(draft, field.value, _merge_lists([], proposed))
        if field == RequirementField.HOME_CONFIGURATIONS:
            draft.configuration_answered = not (
                operation.operation == RequirementChangeOperation.REMOVE
                and not draft.preferred_property_configurations
                and ListingType.ENTIRE_PROPERTY.value in draft.listing_types
            )
        elif field == RequirementField.RENTAL_ARRANGEMENT:
            now_entire = ListingType.ENTIRE_PROPERTY.value in draft.listing_types
            if now_entire and not previously_entire and not draft.preferred_property_configurations:
                draft.configuration_answered = False
            elif not now_entire:
                draft.configuration_answered = True
        return
    if field in {
        RequirementField.CORE_PREFERENCES,
        RequirementField.ADDITIONAL_PREFERENCES,
    }:
        current = dict(getattr(draft, field.value) or {})
        if operation.operation == RequirementChangeOperation.REMOVE:
            for key in proposed:
                current.pop(str(key), None)
        elif operation.operation == RequirementChangeOperation.REPLACE:
            current = dict(proposed)
        else:
            current.update(proposed)
        setattr(draft, field.value, current)
        return
    setattr(
        draft,
        field.value,
        None if operation.operation == RequirementChangeOperation.REMOVE else proposed,
    )


def _merge_lists(current: list[Any], proposed: list[Any]) -> list[Any]:
    merged = list(current)
    known = {str(item).casefold() for item in merged}
    for item in proposed:
        normalized = str(item).casefold()
        if normalized not in known:
            merged.append(item)
            known.add(normalized)
    return merged


def _equivalent(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, default=str) == json.dumps(
        right,
        sort_keys=True,
        default=str,
    )


def _has_value(value: Any) -> bool:
    if isinstance(value, dict):
        return any(item not in (None, '', [], {}) for item in value.values())
    return value not in (None, '', [], {})


def _makes_preference_required(field: RequirementField, current: Any, proposed: Any) -> bool:
    if field not in {
        RequirementField.CORE_PREFERENCES,
        RequirementField.ADDITIONAL_PREFERENCES,
    } or not isinstance(proposed, dict):
        return False
    existing = current if isinstance(current, dict) else {}
    for key, value in proposed.items():
        detail = (
            value
            if isinstance(value, dict)
            else value.model_dump(mode='json')
            if hasattr(value, 'model_dump')
            else {}
        )
        importance = str(detail.get('importance') or '').split('.')[-1].upper()
        if importance != PreferenceImportance.REQUIRED.value:
            continue
        old = existing.get(key)
        old_detail = old if isinstance(old, dict) else (
            old.model_dump(mode='json') if hasattr(old, 'model_dump') else {}
        )
        old_importance = str(old_detail.get('importance') or '').split('.')[-1].upper()
        if old_importance != PreferenceImportance.REQUIRED.value:
            return True
    return False


def _display_list(values: list[Any]) -> str:
    if not values:
        return 'None'
    return ', '.join(
        escape(str(item).replace('_', ' ').title())
        for item in values
    )


def _amount_to_rupees(number: str, unit: Optional[str]) -> Optional[int]:
    try:
        value = float(number)
    except (TypeError, ValueError):
        return None
    normalized_unit = (unit or '').casefold()
    if normalized_unit in {'k', 'thousand'}:
        value *= 1000
    elif normalized_unit in {'lakh', 'lac'}:
        value *= 100000
    return int(value)


def _local_date(
    now: datetime | date | None,
    timezone: tzinfo | str,
) -> date:
    if isinstance(timezone, str):
        try:
            zone = ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            if timezone != 'Asia/Kolkata':
                raise
            zone = datetime_timezone(timedelta(hours=5, minutes=30))
    else:
        zone = timezone
    if now is None:
        return datetime.now(zone).date()
    if isinstance(now, datetime):
        if now.tzinfo is not None:
            return now.astimezone(zone).date()
        return now.replace(tzinfo=zone).date()
    return now


def _word_number(value: str) -> Optional[int]:
    words = {'one': 1, 'two': 2, 'three': 3, 'four': 4}
    if value.isdigit():
        return int(value)
    return words.get(value)


def _normalize(text: str) -> str:
    normalized = re.sub(r'\s+', ' ', (text or '').casefold())
    normalized = normalized.replace('’', '').replace('\'', '')
    return normalized.strip(' .!?')
