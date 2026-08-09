from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Optional
from uuid import UUID, uuid4

from app.common.enums import ListingType, SearchStatus
from app.db.models import SearchSession
from app.requirements.collector import parse_move_in_window
from app.requirements.schemas import (
    RequirementChangeOperation,
    RequirementEditPlan,
    RequirementEditResponse,
    RequirementExtractionResponse,
)


logger = logging.getLogger(__name__)
CONFIGURATION_ANSWERED_MARKER = '__flathunter_configuration_answered'
ALLOWED_PROPERTY_CONFIGURATIONS = {
    '1RK', '1BHK', '2BHK', '3BHK', '4BHK', '4+BHK',
}


class CreationKeyPayloadMismatch(RuntimeError):
    '''Internal signal used to recover a committed draft with a changed retry payload.'''


@dataclass(frozen=True)
class DraftSearchPersistenceResult:
    '''Result of an idempotent draft-and-requirements transaction.'''

    session: SearchSession
    created: bool


@dataclass(frozen=True)
class DraftSearchRecoveryResult:
    '''Exact owned draft located by its client-generated creation key.'''

    session: SearchSession
    requirements: dict


@dataclass(frozen=True)
class SearchActivationResult:
    '''Result of an atomic activation, replacement, and job enqueue.'''

    session: SearchSession
    activated: bool
    job_enqueued: bool
    replaced_search_id: Optional[UUID] = None


@dataclass(frozen=True)
class SearchCancellationResult:
    '''Sessions closed by one validated, atomic cancellation request.'''

    draft_session: Optional[SearchSession] = None
    open_session: Optional[SearchSession] = None

    @property
    def cancelled_count(self) -> int:
        return int(self.draft_session is not None) + int(self.open_session is not None)


@dataclass(frozen=True)
class SearchStatusTransitionResult:
    '''Atomic pause/resume result, including catch-up matching on resume.'''

    session: SearchSession
    changed: bool
    job_enqueued: bool


class RequirementService:
    """Owns deterministic search-profile validation and lifecycle writes."""

    EDITABLE_FIELDS = {
        'listing_types', 'preferred_locations', 'acceptable_locations',
        'excluded_locations', 'work_location', 'target_rent', 'max_rent',
        'preferred_move_in_date', 'latest_move_in_date',
        'preferred_property_configurations', 'core_preferences',
        'additional_preferences',
    }
    LIST_FIELDS = {
        'listing_types', 'preferred_locations', 'acceptable_locations',
        'excluded_locations', 'preferred_property_configurations',
    }
    DICT_FIELDS = {'core_preferences', 'additional_preferences'}
    CORE_CHANGE_FIELDS = {
        'listing_types', 'target_rent', 'max_rent',
        'preferred_move_in_date', 'latest_move_in_date',
        'preferred_property_configurations', 'excluded_locations',
    }
    LIFECYCLE_ERROR_MESSAGES = {
        'RENTER_NOT_FOUND': (ValueError, 'Renter account was not found'),
        'CREATION_KEY_REQUIRED': (ValueError, 'A search creation key is required'),
        'CREATION_KEY_CONFLICT': (RuntimeError, 'That search draft belongs to a different request'),
        'CREATION_KEY_PAYLOAD_MISMATCH': (
            CreationKeyPayloadMismatch,
            'That search request changed while it was being saved; please start again',
        ),
        'CITY_REQUIRED': (ValueError, 'A search city is required'),
        'INCOMPLETE_REQUIREMENTS': (
            ValueError,
            'The reviewed search is still missing a required value',
        ),
        'LISTING_TYPE_REQUIRED': (ValueError, 'Cannot save a search without a listing type'),
        'LOCATION_REQUIRED': (ValueError, 'Cannot save a search without a location'),
        'INVALID_RENT_RANGE': (
            ValueError,
            'The target rent must be positive and no higher than the maximum rent',
        ),
        'MOVE_IN_DATE_REQUIRED': (ValueError, 'Cannot save a search without move-in timing'),
        'INVALID_MOVE_IN_WINDOW': (
            ValueError,
            'The preferred move-in date cannot be after the latest move-in date',
        ),
        'INVALID_PROPERTY_CONFIGURATION': (
            ValueError,
            'The home configuration is not supported',
        ),
        'INVALID_CORE_PREFERENCES': (ValueError, 'Core preferences must be a valid object'),
        'INVALID_ADDITIONAL_PREFERENCES': (
            ValueError,
            'Additional preferences must be a valid object',
        ),
        'SEARCH_NOT_FOUND': (ValueError, 'Search was not found for this renter'),
        'SEARCH_REQUIREMENTS_MISSING': (ValueError, 'Search requirements are missing'),
        'SEARCH_NOT_DRAFT': (
            ValueError,
            'Only an owned draft search can be updated before activation',
        ),
        'SEARCH_NOT_ACTIVATABLE': (ValueError, 'Only a draft search can be started'),
        'STALE_SEARCH_VERSION': (
            RuntimeError,
            'Your search changed elsewhere; please review it and try again',
        ),
        'ACTIVE_SEARCH_EXISTS': (ValueError, 'You already have an active or paused search'),
        'STALE_REPLACEMENT_VERSION': (
            RuntimeError,
            'Your existing search changed; please review it before replacing it',
        ),
        'CANCEL_TARGET_REQUIRED': (ValueError, 'Choose a search or setup to cancel'),
        'DRAFT_ID_REQUIRED': (ValueError, 'A draft search ID is required for that cancellation'),
        'DRAFT_VERSION_REQUIRED': (
            ValueError,
            'A positive draft search version is required for cancellation',
        ),
        'OPEN_SEARCH_ID_REQUIRED': (
            ValueError,
            'An active or paused search ID is required for that cancellation',
        ),
        'OPEN_SEARCH_VERSION_REQUIRED': (
            ValueError,
            'A positive active search version is required for cancellation',
        ),
        'CANCEL_TARGET_CONFLICT': (
            ValueError,
            'The unfinished setup and active search must be different searches',
        ),
        'DRAFT_SEARCH_NOT_FOUND': (
            ValueError,
            'The unfinished setup was not found for this renter',
        ),
        'OPEN_SEARCH_NOT_FOUND': (
            ValueError,
            'The active or paused search was not found for this renter',
        ),
        'STALE_DRAFT_VERSION': (
            RuntimeError,
            'The unfinished setup changed; please review it before canceling',
        ),
        'STALE_OPEN_SEARCH_VERSION': (
            RuntimeError,
            'The active search changed; please review it before canceling',
        ),
        'SEARCH_NOT_OPEN': (
            ValueError,
            'Only an active or paused search can be canceled as the live search',
        ),
        'SEARCH_NOT_ACTIVE': (
            ValueError,
            'Only an active search can be paused',
        ),
        'SEARCH_NOT_PAUSED': (
            ValueError,
            'Only a paused search can be resumed',
        ),
        'PAUSED_FLAG_REQUIRED': (
            ValueError,
            'Choose whether the search should be paused or resumed',
        ),
        'RESUME_JOB_CONFLICT': (
            RuntimeError,
            'The search could not resume safely; please retry',
        ),
        'EXPECTED_VERSION_REQUIRED': (
            ValueError,
            'A positive expected search version is required',
        ),
        'SEARCH_NOT_EDITABLE': (
            ValueError,
            'Only an active or paused search can be edited',
        ),
    }

    def __init__(self, db: Optional[Any] = None, llm: Optional[Any] = None):
        if db is None:
            from app.db.client import get_supabase_client
            db = get_supabase_client()
        if llm is None:
            from app.llm.gemini import get_llm_provider
            llm = get_llm_provider()
        self.db = db
        self.llm = llm

    async def parse_requirements(self, text: str) -> RequirementExtractionResponse:
        prompt = f'''
        You are a friendly rental search assistant having a conversation with a renter.
        Analyze the conversation below and extract rental requirements.

        Conversation:
        "{text}"

        RULES:
        - Listing type, location, maximum budget, and move-in timing are mandatory.
        - Missing mandatory information means is_complete must be false.
        - Ask only the single most important missing item in a friendly 1-2 sentence follow-up.
        - Never invent values. Keep unknown facts null.
        - Return JSON matching RequirementExtractionResponse.
        '''
        try:
            return await self.llm.generate_structured(prompt, RequirementExtractionResponse)
        except Exception as error:
            raise ValueError(f"Failed to parse requirements: {error}") from error

    async def parse_search_edit(self, text: str, current_requirements: dict) -> RequirementEditResponse:
        """Extract only changes; omitted fields intentionally retain stored values."""
        prompt = f'''
        You update an existing Hyderabad rental search. Extract ONLY values the renter
        explicitly changes in their latest message. Do not infer or repeat unchanged fields.

        Current saved requirements: {current_requirements}
        Renter update: "{text}"

        Rules:
        - Return JSON matching RequirementEditResponse.
        - Fields not changed must be null (not empty lists or invented values).
        - For location additions/removals, return the complete resulting list using the
          current values and the renter's explicit change.
        - Do not clear a field unless the renter explicitly asks to remove or clear it.
        '''
        try:
            return await self.llm.generate_structured(prompt, RequirementEditResponse)
        except Exception as error:
            raise ValueError(f"Failed to parse search update: {error}") from error

    async def parse_search_edit_plan(self, text: str, current_requirements: dict) -> RequirementEditPlan:
        '''Extract explicit ADD, REMOVE, REPLACE, or SET operations.'''
        current_requirements = self.requirement_prompt_snapshot(
            current_requirements,
        )
        prompt = f'''
        You update an existing Hyderabad rental search. Convert only the renters explicit
        request into ordered operations. Never repeat unchanged values.

        Current saved requirements: {current_requirements}
        Renter update: {text!r}

        Rules:
        - Return JSON matching RequirementEditPlan.
        - Valid fields are: {sorted(self.EDITABLE_FIELDS)}.
        - ADD appends a value without removing existing values.
        - REMOVE deletes only explicitly named values or keys.
        - REPLACE replaces an entire list or dictionary.
        - SET assigns one scalar field.
        - Use a list value for list-field ADD, REMOVE, and REPLACE operations.
        - For core_preferences values use a mapping keyed by preference name, with value
          and importance. Importance is REQUIRED or PREFERRED.
        - Do not invent changes and do not perform any operation yourself.
        '''
        try:
            plan = await self.llm.generate_structured(prompt, RequirementEditPlan)
        except Exception as error:
            logger.exception('Operation-aware search edit parsing failed')
            raise ValueError('I could not understand that search update. Please rephrase it.') from error
        invalid = sorted({change.field for change in plan.changes} - self.EDITABLE_FIELDS)
        if invalid:
            raise ValueError(f'Unsupported search field: {invalid[0]}')
        self._validate_edit_plan_grounding(plan, text)
        return plan

    @classmethod
    def _validate_edit_plan_grounding(
        cls,
        plan: RequirementEditPlan,
        latest_text: str,
        *,
        now: datetime | date | None = None,
    ) -> None:
        '''Reject structured edits whose proposed facts are absent from this turn.'''
        normalized = re.sub(r'\s+', ' ', (latest_text or '').casefold()).strip()
        compact = re.sub(r'[^a-z0-9]+', '', normalized)
        amounts: set[int] = set()
        for number, unit in re.findall(
            r'(\d+(?:\.\d+)?)\s*(k|thousand|lakh|lac)?',
            normalized,
        ):
            value = float(number)
            if unit in {'k', 'thousand'}:
                value *= 1000
            elif unit in {'lakh', 'lac'}:
                value *= 100000
            amounts.add(int(value))

        def strings(value: Any) -> list[str]:
            if isinstance(value, Mapping):
                return [str(key) for key in value]
            if isinstance(value, (list, tuple, set)):
                return [str(item) for item in value]
            return [] if value is None else [str(value)]

        for change in plan.changes:
            values = strings(cls._plain_value(change.value))
            grounded = True
            if change.field in {
                'preferred_locations', 'acceptable_locations',
                'excluded_locations', 'work_location',
            }:
                grounded = bool(values) and all(
                    re.sub(r'\s+', ' ', value.casefold()).strip() in normalized
                    for value in values
                )
            elif change.field == 'preferred_property_configurations':
                grounded = (
                    not values
                    and bool(re.search(r'\bany\b|no preference', normalized))
                ) or all(
                    re.sub(r'[^a-z0-9+]+', '', value.casefold()) in compact
                    for value in values
                )
            elif change.field == 'listing_types':
                synonyms = {
                    ListingType.ENTIRE_PROPERTY.value: (
                        'entire property', 'full property', 'whole property',
                        'entire flat', 'entire apartment',
                    ),
                    ListingType.PRIVATE_ROOM.value: ('private room', 'single room'),
                    ListingType.SHARED_ROOM.value: ('shared room', 'sharing room'),
                }
                grounded = bool(values) and all(
                    any(term in normalized for term in synonyms.get(value, ()))
                    for value in values
                )
            elif change.field in {'target_rent', 'max_rent'}:
                grounded = bool(values) and all(
                    str(value).isdigit() and int(value) in amounts
                    for value in values
                )
            elif change.field in {
                'preferred_move_in_date', 'latest_move_in_date',
            }:
                move_window = parse_move_in_window(
                    latest_text,
                    now=now,
                    timezone='Asia/Kolkata',
                )
                expected = (
                    getattr(move_window, change.field)
                    if move_window is not None
                    else None
                )
                grounded = bool(values) and all(
                    expected is not None and str(expected) == value
                    for value in values
                )
            elif change.field in {'core_preferences', 'additional_preferences'}:
                grounded = bool(values) and all(
                    value.casefold().replace('_', ' ') in normalized
                    or value.casefold().split('_')[-1] in normalized
                    for value in values
                )
            if not grounded:
                raise ValueError(
                    f'I could not verify the proposed {change.field.replace("_", " ")} '
                    'from your latest message. Please state that value explicitly.'
                )

    @staticmethod
    def missing_core_requirements(requirements: RequirementExtractionResponse) -> list[str]:
        missing = []
        if not requirements.listing_types:
            missing.append("listing type")
        if not (requirements.preferred_locations or requirements.acceptable_locations):
            missing.append("location")
        if requirements.max_rent is None or requirements.max_rent <= 0:
            missing.append("maximum budget")
        if not (requirements.preferred_move_in_date or requirements.latest_move_in_date):
            missing.append("move-in timing")
        return missing

    def create_renter_search_draft(
        self,
        user_id: UUID,
        requirements: RequirementExtractionResponse,
        raw_text: str,
        *,
        creation_key: UUID,
        city: str = 'Hyderabad',
    ) -> DraftSearchPersistenceResult:
        '''Atomically create one idempotent draft and its requirement row.'''

        params = self._rpc_requirement_params(requirements, raw_text)
        recovered = self.get_renter_search_draft_by_creation_key(
            user_id,
            creation_key,
        )
        if recovered:
            if not self._draft_payload_matches(
                recovered,
                params,
                city,
            ):
                raise CreationKeyPayloadMismatch(
                    'That search request changed while it was being saved; please retry'
                )
            return DraftSearchPersistenceResult(
                session=recovered.session,
                created=False,
            )

        params.update({
            'p_creation_key': str(creation_key),
            'p_user_id': str(user_id),
            'p_city': city.strip(),
        })
        try:
            row = self._execute_lifecycle_rpc('create_renter_search_draft', params)
        except CreationKeyPayloadMismatch:
            # Never turn an idempotent create replay into a write. The handler
            # may explicitly recover and update on a later user-triggered retry.
            raise
        return DraftSearchPersistenceResult(
            session=self._session_from_rpc_row(row),
            created=bool(row.get('created')),
        )

    def get_renter_search_draft_by_creation_key(
        self,
        user_id: UUID,
        creation_key: UUID,
    ) -> Optional[DraftSearchRecoveryResult]:
        name = 'get_renter_search_draft_by_creation_key'
        try:
            response = self.db.rpc(name, {
                'p_user_id': str(user_id),
                'p_creation_key': str(creation_key),
            }).execute()
        except Exception as error:
            self._raise_lifecycle_error(name, error)
        rows = response.data or []
        if rows == []:
            return None
        if (
            not isinstance(rows, list)
            or len(rows) != 1
            or not isinstance(rows[0], dict)
        ):
            raise RuntimeError('Draft recovery returned an invalid response')
        requirements = rows[0].get('requirements')
        if not isinstance(requirements, dict):
            raise RuntimeError('Draft recovery returned invalid requirements')
        return DraftSearchRecoveryResult(
            session=self._session_from_rpc_row(rows[0]),
            requirements=dict(requirements),
        )

    def get_owned_search_draft(
        self,
        user_id: UUID,
        search_id: Optional[UUID] = None,
    ) -> Optional[DraftSearchRecoveryResult]:
        '''Read the newest, or one exact, renter-owned durable DRAFT.'''
        query = (
            self.db.table('search_sessions')
            .select('*')
            .eq('user_id', str(user_id))
            .eq('status', SearchStatus.DRAFT.value)
        )
        if search_id is not None:
            query = query.eq('id', str(search_id))
        else:
            query = query.order('created_at', desc=True).limit(1)
        sessions = query.execute().data or []
        if not sessions:
            return None
        session = SearchSession(**sessions[0])
        requirements = (
            self.db.table('search_requirements')
            .select('*')
            .eq('search_id', str(session.id))
            .limit(1)
            .execute()
        ).data or []
        if not requirements:
            return None
        return DraftSearchRecoveryResult(
            session=session,
            requirements=dict(requirements[0]),
        )

    def recovered_draft_matches(
        self,
        recovered: DraftSearchRecoveryResult,
        requirements: RequirementExtractionResponse,
        raw_text: str,
        *,
        city: str = 'Hyderabad',
    ) -> bool:
        '''Compare an uncertain write result with the exact intended payload.'''
        return self._draft_payload_matches(
            recovered,
            self._rpc_requirement_params(requirements, raw_text),
            city,
        )

    @classmethod
    def _draft_payload_matches(
        cls,
        recovered: DraftSearchRecoveryResult,
        intended: Mapping[str, Any],
        city: str,
    ) -> bool:
        if recovered.session.city != city.strip():
            return False
        persisted = recovered.requirements
        list_fields = {
            'listing_types', 'preferred_locations', 'acceptable_locations',
            'excluded_locations', 'preferred_property_configurations',
        }
        for field in (
            'listing_types', 'preferred_locations', 'target_rent', 'max_rent',
            'acceptable_locations', 'excluded_locations', 'work_location',
            'preferred_move_in_date', 'latest_move_in_date',
            'preferred_property_configurations', 'core_preferences',
            'additional_preferences', 'raw_requirement_text',
        ):
            current = persisted.get(field)
            proposed = intended.get('p_' + field)
            if field in list_fields:
                if list(current or []) != list(proposed or []):
                    return False
            elif field in {'preferred_move_in_date', 'latest_move_in_date'}:
                if (
                    str(current) if current is not None else None
                ) != (
                    str(proposed) if proposed is not None else None
                ):
                    return False
            elif current != proposed:
                return False
        return True

    def create_draft_search(
        self,
        user_id: UUID,
        requirements: RequirementExtractionResponse,
        raw_text: str,
        *,
        creation_key: Optional[UUID] = None,
        city: str = 'Hyderabad',
    ) -> SearchSession:
        '''Compatibility wrapper returning the SearchSession used by existing callers.'''

        missing = self.missing_core_requirements(requirements)
        if missing:
            raise ValueError(f"Cannot create a search without: {', '.join(missing)}")
        result = self.create_renter_search_draft(
            user_id,
            requirements,
            raw_text,
            creation_key=creation_key or uuid4(),
            city=city,
        )
        return result.session

    def activate_renter_search(
        self,
        user_id: UUID,
        search_id: UUID,
        *,
        expected_version: int,
        replace_search_id: Optional[UUID] = None,
        replace_expected_version: Optional[int] = None,
    ) -> SearchActivationResult:
        '''Atomically activate a draft, optionally replace an open search, and enqueue matching.'''
        if expected_version is None or int(expected_version) <= 0:
            raise ValueError('A positive expected search version is required')
        if replace_search_id is not None and (
            replace_expected_version is None or int(replace_expected_version) <= 0
        ):
            raise ValueError('A positive expected replacement version is required')

        row = self._execute_lifecycle_rpc('activate_renter_search', {
            'p_user_id': str(user_id),
            'p_search_id': str(search_id),
            'p_expected_version': expected_version,
            'p_replace_search_id': str(replace_search_id) if replace_search_id else None,
            'p_replace_expected_version': replace_expected_version,
        })
        replaced = row.get('replaced_search_id')
        return SearchActivationResult(
            session=self._session_from_rpc_row(row),
            activated=bool(row.get('activated')),
            job_enqueued=bool(row.get('job_enqueued')),
            replaced_search_id=UUID(str(replaced)) if replaced else None,
        )

    def activate_search(
        self,
        user_id: UUID,
        search_id: UUID,
        *,
        expected_version: int,
        replace_search_id: Optional[UUID] = None,
        replace_expected_version: Optional[int] = None,
    ) -> SearchSession:
        '''Compatibility wrapper returning the activated SearchSession.'''

        return self.activate_renter_search(
            user_id,
            search_id,
            expected_version=expected_version,
            replace_search_id=replace_search_id,
            replace_expected_version=replace_expected_version,
        ).session

    def create_search(self, user_id: UUID, requirements: RequirementExtractionResponse, raw_text: str) -> SearchSession:
        '''Compatibility helper for existing callers during the handler migration.'''

        draft = self.create_draft_search(user_id, requirements, raw_text)
        return self.activate_search(user_id, draft.id, expected_version=draft.version)

    def update_renter_search_draft(
        self,
        user_id: UUID,
        search_id: UUID,
        requirements: RequirementExtractionResponse,
        raw_text: str,
        *,
        expected_version: int,
    ) -> SearchSession:
        '''Atomically replace an owned draft requirement row after typed validation.'''
        if expected_version is None or int(expected_version) <= 0:
            raise ValueError('A positive expected search version is required')

        params = self._rpc_requirement_params(requirements, raw_text)
        params.update({
            'p_user_id': str(user_id),
            'p_search_id': str(search_id),
            'p_expected_version': expected_version,
        })
        row = self._execute_lifecycle_rpc('update_renter_search_draft', params)
        if not row.get('updated'):
            raise RuntimeError('Draft requirements were not updated')
        return self._session_from_rpc_row(row)

    def update_draft_search(
        self,
        user_id: UUID,
        search_id: UUID,
        requirements: RequirementExtractionResponse,
        raw_text: str,
        *,
        expected_version: int,
    ) -> None:
        '''Compatibility wrapper for the atomic draft-update RPC.'''

        missing = self.missing_core_requirements(requirements)
        if missing:
            raise ValueError(f"Cannot save a search without: {', '.join(missing)}")
        self.update_renter_search_draft(
            user_id,
            search_id,
            requirements,
            raw_text,
            expected_version=expected_version,
        )

    def get_current_search(self, user_id: UUID) -> tuple[dict, dict]:
        '''Return the newest renter-owned ACTIVE or PAUSED search and its requirements.'''

        sessions = (
            self.db.table('search_sessions')
            .select('*')
            .eq('user_id', str(user_id))
            .in_('status', [SearchStatus.ACTIVE.value, SearchStatus.PAUSED.value])
            .order('created_at', desc=True)
            .limit(1)
            .execute()
        )
        session = (sessions.data or [None])[0]
        if session is None:
            raise ValueError("You do not have an active or paused search to edit")
        requirements = self.db.table("search_requirements").select("*").eq("search_id", session["id"]).execute()
        if not requirements.data:
            raise ValueError("Your saved requirements could not be found")
        return session, requirements.data[0]

    @classmethod
    def requirement_prompt_snapshot(
        cls,
        requirements: Mapping[str, Any],
    ) -> dict:
        '''Return only canonical verified fields safe for bounded LLM context.'''
        return {
            key: cls._plain_value(requirements.get(key))
            for key in cls._requirements_payload_fields()
            if key != 'raw_requirement_text' and key in requirements
        }

    def get_editable_search(self, user_id: UUID) -> tuple[dict, dict]:
        '''Compatibility alias for callers that edit the current open search.'''

        return self.get_current_search(user_id)

    def close_owned_search(
        self,
        user_id: UUID,
        search_id: UUID,
        expected_version: int,
        allowed_statuses: Iterable[SearchStatus | str],
    ) -> SearchSession:
        '''Close one explicitly owned search only from a caller-approved status.'''

        statuses: set[str] = set()
        for status in allowed_statuses:
            try:
                normalized = SearchStatus(status).value
            except ValueError as error:
                raise ValueError(f'Unsupported search status: {status}') from error
            if normalized == SearchStatus.CLOSED.value:
                raise ValueError('CLOSED cannot be an allowed source status')
            statuses.add(normalized)
        if not statuses:
            raise ValueError('At least one allowed source status is required')
        if expected_version <= 0:
            raise ValueError('A positive expected search version is required')

        now = self._now()
        result = (
            self.db.table('search_sessions')
            .update({
                'status': SearchStatus.CLOSED.value,
                'closed_at': now,
                'updated_at': now,
            })
            .eq('id', str(search_id))
            .eq('user_id', str(user_id))
            .eq('version', expected_version)
            .in_('status', sorted(statuses))
            .execute()
        )
        if len(result.data or []) != 1:
            raise RuntimeError(
                'The search was not closed because its owner, status, or version changed'
            )
        return SearchSession(**result.data[0])

    def cancel_renter_searches(
        self,
        user_id: UUID,
        *,
        draft_search_id: Optional[UUID] = None,
        draft_expected_version: Optional[int] = None,
        open_search_id: Optional[UUID] = None,
        open_expected_version: Optional[int] = None,
    ) -> SearchCancellationResult:
        '''Atomically cancel a selected setup, open search, or both.

        The RPC validates ownership, state, and every supplied version before
        either selected row is mutated.
        '''

        if draft_search_id is None and open_search_id is None:
            raise ValueError('Choose a search or setup to cancel')
        if draft_search_id is None and draft_expected_version is not None:
            raise ValueError('A draft search ID is required for that cancellation')
        if open_search_id is None and open_expected_version is not None:
            raise ValueError('An active or paused search ID is required for that cancellation')
        if draft_search_id is not None:
            if draft_expected_version is None or int(draft_expected_version) <= 0:
                raise ValueError('A positive draft search version is required for cancellation')
        if open_search_id is not None:
            if open_expected_version is None or int(open_expected_version) <= 0:
                raise ValueError('A positive active search version is required for cancellation')
        if (
            draft_search_id is not None
            and open_search_id is not None
            and draft_search_id == open_search_id
        ):
            raise ValueError('The unfinished setup and active search must be different searches')

        row = self._execute_lifecycle_rpc('cancel_renter_searches', {
            'p_user_id': str(user_id),
            'p_draft_search_id': str(draft_search_id) if draft_search_id else None,
            'p_draft_expected_version': (
                int(draft_expected_version) if draft_search_id else None
            ),
            'p_open_search_id': str(open_search_id) if open_search_id else None,
            'p_open_expected_version': (
                int(open_expected_version) if open_search_id else None
            ),
        })
        draft_session = self._optional_session_from_rpc_value(
            row.get('draft_session'), 'draft session'
        )
        open_session = self._optional_session_from_rpc_value(
            row.get('open_session'), 'open session'
        )
        result = SearchCancellationResult(
            draft_session=draft_session,
            open_session=open_session,
        )
        expected_count = int(draft_search_id is not None) + int(open_search_id is not None)
        try:
            reported_count = int(row.get('cancelled_count'))
        except (TypeError, ValueError) as error:
            raise RuntimeError('Search cancellation returned an invalid result') from error
        if result.cancelled_count != expected_count or reported_count != expected_count:
            raise RuntimeError('Search cancellation returned an invalid result')
        return result

    def set_renter_search_paused(
        self,
        user_id: UUID,
        search_id: UUID,
        *,
        expected_version: int,
        paused: bool,
    ) -> SearchStatusTransitionResult:
        '''Atomically pause/resume an owned search and enqueue a fresh resume scan.'''

        self._validate_expected_version(expected_version)
        row = self._execute_lifecycle_rpc('set_renter_search_paused', {
            'p_user_id': str(user_id),
            'p_search_id': str(search_id),
            'p_expected_version': int(expected_version),
            'p_paused': bool(paused),
        })
        result = SearchStatusTransitionResult(
            session=self._session_from_rpc_row(row),
            changed=bool(row.get('changed')),
            job_enqueued=bool(row.get('job_enqueued')),
        )
        expected_status = (
            SearchStatus.PAUSED if paused else SearchStatus.ACTIVE
        )
        if result.session.status != expected_status:
            raise RuntimeError('Search status update returned an invalid result')
        if not paused and result.changed and not result.job_enqueued:
            raise RuntimeError('Resumed search did not enqueue catch-up matching')
        return result

    def update_live_search(
        self,
        user_id: UUID,
        search_id: UUID,
        patch: RequirementEditResponse,
        raw_text: str,
        *,
        expected_version: int,
    ) -> int:
        """Persist a renter-owned edit, advance version, and queue fresh matching.

        Requirements, search version, and the rematch job commit in one RPC.
        """
        self._validate_expected_version(expected_version)
        current = self._get_requirement_snapshot(search_id)
        merged = self.merge_live_requirements(current, patch)
        merged = self._validate_live_configuration_transition(
            current,
            merged,
            explicit_configuration_change=(
                'preferred_property_configurations' in patch.model_fields_set
                and not (patch.preferred_property_configurations or [])
            ),
            configuration_removed=False,
        )
        missing = self._missing_persisted_core_requirements(merged)
        if missing:
            raise ValueError(f"This update would remove required search details: {', '.join(missing)}")
        next_version, _ = self._persist_live_search_snapshot(
            user_id,
            search_id,
            merged,
            raw_text,
            expected_version=expected_version,
        )
        return next_version

    @staticmethod
    def merge_live_requirements(current: dict, patch: RequirementEditResponse) -> dict:
        """Return stored requirements overlaid with only explicitly supplied patch fields."""
        merged = dict(current)
        for field, value in patch.model_dump(exclude_none=True, exclude={"conversational_summary"}).items():
            if field == "core_preferences":
                existing = dict(merged.get("core_preferences") or {})
                existing.update({key: item.model_dump() if hasattr(item, "model_dump") else item for key, item in value.items()})
                merged[field] = existing
            elif field == "additional_preferences":
                existing = dict(merged.get("additional_preferences") or {})
                existing.update(value)
                merged[field] = existing
            else:
                merged[field] = value
        return merged

    @classmethod
    def apply_edit_plan(cls, current: dict, plan: RequirementEditPlan) -> dict:
        '''Resolve an operation-aware plan into a complete requirement snapshot.'''
        resolved = dict(current)
        for change in plan.changes:
            field = change.field
            if field not in cls.EDITABLE_FIELDS:
                raise ValueError(f'Unsupported search field: {field}')
            operation = change.operation
            value = cls._plain_value(change.value)

            if field in cls.LIST_FIELDS:
                existing = list(resolved.get(field) or [])
                values = value if isinstance(value, list) else [value]
                values = [item for item in values if item is not None]
                if operation == RequirementChangeOperation.ADD:
                    known = {str(item).casefold() for item in existing}
                    for item in values:
                        normalized = str(item).casefold()
                        if normalized not in known:
                            existing.append(item)
                            known.add(normalized)
                    resolved[field] = existing
                elif operation == RequirementChangeOperation.REMOVE:
                    removed = {str(item).casefold() for item in values}
                    resolved[field] = [item for item in existing if str(item).casefold() not in removed]
                elif operation in {RequirementChangeOperation.REPLACE, RequirementChangeOperation.SET}:
                    resolved[field] = values
                continue

            if field in cls.DICT_FIELDS:
                existing = dict(resolved.get(field) or {})
                if operation == RequirementChangeOperation.REMOVE:
                    if isinstance(value, list):
                        keys = value
                    elif isinstance(value, dict):
                        keys = list(value)
                    else:
                        keys = [value]
                    for key in keys:
                        existing.pop(str(key), None)
                    resolved[field] = existing
                elif operation == RequirementChangeOperation.REPLACE:
                    if not isinstance(value, dict):
                        raise ValueError(f'{field} must be replaced with a mapping')
                    resolved[field] = value
                elif operation in {RequirementChangeOperation.ADD, RequirementChangeOperation.SET}:
                    if not isinstance(value, dict):
                        raise ValueError(f'{field} must be updated with a mapping')
                    existing.update(value)
                    resolved[field] = existing
                continue

            if operation == RequirementChangeOperation.ADD:
                raise ValueError(f'ADD is not valid for scalar field {field}')
            resolved[field] = None if operation == RequirementChangeOperation.REMOVE else value
        return resolved

    @classmethod
    def edit_plan_is_risky(cls, current: dict, plan: RequirementEditPlan) -> bool:
        '''Return whether explicit confirmation is required before persistence.'''
        for change in plan.changes:
            if change.operation in {RequirementChangeOperation.REMOVE, RequirementChangeOperation.REPLACE}:
                return True
            if change.field in cls.CORE_CHANGE_FIELDS:
                return True
            if change.field in {'preferred_locations', 'acceptable_locations'}:
                if change.operation != RequirementChangeOperation.ADD:
                    return True
                continue
            if change.field == 'core_preferences':
                value = cls._plain_value(change.value)
                if not isinstance(value, dict):
                    return True
                existing = current.get('core_preferences') or {}
                for key, item in value.items():
                    detail = cls._plain_value(item)
                    importance = detail.get('importance') if isinstance(detail, dict) else None
                    if str(importance).upper().endswith('REQUIRED') or key in existing:
                        return True
        return False

    def update_live_search_from_plan(
        self,
        user_id: UUID,
        search_id: UUID,
        plan: RequirementEditPlan,
        raw_text: str,
        *,
        expected_version: int,
    ) -> tuple[int, dict]:
        '''Apply a validated plan in the atomic live-search update transaction.'''
        self._validate_expected_version(expected_version)
        current = self._get_requirement_snapshot(search_id)
        merged = self.apply_edit_plan(current, plan)
        configuration_changes = [
            change
            for change in plan.changes
            if change.field == 'preferred_property_configurations'
        ]
        merged = self._validate_live_configuration_transition(
            current,
            merged,
            explicit_configuration_change=bool(
                configuration_changes
                and configuration_changes[-1].operation in {
                    RequirementChangeOperation.SET,
                    RequirementChangeOperation.REPLACE,
                }
                and not (
                    merged.get('preferred_property_configurations') or []
                )
            ),
            configuration_removed=bool(
                configuration_changes
                and configuration_changes[-1].operation
                == RequirementChangeOperation.REMOVE
            ),
        )
        missing = self._missing_persisted_core_requirements(merged)
        if missing:
            missing_text = ', '.join(missing)
            raise ValueError(f'This update would remove required search details: {missing_text}')
        return self._persist_live_search_snapshot(
            user_id,
            search_id,
            merged,
            raw_text,
            expected_version=expected_version,
        )

    @classmethod
    def _validate_live_configuration_transition(
        cls,
        current: Mapping[str, Any],
        merged: Mapping[str, Any],
        *,
        explicit_configuration_change: bool,
        configuration_removed: bool,
    ) -> dict:
        '''Preserve explicit Any only when the renter actually answered configuration.'''
        validated = dict(merged)
        configurations = [
            str(item).upper().replace(' ', '')
            for item in (validated.get('preferred_property_configurations') or [])
        ]
        if any(item not in ALLOWED_PROPERTY_CONFIGURATIONS for item in configurations):
            raise ValueError('The home configuration is not supported')
        validated['preferred_property_configurations'] = configurations

        current_types = {
            item.value if hasattr(item, 'value') else str(item)
            for item in (current.get('listing_types') or [])
        }
        new_types = {
            item.value if hasattr(item, 'value') else str(item)
            for item in (validated.get('listing_types') or [])
        }
        transitioned_to_entire = (
            ListingType.ENTIRE_PROPERTY.value in new_types
            and ListingType.ENTIRE_PROPERTY.value not in current_types
        )
        additional = dict(validated.get('additional_preferences') or {})
        if configurations:
            additional.pop(CONFIGURATION_ANSWERED_MARKER, None)
        elif configuration_removed or (
            transitioned_to_entire and not explicit_configuration_change
        ):
            additional.pop(CONFIGURATION_ANSWERED_MARKER, None)
        elif (
            explicit_configuration_change
            and ListingType.ENTIRE_PROPERTY.value in new_types
        ):
            # An explicit empty SET/REPLACE means the renter selected Any.
            additional[CONFIGURATION_ANSWERED_MARKER] = 'true'
        validated['additional_preferences'] = additional

        if (
            ListingType.ENTIRE_PROPERTY.value in new_types
            and not configurations
            and str(additional.get(CONFIGURATION_ANSWERED_MARKER) or '').casefold()
            != 'true'
        ):
            raise ValueError(
                'An entire-property search needs a home configuration or explicit Any'
            )
        return validated

    def _get_requirement_snapshot(self, search_id: UUID) -> dict:
        result = (
            self.db.table('search_requirements')
            .select('*')
            .eq('search_id', str(search_id))
            .execute()
        )
        rows = result.data or []
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise ValueError('Search requirements are missing')
        return dict(rows[0])

    def _persist_live_search_snapshot(
        self,
        user_id: UUID,
        search_id: UUID,
        requirements: dict,
        raw_text: str,
        *,
        expected_version: int,
    ) -> tuple[int, dict]:
        '''Validate and atomically persist one complete requirements snapshot.'''

        self._validate_expected_version(expected_version)
        history = str(requirements.get('raw_requirement_text') or '').strip()[-6000:]
        update_text = str(raw_text or '').strip()[:2000]
        combined_raw = (
            f'{history}\nUpdate: {update_text}'.strip()
            if update_text
            else history
        )[-8000:]
        typed_requirements = self._typed_requirements_from_mapping(requirements)
        params = self._rpc_requirement_params(typed_requirements, combined_raw)
        params.update({
            'p_user_id': str(user_id),
            'p_search_id': str(search_id),
            'p_expected_version': int(expected_version),
        })
        row = self._execute_lifecycle_rpc('update_live_renter_search', params)
        session = self._session_from_rpc_row(row)
        persisted = row.get('requirements')
        if (
            session.id != search_id
            or session.version != int(expected_version) + 1
            or not isinstance(persisted, dict)
        ):
            raise RuntimeError('Search update returned an invalid result')
        return session.version, persisted

    @classmethod
    def _typed_requirements_from_mapping(
        cls,
        requirements: Mapping[str, Any],
    ) -> RequirementExtractionResponse:
        '''Validate a complete stored snapshot before it reaches the typed RPC.'''

        fields = {
            key: requirements.get(key)
            for key in cls._requirements_payload_fields()
            if key != 'raw_requirement_text'
        }
        try:
            typed = RequirementExtractionResponse(is_complete=True, **fields)
        except (TypeError, ValueError) as error:
            raise ValueError('The updated search requirements are invalid') from error
        missing = cls.missing_core_requirements(typed)
        if missing:
            raise ValueError(
                f"This update would remove required search details: {', '.join(missing)}"
            )
        return typed

    @staticmethod
    def _validate_expected_version(expected_version: int) -> None:
        try:
            normalized = int(expected_version)
        except (TypeError, ValueError) as error:
            raise ValueError('A positive expected search version is required') from error
        if normalized <= 0:
            raise ValueError('A positive expected search version is required')

    @classmethod
    def _rpc_requirement_params(
        cls,
        requirements: RequirementExtractionResponse,
        raw_text: str,
    ) -> dict:
        '''Build the typed RPC payload after deterministic local validation.'''

        missing = cls.missing_core_requirements(requirements)
        if missing:
            raise ValueError(f"Cannot save a search without: {', '.join(missing)}")

        try:
            listing_types = [
                ListingType(item.value if hasattr(item, 'value') else str(item)).value
                for item in requirements.listing_types
            ]
        except ValueError as error:
            raise ValueError('The listing type is not supported') from error

        target_rent = (
            requirements.target_rent
            if requirements.target_rent is not None
            else requirements.max_rent
        )
        max_rent = requirements.max_rent
        if target_rent is None or max_rent is None:
            raise ValueError('Target rent and maximum rent are required')
        if target_rent <= 0 or max_rent <= 0 or target_rent > max_rent:
            raise ValueError(
                'The target rent must be positive and no higher than the maximum rent'
            )

        preferred_date = cls._normalize_rpc_date(
            requirements.preferred_move_in_date,
            'preferred move-in date',
        )
        latest_date = cls._normalize_rpc_date(
            requirements.latest_move_in_date,
            'latest move-in date',
        )
        if preferred_date and latest_date and preferred_date > latest_date:
            raise ValueError(
                'The preferred move-in date cannot be after the latest move-in date'
            )

        payload = cls._requirements_payload(requirements, raw_text)
        configurations = cls._clean_optional_text_list(
            payload.get('preferred_property_configurations')
        )
        normalized_configurations = [
            item.upper().replace(' ', '') for item in (configurations or [])
        ]
        if any(
            item not in ALLOWED_PROPERTY_CONFIGURATIONS
            for item in normalized_configurations
        ):
            raise ValueError('The home configuration is not supported')
        return {
            'p_listing_types': listing_types,
            'p_preferred_locations': cls._clean_text_list(
                payload.get('preferred_locations')
            ),
            'p_target_rent': int(target_rent),
            'p_max_rent': int(max_rent),
            'p_acceptable_locations': cls._clean_text_list(
                payload.get('acceptable_locations')
            ),
            'p_excluded_locations': cls._clean_text_list(
                payload.get('excluded_locations')
            ),
            'p_work_location': cls._clean_optional_text(payload.get('work_location')),
            'p_preferred_move_in_date': preferred_date,
            'p_latest_move_in_date': latest_date,
            'p_preferred_property_configurations': normalized_configurations,
            'p_core_preferences': payload.get('core_preferences') or {},
            'p_additional_preferences': payload.get('additional_preferences') or {},
            'p_raw_requirement_text': raw_text,
        }

    def _execute_lifecycle_rpc(self, name: str, params: dict) -> dict:
        try:
            response = self.db.rpc(name, params).execute()
        except Exception as error:
            self._raise_lifecycle_error(name, error)
        rows = response.data or []
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            logger.error(
                'Renter search lifecycle RPC returned an invalid row count',
                extra={'rpc': name, 'row_count': len(rows) if isinstance(rows, list) else None},
            )
            raise RuntimeError('Search persistence returned an invalid response')
        return rows[0]

    @classmethod
    def _raise_lifecycle_error(cls, rpc_name: str, error: Exception) -> None:
        raw_error = str(error)
        messages = sorted(
            cls.LIFECYCLE_ERROR_MESSAGES.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        for code, (error_type, safe_message) in messages:
            if code in raw_error:
                raise error_type(safe_message) from error
        logger.exception(
            'Renter search lifecycle RPC failed',
            extra={'rpc': rpc_name},
        )
        raise RuntimeError('Search persistence failed; please try again') from error

    @staticmethod
    def _session_from_rpc_row(row: dict) -> SearchSession:
        session = row.get('session')
        if not isinstance(session, dict):
            raise RuntimeError('Search persistence returned an invalid session')
        return SearchSession(**session)

    @staticmethod
    def _optional_session_from_rpc_value(
        value: Any,
        label: str,
    ) -> Optional[SearchSession]:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise RuntimeError(f'Search cancellation returned an invalid {label}')
        return SearchSession(**value)

    @staticmethod
    def _normalize_rpc_date(value: Any, label: str) -> Optional[str]:
        if value in (None, ''):
            return None
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        try:
            return date.fromisoformat(str(value)).isoformat()
        except (TypeError, ValueError) as error:
            raise ValueError(
                f'{label.capitalize()} must be an exact date in YYYY-MM-DD format'
            ) from error

    @staticmethod
    def _clean_text_list(values: Any) -> list[str]:
        return [
            text
            for item in (values or [])
            if (text := str(item).strip())
        ]

    @classmethod
    def _clean_optional_text_list(cls, values: Any) -> Optional[list[str]]:
        if values is None:
            return None
        return cls._clean_text_list(values)

    @staticmethod
    def _clean_optional_text(value: Any) -> Optional[str]:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @staticmethod
    def _plain_value(value: Any) -> Any:
        if hasattr(value, 'model_dump'):
            return value.model_dump()
        if isinstance(value, dict):
            return {key: RequirementService._plain_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [RequirementService._plain_value(item) for item in value]
        return value

    @staticmethod
    def _requirements_payload_fields() -> set[str]:
        return {
            'listing_types', 'preferred_locations', 'acceptable_locations',
            'excluded_locations', 'work_location', 'target_rent', 'max_rent',
            'preferred_move_in_date', 'latest_move_in_date',
            'preferred_property_configurations', 'additional_preferences',
            'raw_requirement_text', 'core_preferences',
        }

    def _queue_match_job(self, job_type: str, search_id: UUID, version: int, trigger: str) -> None:
        key = f"{job_type}:{search_id}:{version}"
        try:
            self.db.table("agent_jobs").insert({
                "job_type": job_type, "idempotency_key": key, "status": "PENDING",
                "payload": {"search_id": str(search_id), "search_version": version, "trigger": trigger},
                "run_after": self._now(),
            }).execute()
        except Exception as error:
            if "duplicate" not in str(error).lower() and "unique" not in str(error).lower():
                raise

    def _insert_requirements(self, search_id: UUID, requirements: RequirementExtractionResponse, raw_text: str) -> None:
        payload = self._requirements_payload(requirements, raw_text)
        payload["search_id"] = str(search_id)
        self.db.table("search_requirements").insert(payload).execute()

    @staticmethod
    def _requirements_payload(requirements: RequirementExtractionResponse, raw_text: str) -> dict:
        return {
            "listing_types": requirements.listing_types,
            "preferred_locations": requirements.preferred_locations,
            "acceptable_locations": requirements.acceptable_locations,
            "excluded_locations": requirements.excluded_locations,
            "work_location": requirements.work_location,
            "target_rent": (
                requirements.target_rent
                if requirements.target_rent is not None
                else requirements.max_rent
            ),
            "max_rent": requirements.max_rent,
            "preferred_move_in_date": requirements.preferred_move_in_date,
            "latest_move_in_date": requirements.latest_move_in_date,
            "preferred_property_configurations": requirements.preferred_property_configurations,
            "additional_preferences": requirements.additional_preferences,
            "raw_requirement_text": raw_text,
            "core_preferences": {key: value.model_dump() for key, value in requirements.core_preferences.items()},
        }

    @staticmethod
    def _missing_persisted_core_requirements(requirements: dict) -> list[str]:
        missing = []
        if not requirements.get("listing_types"):
            missing.append("listing type")
        if not (requirements.get("preferred_locations") or requirements.get("acceptable_locations")):
            missing.append("location")
        if not requirements.get("max_rent"):
            missing.append("maximum budget")
        if not (requirements.get("preferred_move_in_date") or requirements.get("latest_move_in_date")):
            missing.append("move-in timing")
        return missing

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
