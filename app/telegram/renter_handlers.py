from aiogram import Router, F, BaseMiddleware
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.utils.chat_action import ChatActionSender
from aiogram.enums import ChatAction
from app.telegram.states import RenterState
from app.requirements.service import (
    CreationKeyPayloadMismatch,
    RequirementService,
)
from app.requirements.collector import (
    CollectionMode,
    CollectionProgress,
    ConflictResolution,
    PendingRequirementConflict,
    RenterRequirementDraft,
    RequirementField,
    RequirementTurnPatch,
    REQUIRED_PROMPTS,
    advance_collection_progress,
    apply_requirement_patch,
    build_requirement_patch_prompt,
    combine_requirement_turn_patches,
    collection_signature,
    describe_requirement_changes,
    detect_collection_control_intents,
    next_required_field,
    next_requirement_prompt,
    parse_requirement_turn,
    requirement_turn_needs_enrichment,
    resolve_requirement_conflict,
    split_terminal_finish_phrase,
    split_terminal_summary_phrase,
    strip_routed_non_requirement_clauses,
    validate_requirement_turn_patch_grounding,
)
from app.db.client import get_supabase_client
from app.config import settings
from app.common.tracer import tracer
from uuid import UUID, uuid4
from datetime import datetime, timezone
from html import escape
import logging
from typing import Optional

from app.requirements.presentation import (
    format_requirement_diff,
    format_requirements,
    missing_core_fields,
)
from app.requirements.schemas import (
    RequirementChangeOperation,
    RequirementEditPlan,
)
from app.telegram.command_menus import is_admin_menu_active
from app.telegram.renter_conversation import (
    PendingRenterAction,
    RenterConversationService,
    RenterIntent,
    RenterTurnDecision,
)
from app.common.enums import SearchStatus

class _AdminModeCallbackGuard(BaseMiddleware):
    """Prevent stale renter buttons from escaping explicit admin-menu mode."""

    async def __call__(self, handler, event, data):
        message = getattr(event, 'message', None)
        chat = getattr(message, 'chat', None)
        if chat is not None and is_admin_menu_active(chat.id):
            await event.answer(
                'Switch to renter mode with /renter before using this button.',
                show_alert=True,
            )
            return None
        return await handler(event, data)


router = Router()
router.callback_query.outer_middleware(_AdminModeCallbackGuard())
req_service = RequirementService()
conversation_service = RenterConversationService()
logger = logging.getLogger(__name__)

# Words that signal "I'm done, start searching"
DONE_PHRASES = {"no", "nope", "nothing", "that's it", "thats it", "start searching",
                "go ahead", "begin", "nah", "all good", "nothing else",
                "no thanks", "search", "lets go", "let's go", "begin searching"}

CONFIRM_ACTION_CALLBACK = 'renter:confirm'
DECLINE_ACTION_CALLBACK = 'renter:keep'
REVIEW_START_CALLBACK = 'r:review:start'
REVIEW_EDIT_CALLBACK = 'r:review:edit'
REVIEW_PREFS_CALLBACK = 'r:review:prefs'
REVIEW_CANCEL_CALLBACK = 'r:review:cancel'
REVIEW_START_PREFIX = 'r:rv:s:'
REVIEW_EDIT_PREFIX = 'r:rv:e:'
REVIEW_PREFS_PREFIX = 'r:rv:p:'
REVIEW_CANCEL_PREFIX = 'r:rv:c:'
CONFLICT_USE_CALLBACK = 'r:conflict:use'
CONFLICT_KEEP_CALLBACK = 'r:conflict:keep'
CONFLICT_ADD_CALLBACK = 'r:conflict:add'
CONFLICT_EDIT_CALLBACK = 'r:conflict:edit'
GUIDED_CALLBACK_PREFIX = 'r:guided:'
GUIDED_CANCEL_CALLBACK = 'r:guided:cancel'
EDIT_CALLBACK_PREFIX = 'r:edit:'
CANCEL_SETUP_CALLBACK = 'r:cancel:setup'
CANCEL_ACTIVE_CALLBACK = 'r:cancel:active'
CANCEL_BOTH_CALLBACK = 'r:cancel:both'
CANCEL_KEEP_CALLBACK = 'r:cancel:keep'


def _confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='Confirm', callback_data=CONFIRM_ACTION_CALLBACK),
        InlineKeyboardButton(text='Keep current', callback_data=DECLINE_ACTION_CALLBACK),
    ]])


def _review_reference(
    prefix: str,
    search_id: Optional[str],
    search_version: Optional[int],
    fallback: str,
) -> str:
    if search_id and search_version:
        return f'{prefix}{search_id}:{int(search_version)}'
    if search_id:
        return prefix + str(search_id)
    return fallback


def _review_keyboard(
    search_id: Optional[str] = None,
    search_version: Optional[int] = None,
) -> InlineKeyboardMarkup:
    callback_values = {
        'start': _review_reference(
            REVIEW_START_PREFIX, search_id, search_version, REVIEW_START_CALLBACK,
        ),
        'edit': _review_reference(
            REVIEW_EDIT_PREFIX, search_id, search_version, REVIEW_EDIT_CALLBACK,
        ),
        'preferences': _review_reference(
            REVIEW_PREFS_PREFIX, search_id, search_version, REVIEW_PREFS_CALLBACK,
        ),
        'cancel': _review_reference(
            REVIEW_CANCEL_PREFIX, search_id, search_version, REVIEW_CANCEL_CALLBACK,
        ),
    }
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Start Search', callback_data=callback_values['start'])],
        [
            InlineKeyboardButton(text='Edit', callback_data=callback_values['edit']),
            InlineKeyboardButton(text='Add preferences', callback_data=callback_values['preferences']),
        ],
        [InlineKeyboardButton(text='Cancel setup', callback_data=callback_values['cancel'])],
    ])


def _conflict_keyboard(
    conflict: Optional[PendingRequirementConflict] = None,
) -> InlineKeyboardMarkup:
    first_row = [
        InlineKeyboardButton(text='Use new', callback_data=CONFLICT_USE_CALLBACK),
        InlineKeyboardButton(text='Keep current', callback_data=CONFLICT_KEEP_CALLBACK),
    ]
    conflicting_operation = (
        conflict.staged_patch.operations[conflict.operation_index]
        if conflict
        else None
    )
    if conflict and conflict.field in {
        RequirementField.HOME_CONFIGURATIONS,
        RequirementField.PREFERRED_LOCATIONS,
        RequirementField.ACCEPTABLE_LOCATIONS,
    } and conflicting_operation.operation.value != 'REMOVE':
        first_row.insert(
            0,
            InlineKeyboardButton(text='Add new', callback_data=CONFLICT_ADD_CALLBACK),
        )
    return InlineKeyboardMarkup(inline_keyboard=[
        first_row,
        [InlineKeyboardButton(text='Edit', callback_data=CONFLICT_EDIT_CALLBACK)],
    ])


def _edit_fields_keyboard(
    search_id: Optional[str] = None,
    search_version: Optional[int] = None,
) -> InlineKeyboardMarkup:
    def callback(key: str) -> str:
        return (
            f'{EDIT_CALLBACK_PREFIX}{key}:{search_id}:{int(search_version)}'
            if search_id and search_version
            else f'{EDIT_CALLBACK_PREFIX}{key}:{search_id}'
            if search_id
            else f'{EDIT_CALLBACK_PREFIX}{key}'
        )

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text='Renting', callback_data=callback('arr')),
            InlineKeyboardButton(text='Home type', callback_data=callback('cfg')),
        ],
        [
            InlineKeyboardButton(text='Areas', callback_data=callback('loc')),
            InlineKeyboardButton(text='Budget', callback_data=callback('budget')),
        ],
        [InlineKeyboardButton(text='Move-in', callback_data=callback('move'))],
        [InlineKeyboardButton(text='Back to review', callback_data=callback('back'))],
    ])


def _cancel_scope_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Discard setup', callback_data=CANCEL_SETUP_CALLBACK)],
        [InlineKeyboardButton(text='Stop active search', callback_data=CANCEL_ACTIVE_CALLBACK)],
        [InlineKeyboardButton(text='Cancel both', callback_data=CANCEL_BOTH_CALLBACK)],
        [InlineKeyboardButton(text='Keep everything', callback_data=CANCEL_KEEP_CALLBACK)],
    ])


def _configured_collection_mode() -> CollectionMode:
    return CollectionMode(str(settings.renter_collection_mode).upper())


async def _initialize_collection(
    state: FSMContext,
    *,
    replacement_search_id: Optional[str] = None,
    replacement_search_version: Optional[int] = None,
) -> None:
    draft = RenterRequirementDraft()
    progress = CollectionProgress(mode=_configured_collection_mode())
    await state.set_state(RenterState.waiting_for_requirement)
    await state.update_data(
        chat_history=[],
        recent_turns=[],
        raw_user_turns=[],
        parsed_reqs={},
        collection_draft=draft.model_dump(mode='json'),
        collection_progress=progress.model_dump(mode='json'),
        requested_field=RequirementField.RENTAL_ARRANGEMENT.value,
        creation_key=str(uuid4()),
        creation_recovery_required=False,
        search_id=None,
        search_version=None,
        pending_action=None,
        pending_requirement_conflict=None,
        pending_conflict_message_id=None,
        replacement_search_id=replacement_search_id,
        replacement_search_version=replacement_search_version,
        unconfirmed_replacement_search_id=None,
        unconfirmed_replacement_search_version=None,
    )


def _load_collection_draft(data: dict) -> RenterRequirementDraft:
    raw = data.get('collection_draft') or data.get('parsed_reqs') or {}
    return RenterRequirementDraft.from_requirements(raw)


def _load_collection_progress(data: dict) -> CollectionProgress:
    raw = data.get('collection_progress')
    if raw:
        return CollectionProgress(**raw)
    return CollectionProgress(mode=_configured_collection_mode())


async def _save_collection_state(
    state: FSMContext,
    draft: RenterRequirementDraft,
    progress: CollectionProgress,
    *,
    requested_field: Optional[RequirementField] = None,
    pending_conflict: Optional[PendingRequirementConflict] = None,
    extra: Optional[dict] = None,
) -> None:
    values = {
        'collection_draft': draft.model_dump(mode='json'),
        'parsed_reqs': draft.to_requirement_dict(),
        'collection_progress': progress.model_dump(mode='json'),
        'requested_field': (
            requested_field.value
            if requested_field
            else None
        ),
        'pending_requirement_conflict': (
            pending_conflict.model_dump(mode='json')
            if pending_conflict
            else None
        ),
    }
    if extra:
        values.update(extra)
    await state.update_data(**values)


async def _restore_owned_draft_state(
    state: FSMContext,
    user_id: UUID,
    recovered,
) -> tuple[RenterRequirementDraft, CollectionProgress]:
    '''Restore one exact durable DRAFT into the canonical collection state.'''
    draft = RenterRequirementDraft.from_requirements(recovered.requirements)
    progress = CollectionProgress(mode=_configured_collection_mode())
    try:
        open_search, _ = req_service.get_current_search(user_id)
    except ValueError:
        open_search = None
    raw_text = str(recovered.requirements.get('raw_requirement_text') or '')
    await _save_collection_state(
        state,
        draft,
        progress,
        requested_field=next_required_field(draft),
        extra={
            'user_id': str(user_id),
            'search_id': str(recovered.session.id),
            'search_version': int(recovered.session.version),
            'persisted_snapshot_hash': collection_signature(draft),
            'persistence_pending': False,
            'unconfirmed_replacement_search_id': (
                str(open_search.get('id')) if open_search else None
            ),
            'unconfirmed_replacement_search_version': (
                int(open_search.get('version') or 1) if open_search else None
            ),
            'raw_user_turns': raw_text.splitlines()[-20:],
        },
    )
    await state.set_state(
        RenterState.waiting_for_requirement
        if draft.missing_required_fields()
        else RenterState.reviewing_requirements
    )
    return draft, progress


def _guided_keyboard(field: Optional[RequirementField]) -> Optional[InlineKeyboardMarkup]:
    rows = []
    if field == RequirementField.RENTAL_ARRANGEMENT:
        rows = [
            [InlineKeyboardButton(text='Entire property', callback_data='r:guided:entire')],
            [InlineKeyboardButton(text='Private room', callback_data='r:guided:private')],
            [InlineKeyboardButton(text='Shared room', callback_data='r:guided:shared')],
        ]
    elif field == RequirementField.HOME_CONFIGURATIONS:
        rows = [
            [
                InlineKeyboardButton(text='1BHK', callback_data='r:guided:1bhk'),
                InlineKeyboardButton(text='2BHK', callback_data='r:guided:2bhk'),
            ],
            [
                InlineKeyboardButton(text='3BHK', callback_data='r:guided:3bhk'),
                InlineKeyboardButton(text='Any', callback_data='r:guided:anybhk'),
            ],
        ]
    elif field == RequirementField.BUDGET:
        rows = [
            [
                InlineKeyboardButton(text='₹20k', callback_data='r:guided:20k'),
                InlineKeyboardButton(text='₹30k', callback_data='r:guided:30k'),
            ],
            [
                InlineKeyboardButton(text='₹40k', callback_data='r:guided:40k'),
                InlineKeyboardButton(text='Type another', callback_data='r:guided:other'),
            ],
        ]
    elif field == RequirementField.MOVE_IN_TIMING:
        rows = [
            [InlineKeyboardButton(text='Tomorrow', callback_data='r:guided:tomorrow')],
            [InlineKeyboardButton(text='Within 2 weeks', callback_data='r:guided:2weeks')],
            [InlineKeyboardButton(text='First week next month', callback_data='r:guided:firstweek')],
        ]
    rows.append([
        InlineKeyboardButton(text='Cancel setup', callback_data=GUIDED_CANCEL_CALLBACK),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _guided_terminal_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Try again', callback_data='r:guided:retry')],
        [InlineKeyboardButton(text='Cancel setup', callback_data=GUIDED_CANCEL_CALLBACK)],
    ])


def _guided_example(field: Optional[RequirementField]) -> str:
    return {
        RequirementField.RENTAL_ARRANGEMENT: 'Private room',
        RequirementField.HOME_CONFIGURATIONS: '2BHK or 3BHK',
        RequirementField.PREFERRED_LOCATIONS: 'Kondapur or Gachibowli',
        RequirementField.BUDGET: '30k',
        RequirementField.MOVE_IN_TIMING: 'First week next month',
    }.get(field, 'Parking preferred')


async def _persist_collection_draft(
    message: Message,
    state: FSMContext,
    draft: RenterRequirementDraft,
    *,
    telegram_user=None,
    raw_turns: Optional[list[str]] = None,
) -> dict:
    data = await state.get_data()
    user_id = data.get('user_id') or await get_or_create_user(message, telegram_user)
    accepted_turns = (
        list(raw_turns)
        if raw_turns is not None
        else list(data.get('raw_user_turns') or [])
    )
    raw_text = '\n'.join(accepted_turns)
    requirements = draft.to_extraction_response()
    search_id = data.get('search_id')
    if search_id:
        try:
            session = req_service.update_renter_search_draft(
                UUID(str(user_id)),
                UUID(str(search_id)),
                requirements,
                raw_text,
                expected_version=data.get('search_version'),
            )
        except Exception:
            # If the transaction committed but its HTTP response was lost,
            # recover the exact owned row and treat an exact payload match as
            # success. If another writer changed the row, reload both the
            # canonical requirements and version; never combine a newer
            # version with stale FSM requirements.
            try:
                recovered = req_service.get_owned_search_draft(
                    UUID(str(user_id)),
                    UUID(str(search_id)),
                )
                if recovered and req_service.recovered_draft_matches(
                    recovered,
                    requirements,
                    raw_text,
                    city=settings.flathunter_default_city,
                ):
                    session = recovered.session
                elif recovered:
                    recovered_draft = RenterRequirementDraft.from_requirements(
                        recovered.requirements,
                    )
                    await state.update_data(
                        collection_draft=recovered_draft.model_dump(mode='json'),
                        search_version=int(recovered.session.version),
                        persisted_snapshot_hash=collection_signature(
                            recovered_draft,
                        ),
                        raw_user_turns=str(
                            recovered.requirements.get('raw_requirement_text') or ''
                        ).splitlines(),
                    )
                    raise RuntimeError(
                        'This draft changed in another request. I reloaded the '
                        'latest saved version; please review your edit again.'
                    )
                else:
                    raise
            except Exception:
                raise
    else:
        creation_key = data.get('creation_key')
        if not creation_key:
            creation_key = str(uuid4())
            # Store the idempotency key before the network call. If the commit
            # succeeds but the response is lost, Retry must reuse this exact key.
            await state.update_data(creation_key=creation_key)
            data['creation_key'] = creation_key
        try:
            result = req_service.create_renter_search_draft(
                user_id,
                requirements,
                raw_text,
                creation_key=UUID(str(creation_key)),
                city=settings.flathunter_default_city,
            )
            session = result.session
        except CreationKeyPayloadMismatch:
            if not data.get('creation_recovery_required'):
                await state.update_data(creation_recovery_required=True)
                raise
            recovered = req_service.get_renter_search_draft_by_creation_key(
                UUID(str(user_id)),
                UUID(str(creation_key)),
            )
            if not recovered:
                raise RuntimeError(
                    'The saved draft could not be recovered; please retry'
                )
            session = req_service.update_renter_search_draft(
                UUID(str(user_id)),
                recovered.session.id,
                requirements,
                raw_text,
                expected_version=recovered.session.version,
            )
        except Exception:
            await state.update_data(creation_recovery_required=True)
            raise
        data['creation_key'] = creation_key
    return {
        'user_id': str(user_id),
        'creation_key': data.get('creation_key'),
        'search_id': str(session.id),
        'search_version': int(session.version),
        'persisted_snapshot_hash': collection_signature(draft),
        'creation_recovery_required': False,
        'persistence_pending': False,
    }


async def _show_requirement_review(
    message: Message,
    state: FSMContext,
    draft: RenterRequirementDraft,
    progress: CollectionProgress,
    *,
    telegram_user=None,
) -> bool:
    missing = draft.missing_required_fields()
    if missing:
        requested = missing[0]
        await _save_collection_state(
            state,
            draft,
            progress,
            requested_field=requested,
        )
        await state.set_state(RenterState.waiting_for_requirement)
        await message.answer(
            format_requirements(draft)
            + '\n\n'
            + next_requirement_prompt(draft, progress.mode),
            reply_markup=_guided_keyboard(requested) if progress.mode == CollectionMode.GUIDED else None,
        )
        return False
    data = await state.get_data()
    if (
        data.get('search_id')
        and data.get('persisted_snapshot_hash') == collection_signature(draft)
    ):
        persisted = {
            key: data.get(key)
            for key in {
                'user_id', 'creation_key', 'search_id', 'search_version',
                'persisted_snapshot_hash',
            }
        }
    else:
        try:
            persisted = await _persist_collection_draft(
                message,
                state,
                draft,
                telegram_user=telegram_user,
            )
        except Exception:
            logger.exception('Requirement draft persistence failed')
            actor = telegram_user or message.from_user
            tracer.log_event(
                'RENTER_COLLECTION_PERSISTENCE_FAILED',
                override_telegram_user_id=actor.id,
                payload={
                    'collection_mode': progress.mode.value,
                    'snapshot_hash': collection_signature(draft),
                    'failure_stage': 'draft_persistence',
                },
            )
            await _save_collection_state(
                state,
                draft,
                progress,
                extra={
                    'persisted_snapshot_hash': None,
                    'persistence_pending': True,
                },
            )
            await state.set_state(RenterState.reviewing_requirements)
            await message.answer(
                'I kept every requirement, but I could not save the review draft right now. '
                'Please try Start Search again, or edit a value first.',
                reply_markup=_review_keyboard(
                    data.get('search_id'), data.get('search_version'),
                ),
            )
            return False
    await _save_collection_state(
        state,
        draft,
        progress,
        extra=persisted,
    )
    await state.set_state(RenterState.reviewing_requirements)
    actor = telegram_user or message.from_user
    current_data = await state.get_data()
    tracer.log_event(
        'RENTER_COLLECTION_COMPLETED',
        override_telegram_user_id=actor.id,
        payload={
            'collection_mode': progress.mode.value,
            'collection_turns': len(current_data.get('raw_user_turns') or []),
            'guided_fallback': progress.mode == CollectionMode.GUIDED,
            'snapshot_hash': collection_signature(draft),
        },
    )
    await message.answer(
        format_requirements(draft, title='Review your search before starting'),
        reply_markup=_review_keyboard(
            persisted.get('search_id'), persisted.get('search_version'),
        ),
    )
    return True


def _trace_collection_turn(
    message: Message,
    draft: RenterRequirementDraft,
    progress: CollectionProgress,
    *,
    intents: list[str],
    patch: Optional[RequirementTurnPatch] = None,
    failure_stage: Optional[str] = None,
    telegram_user=None,
    state_name: Optional[str] = None,
) -> None:
    actor = telegram_user or message.from_user
    tracer.log_event(
        'RENTER_COLLECTION_TURN',
        override_telegram_user_id=actor.id,
        payload={
            'turn_id': str(uuid4()),
            'state': state_name or 'RENTER_REQUIREMENT_COLLECTION',
            'collection_mode': progress.mode.value,
            'intents': intents,
            'patch_fields': (
                [item.field.value for item in patch.operations]
                if patch
                else []
            ),
            'snapshot_hash': collection_signature(draft),
            'missing_fields': [item.value for item in draft.missing_required_fields()],
            'no_progress_count': progress.no_progress_count,
            'failure_stage': failure_stage,
        },
    )


async def _current_requirements(message: Message, state: FSMContext, telegram_user=None) -> tuple[dict, Optional[dict]]:
    data = await state.get_data()
    parsed = data.get('collection_draft') or data.get('parsed_reqs')
    if parsed:
        return dict(parsed), None
    user_id = await get_or_create_user(message, telegram_user)
    try:
        recovered = req_service.get_owned_search_draft(user_id)
        if recovered is not None:
            return (
                req_service.requirement_prompt_snapshot(recovered.requirements),
                recovered.session.model_dump(mode='json'),
            )
    except (ValueError, RuntimeError):
        logger.exception('Durable renter draft lookup failed during conversation routing')
    try:
        session, requirements = req_service.get_editable_search(user_id)
        return req_service.requirement_prompt_snapshot(requirements), session
    except ValueError:
        return {}, None


async def _classify_renter_turn(message: Message, state: FSMContext):
    data = await state.get_data()
    requirements, _ = await _current_requirements(message, state)
    return await conversation_service.classify(
        message.text or '',
        current_state=await state.get_state(),
        requirements=requirements,
        missing_fields=missing_core_fields(requirements),
        pending_action=data.get('pending_action'),
        recent_history=data.get('recent_turns') or [],
    )


async def _remember_turn(state: FSMContext, role: str, text: str) -> None:
    data = await state.get_data()
    history = list(data.get('recent_turns') or [])
    history.append({'role': role, 'text': text[:1000]})
    await state.update_data(recent_turns=history[-conversation_service.MAX_HISTORY_TURNS :])


async def _resume_flow(message: Message, state: FSMContext, requirements: Optional[dict] = None) -> None:
    current_state = await state.get_state()
    data = await state.get_data()
    if current_state == RenterState.confirming_requirement.state:
        raw_conflict = data.get('pending_requirement_conflict')
        if raw_conflict:
            conflict = PendingRequirementConflict(**raw_conflict)
            await message.answer(
                'Please choose how to handle the pending '
                + conflict.field.value.replace('_', ' ')
                + ' change.\n\nCurrent: '
                + escape(str(conflict.current_value))
                + '\nProposed: '
                + escape(str(conflict.proposed_value)),
                reply_markup=_conflict_keyboard(conflict),
            )
            return
        await state.set_state(RenterState.waiting_for_requirement)
        current_state = RenterState.waiting_for_requirement.state
    if current_state == RenterState.waiting_for_requirement.state:
        draft = _load_collection_draft(data)
        progress = _load_collection_progress(data)
        prompt = next_requirement_prompt(draft, progress.mode)
    elif current_state == RenterState.reviewing_requirements.state:
        await message.answer(
            'Your search is ready for review. Start it, edit it, add optional preferences, '
            'or cancel this setup.',
            reply_markup=_review_keyboard(
                data.get('search_id'), data.get('search_version'),
            ),
        )
        return
    else:
        prompt = conversation_service.resume_prompt(current_state, requirements)
    if prompt:
        data = await state.get_data()
        progress = _load_collection_progress(data)
        requested = next_required_field(_load_collection_draft(data))
        await message.answer(
            prompt,
            reply_markup=(
                _guided_keyboard(requested)
                if progress.mode == CollectionMode.GUIDED
                else None
            ),
        )


async def _show_current_requirements(message: Message, state: FSMContext, *, resume: bool = True) -> None:
    requirements, _ = await _current_requirements(message, state)
    data = await state.get_data()
    await message.answer(
        format_requirements(
            requirements,
            pending_change=data.get('pending_requirement_conflict'),
        )
    )
    if resume:
        await _resume_flow(message, state, requirements)


async def _set_pending_action(
    message: Message,
    state: FSMContext,
    pending: PendingRenterAction,
    prompt: str,
) -> None:
    await state.update_data(pending_action=pending.model_dump(mode='json'))
    await state.set_state(RenterState.confirming_conversational_action)
    sent = await message.answer(prompt, reply_markup=_confirmation_keyboard())
    confirmation_message_id = getattr(sent, 'message_id', None)
    if confirmation_message_id is not None:
        pending.confirmation_message_id = int(confirmation_message_id)
        await state.update_data(
            pending_action=pending.model_dump(mode='json'),
        )


async def _request_cancel_confirmation(
    message: Message,
    state: FSMContext,
    telegram_user=None,
) -> None:
    user_id = await get_or_create_user(message, telegram_user)
    data = await state.get_data()
    current_state = await state.get_state()
    setup_return_state = data.get('availability_return_state') or current_state
    has_setup = bool(
        data.get('collection_draft') is not None
        or data.get('parsed_reqs') is not None
    )
    if not has_setup:
        try:
            recovered = req_service.get_owned_search_draft(UUID(str(user_id)))
        except Exception:
            logger.exception('Could not check for a durable draft during cancellation')
            recovered = None
        if recovered:
            await _restore_owned_draft_state(
                state,
                UUID(str(user_id)),
                recovered,
            )
            data = await state.get_data()
            current_state = await state.get_state()
            setup_return_state = current_state
            has_setup = True
    try:
        session, _ = req_service.get_editable_search(user_id)
    except ValueError:
        session = None
    if has_setup and session:
        pending = PendingRenterAction(
            action='cancel_scope',
            return_state=setup_return_state,
            payload={
                'setup_search_id': data.get('search_id'),
                'setup_search_version': data.get('search_version'),
                'active_search_id': str(session.get('id')),
                'active_search_version': int(session.get('version') or 1),
            },
        )
        await state.update_data(pending_action=pending.model_dump(mode='json'))
        await state.set_state(RenterState.confirming_conversational_action)
        sent = await message.answer(
            'You have an unfinished setup and an active or paused search. What should I cancel?',
            reply_markup=_cancel_scope_keyboard(),
        )
        confirmation_message_id = getattr(sent, 'message_id', None)
        if confirmation_message_id is not None:
            pending.confirmation_message_id = int(confirmation_message_id)
            await state.update_data(
                pending_action=pending.model_dump(mode='json'),
            )
        return
    if has_setup:
        pending = PendingRenterAction(
            action='discard_setup',
            return_state=setup_return_state,
            search_id=(str(data.get('search_id')) if data.get('search_id') else None),
            search_version=data.get('search_version'),
        )
        await _set_pending_action(
            message,
            state,
            pending,
            'Discard this unfinished search setup? Your collected unsaved progress will be lost.',
        )
        return
    if not session:
        await message.answer('You do not have an unfinished setup or an active or paused search to cancel.')
        return
    pending = PendingRenterAction(
        action='cancel_search',
        return_state=await state.get_state(),
        search_id=str(session.get('id')),
        search_version=int(session.get('version') or 1),
    )
    await _set_pending_action(
        message,
        state,
        pending,
        'Canceling stops all matching and alerts for this search. Do you want to cancel it?',
    )


async def _handle_search_edit(
    message: Message,
    state: FSMContext,
    text: str,
    *,
    show_after: bool = False,
) -> None:
    user_id = await get_or_create_user(message)
    try:
        session, current = req_service.get_editable_search(user_id)
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            plan = await req_service.parse_search_edit_plan(text, current)
        if not plan.changes:
            await message.answer('I could not identify a specific change. For example, say add Madhapur or set my maximum rent to 25k.')
            return
        proposed = req_service.apply_edit_plan(current, plan)
        if all(current.get(field) == proposed.get(field) for field in req_service.EDITABLE_FIELDS):
            await message.answer('That is already part of your saved search, so I left it unchanged.')
            if show_after:
                await message.answer(format_requirements(current, title='Your current requirements'))
            return
        missing = missing_core_fields(proposed)
        if missing:
            missing_text = ', '.join(missing)
            await message.answer(f'I cannot leave an active search without {escape(missing_text)}. Please give me a replacement value instead.')
            return
        if req_service.edit_plan_is_risky(current, plan):
            pending = PendingRenterAction(
                action='edit_search',
                return_state=await state.get_state(),
                search_id=str(session.get('id')),
                search_version=int(session.get('version') or 1),
                payload={'plan': plan.model_dump(mode='json'), 'show_after': show_after},
                raw_text=text,
            )
            confirmation_text = format_requirement_diff(current, proposed) + '\n\nDo you want me to save these changes?'
            await _set_pending_action(message, state, pending, confirmation_text)
            return
        version, updated = req_service.update_live_search_from_plan(
            user_id,
            UUID(str(session.get('id'))),
            plan,
            text,
            expected_version=int(session.get('version') or 1),
        )
    except (ValueError, RuntimeError) as error:
        await message.answer(escape(str(error)))
        return
    await message.answer(f'Saved that update. I am checking the inventory again using search version {version}.')
    if show_after:
        await message.answer(format_requirements(updated, title='Your updated requirements'))
    await state.clear()


async def _confirm_pending_action(
    message: Message,
    state: FSMContext,
    telegram_user=None,
    *,
    callback_message_id: Optional[int] = None,
) -> None:
    data = await state.get_data()
    raw_pending = data.get('pending_action')
    if not raw_pending:
        await message.answer('That confirmation has expired. Please send the request again.')
        return
    pending = PendingRenterAction(**raw_pending)
    if await state.get_state() != RenterState.confirming_conversational_action.state:
        await message.answer('That confirmation has expired. Please send the request again.')
        return
    if (
        callback_message_id is not None
        and pending.confirmation_message_id != callback_message_id
    ):
        await message.answer('That button belongs to an older request, so I left the current setup unchanged.')
        return
    user_id = await get_or_create_user(message, telegram_user)
    actor = telegram_user or message.from_user
    try:
        if pending.action == 'cancel_search':
            req_service.cancel_renter_searches(
                UUID(str(user_id)),
                open_search_id=UUID(str(pending.search_id)),
                open_expected_version=int(pending.search_version or 0),
            )
            await message.answer('Your search is canceled. You will not receive further alerts for it.')
            tracer.log_event('SEARCH_CANCELLED', override_telegram_user_id=actor.id, payload={'search_id': pending.search_id})
            if pending.payload.get('preserve_setup') and pending.return_state:
                await state.update_data(pending_action=None)
                await state.set_state(pending.return_state)
                requirements, _ = await _current_requirements(
                    message,
                    state,
                    telegram_user,
                )
                await _resume_flow(message, state, requirements)
            else:
                await state.clear()
            return
        if pending.action == 'discard_setup':
            if pending.search_id and pending.search_version:
                req_service.cancel_renter_searches(
                    UUID(str(user_id)),
                    draft_search_id=UUID(str(pending.search_id)),
                    draft_expected_version=int(pending.search_version),
                )
            await state.clear()
            await message.answer('The unfinished setup was discarded. Your active search, if any, was not changed.')
            return
        if pending.action == 'cancel_both':
            setup_id = pending.payload.get('setup_search_id')
            setup_version = pending.payload.get('setup_search_version')
            req_service.cancel_renter_searches(
                UUID(str(user_id)),
                draft_search_id=(UUID(str(setup_id)) if setup_id else None),
                draft_expected_version=(
                    int(setup_version) if setup_id and setup_version else None
                ),
                open_search_id=UUID(str(pending.payload.get('active_search_id'))),
                open_expected_version=int(
                    pending.payload.get('active_search_version') or 0
                ),
            )
            await state.clear()
            await message.answer('The unfinished setup and active search were canceled.')
            return
        if pending.action == 'replace_search':
            await _initialize_collection(
                state,
                replacement_search_id=pending.search_id,
                replacement_search_version=pending.search_version,
            )
            if pending.raw_text:
                await message.answer(
                    'Your current search will stay active until the replacement starts. '
                    'I am applying the requirements you already sent.'
                )
                await _process_collection_turn(
                    message,
                    state,
                    text=pending.raw_text,
                    telegram_user=telegram_user,
                )
            else:
                await message.answer(
                    'Your current search will stay active until the replacement is reviewed '
                    'and starts successfully. Tell me what you want in the new search.'
                )
            return
        if pending.action == 'resume_replacement_review':
            await state.update_data(
                pending_action=None,
                replacement_search_id=pending.payload.get('replacement_search_id'),
                replacement_search_version=pending.payload.get(
                    'replacement_search_version'
                ),
                unconfirmed_replacement_search_id=None,
                unconfirmed_replacement_search_version=None,
            )
            await state.set_state(RenterState.reviewing_requirements)
            current = await state.get_data()
            await message.answer(
                'Confirmed. Your current search will remain live until this reviewed '
                'replacement starts successfully.',
                reply_markup=_review_keyboard(
                    current.get('search_id'), current.get('search_version'),
                ),
            )
            return
        if pending.action == 'reset_setup':
            if pending.search_id and pending.search_version:
                req_service.cancel_renter_searches(
                    UUID(str(user_id)),
                    draft_search_id=UUID(str(pending.search_id)),
                    draft_expected_version=int(pending.search_version),
                )
            await _initialize_collection(
                state,
                replacement_search_id=pending.payload.get('replacement_search_id'),
                replacement_search_version=pending.payload.get('replacement_search_version'),
            )
            await message.answer(
                'Started a fresh setup. Are you looking for an entire flat/property, '
                'a private room, or a shared room?'
            )
            return
        if pending.action == 'edit_search':
            plan = RequirementEditPlan(**pending.payload.get('plan', {}))
            version, updated = req_service.update_live_search_from_plan(
                user_id,
                UUID(str(pending.search_id)),
                plan,
                pending.raw_text or '',
                expected_version=pending.search_version,
            )
            await message.answer(f'Confirmed and saved. I am rematching your search using version {version}.')
            if pending.payload.get('show_after'):
                await message.answer(format_requirements(updated, title='Your updated requirements'))
            await state.clear()
            return
    except (ValueError, RuntimeError) as error:
        await message.answer(escape(str(error)))
        return
    await message.answer('That action is no longer available. Please send the request again.')
    await state.clear()


async def _decline_pending_action(
    message: Message,
    state: FSMContext,
    telegram_user=None,
    *,
    callback_message_id: Optional[int] = None,
) -> None:
    data = await state.get_data()
    raw_pending = data.get('pending_action')
    if not raw_pending:
        await message.answer('There is no pending change to keep or decline.')
        return
    pending = PendingRenterAction(**raw_pending)
    if await state.get_state() != RenterState.confirming_conversational_action.state:
        await message.answer('That confirmation has expired. I left your current setup unchanged.')
        return
    if (
        callback_message_id is not None
        and pending.confirmation_message_id != callback_message_id
    ):
        await message.answer('That button belongs to an older request, so I left the current setup unchanged.')
        return
    await state.update_data(pending_action=None)
    await state.set_state(pending.return_state)
    await message.answer('Kept your current search unchanged.')
    requirements, _ = await _current_requirements(message, state, telegram_user)
    await _resume_flow(message, state, requirements)


async def _activate_reviewed_search(
    message: Message,
    state: FSMContext,
    *,
    telegram_user=None,
) -> bool:
    data = await state.get_data()
    draft = _load_collection_draft(data)
    progress = _load_collection_progress(data)
    if draft.missing_required_fields():
        await _show_requirement_review(
            message, state, draft, progress, telegram_user=telegram_user,
        )
        return False
    if data.get('persisted_snapshot_hash') != collection_signature(draft):
        await _show_requirement_review(
            message, state, draft, progress, telegram_user=telegram_user,
        )
        return False
    search_id = data.get('search_id')
    user_id = data.get('user_id') or await get_or_create_user(message, telegram_user)
    if not search_id:
        await _show_requirement_review(
            message, state, draft, progress, telegram_user=telegram_user,
        )
        return False
    replacement_search_id = data.get('replacement_search_id')
    if replacement_search_id:
        try:
            current_open, _ = req_service.get_current_search(UUID(str(user_id)))
        except ValueError:
            await state.update_data(
                replacement_search_id=None,
                replacement_search_version=None,
            )
            await message.answer(
                'The search you planned to replace is no longer open. I left this '
                'draft unchanged; press Start Search again to start it normally.',
                reply_markup=_review_keyboard(search_id, data.get('search_version')),
            )
            return False
        current_open_id = str(current_open.get('id'))
        current_open_version = int(current_open.get('version') or 0)
        if (
            current_open_id != str(replacement_search_id)
            or current_open_version
            != int(data.get('replacement_search_version') or 0)
        ):
            pending = PendingRenterAction(
                action='resume_replacement_review',
                return_state=RenterState.reviewing_requirements.state,
                payload={
                    'replacement_search_id': current_open_id,
                    'replacement_search_version': current_open_version,
                },
            )
            await _set_pending_action(
                message,
                state,
                pending,
                'Your current search changed since this review was prepared. '
                'Confirm replacing its latest version before this draft starts?',
            )
            return False
    try:
        result = req_service.activate_renter_search(
            UUID(str(user_id)),
            UUID(str(search_id)),
            expected_version=data.get('search_version'),
            replace_search_id=(
                UUID(str(replacement_search_id))
                if replacement_search_id
                else None
            ),
            replace_expected_version=data.get('replacement_search_version'),
        )
    except Exception:
        logger.exception('Reviewed search activation failed')
        await message.answer(
            'I could not start the reviewed search right now. Your draft and any existing '
            'search are unchanged. Please retry Start Search or edit the review.',
            reply_markup=_review_keyboard(
                data.get('search_id'), data.get('search_version'),
            ),
        )
        return False
    session = result.session
    await message.answer('Your search is live. I will message you when I find matching properties.')
    actor = telegram_user or message.from_user
    tracer.log_event(
        'SEARCH_STARTED',
        override_telegram_user_id=actor.id,
        payload={
            'search_id': str(session.id),
            'activated': result.activated,
            'job_enqueued': result.job_enqueued,
            'replaced_search_id': (
                str(result.replaced_search_id) if result.replaced_search_id else None
            ),
        },
        override_search_id=str(session.id),
    )
    await state.clear()
    return True


async def _activate_draft_from_state(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await _show_requirement_review(
        message,
        state,
        _load_collection_draft(data),
        _load_collection_progress(data),
    )


async def _show_matches(message: Message) -> None:
    user_id = await get_or_create_user(message)
    try:
        session, _ = req_service.get_editable_search(user_id)
    except ValueError:
        await message.answer('You do not have an active or paused search with matches yet.')
        return
    db = get_supabase_client()
    result = db.table('matches').select('*').eq('search_id', session.get('id')).order('fit_score', desc=True).limit(10).execute()
    matches = [item for item in (result.data or []) if item.get('status') != 'SKIPPED']
    matches.sort(key=lambda item: float(item.get('fit_score') or 0), reverse=True)
    matches = matches[:5]
    if not matches:
        await message.answer('I have not found a reviewable match yet. I am still checking new listings.')
        return
    from app.jobs.worker import JobWorker, _match_action_keyboard
    result_suffix = 's' if len(matches) != 1 else ''
    await message.answer(f'I found {len(matches)} result{result_suffix} to review.')
    for rank, match in enumerate(matches, start=1):
        listing_result = db.table('listings').select('*').eq('id', match.get('listing_id')).execute()
        if not listing_result.data:
            continue
        listing = listing_result.data[0]
        card = JobWorker.build_match_card(rank, match, listing)
        await message.answer(card, reply_markup=_match_action_keyboard(str(match.get('id'))))


async def _show_referenced_property(message: Message) -> None:
    user_id = await get_or_create_user(message)
    try:
        session, _ = req_service.get_editable_search(user_id)
    except ValueError:
        await message.answer('You do not have a current search result to describe.')
        return
    db = get_supabase_client()
    result = db.table('matches').select('*').eq('search_id', session.get('id')).order('fit_score', desc=True).limit(10).execute()
    matches = [item for item in (result.data or []) if item.get('status') != 'SKIPPED']
    if not matches:
        await message.answer('There is no current property match to describe yet.')
        return
    if len(matches) > 1:
        rows = [[InlineKeyboardButton(text=f'Property {index}', callback_data='details_match_' + str(item.get('id')))]
                for index, item in enumerate(matches[:5], start=1)]
        await message.answer('Which property do you mean?', reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return
    match = matches[0]
    listing_result = db.table('listings').select('*').eq('id', match.get('listing_id')).execute()
    if not listing_result.data:
        await message.answer('That property is no longer available to review.')
        return
    from app.matching.details import clarification_labels, draft_property_narrative
    narrative = await draft_property_narrative(listing_result.data[0])
    clarifications = clarification_labels(match.get('missing_information'))
    suffix = ''
    if clarifications:
        suffix = '\n\nStill being confirmed with the owner: ' + ', '.join(clarifications) + '.'
    await message.answer(narrative + suffix, parse_mode=None)


async def _handle_conversational_decision(
    message: Message,
    state: FSMContext,
    decision,
    *,
    resume_flow: bool = True,
) -> bool:
    '''Execute classified intents except requirement collection input.'''
    if is_admin_menu_active(message.chat.id):
        return True
    intents = list(decision.intents)
    lifecycle = {RenterIntent.PAUSE_SEARCH, RenterIntent.RESUME_SEARCH, RenterIntent.CANCEL_SEARCH}
    if len(lifecycle.intersection(intents)) > 1:
        await message.answer('Those search actions conflict. Should I pause, resume, or cancel the search?')
        return True

    handled = False
    resume_after = False
    skip_show = False
    for intent in intents:
        if intent == RenterIntent.REQUIREMENT_INPUT:
            continue
        if intent == RenterIntent.EDIT_REQUIREMENTS:
            await _handle_search_edit(
                message,
                state,
                message.text or '',
                show_after=RenterIntent.SHOW_REQUIREMENTS in intents,
            )
            handled = True
            skip_show = True
        elif intent == RenterIntent.SHOW_REQUIREMENTS and not skip_show:
            await _show_current_requirements(message, state, resume=False)
            handled = True
            resume_after = True
        elif intent == RenterIntent.SHOW_STATUS:
            await cmd_mysearch(message, state)
            handled = True
            resume_after = True
        elif intent == RenterIntent.SHOW_MATCHES:
            await _show_matches(message)
            handled = True
            resume_after = True
        elif intent == RenterIntent.PROPERTY_DETAILS:
            await _show_referenced_property(message)
            handled = True
            resume_after = True
        elif intent == RenterIntent.PAUSE_SEARCH:
            await cmd_pause_search(message, state)
            handled = True
            resume_after = True
        elif intent == RenterIntent.RESUME_SEARCH:
            await cmd_resume_search(message, state)
            handled = True
            resume_after = True
        elif intent == RenterIntent.CANCEL_SEARCH:
            await _request_cancel_confirmation(message, state)
            handled = True
        elif intent == RenterIntent.START_SEARCH:
            data = await state.get_data()
            if data.get('collection_draft') is None:
                await cmd_start(message, state)
            else:
                await _activate_draft_from_state(message, state)
            handled = True
        elif intent == RenterIntent.SET_AVAILABILITY:
            normalized = (message.text or '').casefold().strip()
            if normalized in {'set availability', 'update availability', 'change availability'}:
                await cmd_set_availability(message, state)
            else:
                from app.scheduling.service import SchedulingService
                try:
                    user_id = await get_or_create_user(message)
                    await SchedulingService().parse_and_save_availability(user_id, None, message.text or '')
                    await message.answer('Saved your visit availability.')
                    resume_after = True
                except Exception:
                    logger.exception('Could not save renter availability')
                    await message.answer('I could not save that availability. Try something like weekends anytime or weekdays after 6 PM.')
            handled = True
        elif intent == RenterIntent.RENTAL_QUESTION:
            answer = await conversation_service.answer_rental_question(decision.rental_question or message.text or '')
            await message.answer(answer, parse_mode=None)
            handled = True
            resume_after = True
        elif intent == RenterIntent.CONFIRM:
            await _confirm_pending_action(message, state)
            handled = True
        elif intent == RenterIntent.DECLINE:
            await _decline_pending_action(message, state)
            handled = True
        elif intent == RenterIntent.GREETING:
            current_state = await state.get_state()
            if current_state:
                await message.answer('Hi! I still have your progress, so we can continue where we left off.')
                resume_after = True
            else:
                await cmd_start(message, state)
            handled = True
        elif intent == RenterIntent.HELP:
            await cmd_help(message, state)
            handled = True
        elif intent == RenterIntent.OUT_OF_SCOPE:
            await message.answer('I can help with your flat search, saved requirements, matches, visits, and general rental questions. I cannot handle that unrelated request.')
            handled = True
            resume_after = True
        elif intent == RenterIntent.AMBIGUOUS:
            clarification = decision.clarification_question or 'I am not sure whether you want to change your search, view it, or ask a rental question. Could you clarify?'
            await message.answer(clarification[:1000], parse_mode=None)
            handled = True

    if (
        resume_flow
        and resume_after
        and await state.get_state() != RenterState.confirming_conversational_action.state
    ):
        requirements, _ = await _current_requirements(message, state)
        await _resume_flow(message, state, requirements)
    return handled


async def get_or_create_user(message: Message, telegram_user=None) -> str:
    db = get_supabase_client()
    identity = telegram_user or message.from_user
    tg_id = identity.id
    res = db.table("users").select("*").eq("telegram_user_id", tg_id).execute()
    if res.data:
        return res.data[0]['id']
    
    new_user = db.table("users").insert({
        "telegram_user_id": tg_id,
        "telegram_username": identity.username,
        "display_name": identity.full_name,
        "role": "RENTER"
    }).execute()
    return new_user.data[0]['id']

async def check_and_handle_active_searches(
    message: Message,
    state: FSMContext,
    *,
    initial_requirement_text: Optional[str] = None,
) -> bool:
    """Returns True if the limit is hit and we are awaiting confirmation, False otherwise."""
    user_id = await get_or_create_user(message)
    try:
        current, _ = req_service.get_editable_search(UUID(str(user_id)))
    except ValueError:
        return False
    if settings.max_active_searches <= 1:
        pending = PendingRenterAction(
            action='replace_search',
            return_state=await state.get_state(),
            search_id=str(current.get('id')),
            search_version=int(current.get('version') or 1),
            raw_text=initial_requirement_text,
        )
        await _set_pending_action(
            message,
            state,
            pending,
            'You already have a live search. I will keep it running while we build the '
            'replacement and close it only when the new search starts. Continue?',
        )
        tracer.log_event(
            'SEARCH_LIMIT_HIT',
            override_telegram_user_id=message.from_user.id,
            payload={'active_count': 1, 'limit': settings.max_active_searches},
        )
        return True
    return False

# ─── COMMANDS (registered FIRST so they aren't swallowed by FSM states) ───

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    data = await state.get_data()
    has_setup = (
        'collection_draft' in data
        and data.get('collection_draft') is not None
    )
    if has_setup:
        current_state = await state.get_state()
        existing_pending = data.get('pending_action') or {}
        return_state = (
            data.get('availability_return_state')
            or existing_pending.get('return_state')
            or current_state
        )
        pending = PendingRenterAction(
            action='reset_setup',
            return_state=return_state,
            search_id=(str(data.get('search_id')) if data.get('search_id') else None),
            search_version=data.get('search_version'),
            payload={
                'replacement_search_id': data.get('replacement_search_id'),
                'replacement_search_version': data.get('replacement_search_version'),
            },
        )
        await _set_pending_action(
            message,
            state,
            pending,
            'Start over and discard the current unfinished setup?',
        )
        return
    user_id = UUID(str(await get_or_create_user(message)))
    try:
        recovered = req_service.get_owned_search_draft(user_id)
    except Exception:
        logger.exception('Could not check for an existing durable renter draft')
        recovered = None
    if recovered:
        draft, progress = await _restore_owned_draft_state(
            state,
            user_id,
            recovered,
        )
        if draft.missing_required_fields():
            await message.answer(
                format_requirements(draft, title='Resumed your saved search setup')
                + '\n\n'
                + next_requirement_prompt(draft, progress.mode),
                reply_markup=(
                    _guided_keyboard(next_required_field(draft))
                    if progress.mode == CollectionMode.GUIDED
                    else None
                ),
            )
        else:
            await message.answer(
                format_requirements(draft, title='Resumed your saved review'),
                reply_markup=_review_keyboard(
                    str(recovered.session.id), recovered.session.version,
                ),
            )
        return
    if await check_and_handle_active_searches(message, state):
        return
        
    await state.clear()
    await message.answer(
        "👋 Hey there! I'm FlatHunter, your personal flat-finding assistant.\n\n"
        "Tell me what you're looking for — area, budget, type of place — "
        "and I'll start hunting for you!"
    )
    await _initialize_collection(state)

@router.message(Command("cancel_search"))
async def cmd_cancel_search(message: Message, state: FSMContext):
    await _request_cancel_confirmation(message, state)

@router.message(Command("pause"))
async def cmd_pause_search(message: Message, state: FSMContext):
    user_id = UUID(str(await get_or_create_user(message)))
    try:
        search, _ = req_service.get_current_search(user_id)
        if search.get('status') != SearchStatus.ACTIVE.value:
            raise ValueError('No active search')
        transition = req_service.set_renter_search_paused(
            user_id,
            UUID(str(search['id'])),
            expected_version=int(search.get('version') or 0),
            paused=True,
        )
    except ValueError:
        await message.answer("You do not have an active search to pause.")
        return
    except RuntimeError:
        logger.exception('Could not pause renter search')
        await message.answer('I could not pause the search because it changed. Please retry.')
        return
    data = await state.get_data()
    updates = {}
    if str(data.get('replacement_search_id') or '') == str(search['id']):
        updates['replacement_search_version'] = int(transition.session.version)
    if str(data.get('unconfirmed_replacement_search_id') or '') == str(search['id']):
        updates['unconfirmed_replacement_search_version'] = int(
            transition.session.version
        )
    if updates:
        await state.update_data(**updates)
    await message.answer(
        "Your search is paused. I will not contact or notify you about new listings."
    )


@router.message(Command("resume"))
async def cmd_resume_search(message: Message, state: FSMContext):
    user_id = UUID(str(await get_or_create_user(message)))
    try:
        search, _ = req_service.get_current_search(user_id)
        if search.get('status') != SearchStatus.PAUSED.value:
            raise ValueError('No paused search')
        transition = req_service.set_renter_search_paused(
            user_id,
            UUID(str(search['id'])),
            expected_version=int(search.get('version') or 0),
            paused=False,
        )
    except ValueError:
        await message.answer("You do not have a paused search to resume.")
        return
    except RuntimeError:
        logger.exception('Could not resume renter search')
        await message.answer('I could not resume the search because it changed. Please retry.')
        return
    data = await state.get_data()
    updates = {}
    if str(data.get('replacement_search_id') or '') == str(search['id']):
        updates['replacement_search_version'] = int(transition.session.version)
    if str(data.get('unconfirmed_replacement_search_id') or '') == str(search['id']):
        updates['unconfirmed_replacement_search_version'] = int(
            transition.session.version
        )
    if updates:
        await state.update_data(**updates)
    await message.answer("Your search is active again. I am checking listings added while it was paused.")
@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    current_state = await state.get_state()
    
    if current_state == RenterState.waiting_for_requirement.state:
        help_text = (
            "🔍 <b>I'm waiting for your requirements!</b>\n\n"
            "Just tell me what you're looking for. For example:\n"
            "• <i>\"2BHK in Gachibowli under 30k\"</i>\n"
            "• <i>\"Private room near HITEC City, furnished\"</i>\n\n"
            "<b>Commands:</b>\n"
            "/start - Restart the search\n"
            "/cancel_search - Cancel your active search\n"
            "/help - Show this message"
        )
    elif current_state == RenterState.collecting_extras.state:
        help_text = (
            "✅ <b>I have your basic requirements!</b>\n\n"
            "You can add extra preferences like:\n"
            "• <i>\"Furnished with parking\"</i>\n"
            "• <i>\"No brokerage, near metro\"</i>\n\n"
            "Or say <b>\"start searching\"</b> to begin!\n\n"
            "<b>Commands:</b>\n"
            "/start - Restart the search\n"
            "/cancel_search - Cancel your active search\n"
            "/help - Show this message"
        )
    else:
        help_text = (
            "👋 <b>Welcome to FlatHunter!</b>\n\n"
            "I help you find the perfect flat. Here's what I can do:\n\n"
            "<b>Commands:</b>\n"
            "/mysearch - Check status & matches of your active search\n"
            "/start - Start or restart your flat search\n"
            "/editsearch - Update your saved search\n"
            "/pause - Pause alerts and outreach\n"
            "/resume - Resume your paused search\n"
            "/cancel_search - Cancel your active search\n"
            "/set_availability - Set when you're free for visits\n"
            "/help - Show this message\n\n"
            "Just say <b>hi</b> to get started!"
        )
    await message.answer(help_text, parse_mode="HTML")

@router.message(Command("mysearch"))
async def cmd_mysearch(message: Message, state: Optional[FSMContext] = None):
    db = get_supabase_client()
    tg_id = message.from_user.id
    has_setup = False
    if state is not None:
        data = await state.get_data()
        has_setup = (
            'collection_draft' in data
            and data.get('collection_draft') is not None
        )
        if has_setup:
            setup_state = await state.get_state()
            setup_status = (
                'READY FOR REVIEW'
                if setup_state == RenterState.reviewing_requirements.state
                else 'IN PROGRESS'
            )
            await message.answer(
                format_requirements(
                    _load_collection_draft(data),
                    title='Your unfinished search setup',
                    pending_change=data.get('pending_requirement_conflict'),
                )
                + f'\n\n<b>Setup status:</b> {setup_status}'
            )
    
    # 1. Get user
    user_res = db.table("users").select("id").eq("telegram_user_id", tg_id).execute()
    if not user_res.data:
        await message.answer(
            'Your setup above is not active yet.'
            if has_setup
            else "You don't have an active search yet! Say <b>hi</b> or tap /start to begin looking for a flat.",
            parse_mode="HTML",
        )
        return
        
    user_id = user_res.data[0]['id']

    if not has_setup:
        try:
            recovered = req_service.get_owned_search_draft(UUID(str(user_id)))
        except (ValueError, RuntimeError):
            logger.exception('Durable renter draft lookup failed for /mysearch')
            recovered = None
        if recovered is not None:
            has_setup = True
            await message.answer(
                format_requirements(
                    RenterRequirementDraft.from_requirements(
                        recovered.requirements,
                    ),
                    title='Your unfinished search setup',
                )
                + '\n\n<b>Setup status:</b> READY FOR REVIEW'
            )
    
    # 2. Get active search sessions
    session_res = db.table('search_sessions').select('id,status,created_at').eq('user_id', user_id).in_('status', ['ACTIVE', 'PAUSED']).order('created_at', desc=True).limit(1).execute()
    search_session = session_res.data[0] if session_res.data else None
    if not search_session:
        await message.answer(
            'You do not have any active searches right now; the setup above has not started.'
            if has_setup
            else "You don't have any active searches right now. Say <b>hi</b> or tap /start to start a search!",
            parse_mode="HTML",
        )
        return

    search_id = search_session['id']
    
    # 3. Get requirements
    req_res = db.table("search_requirements").select("*").eq("search_id", search_id).execute()
    req = req_res.data[0] if req_res.data else {}
    
    # 4. Get matches summary
    matches_res = db.table("matches").select("id, status, fit_score").eq("search_id", search_id).execute()
    total_matches = len(matches_res.data)
    strong_matches = len([m for m in matches_res.data if m.get("status") == "STRONG_MATCH"])
    qualifying_matches = len([m for m in matches_res.data if m.get("status") == "NEEDS_QUALIFICATION"])
    
    status = escape(str(search_session.get('status') or 'UNKNOWN'))
    summary = (
        format_requirements(req, title='Your rental search')
        + f'\n\n<b>Status:</b> {status}'
        + f'\n<b>Matches:</b> {total_matches}'
        + f'\n<b>Strong matches:</b> {strong_matches}'
        + f'\n<b>Being clarified:</b> {qualifying_matches}'
        + '\n\nYou can tell me naturally what to change, or use /editsearch.'
    )
    await message.answer(summary, parse_mode="HTML")

@router.message(Command("editsearch"))
async def cmd_edit_search(message: Message, state: FSMContext):
    user_id = await get_or_create_user(message)
    try:
        _, current = req_service.get_editable_search(user_id)
    except ValueError as error:
        await message.answer(str(error))
        return
    await message.answer(
        format_requirements(current, title='Your current requirements')
        + '\n\nTell me what you want to change. For example: increase my budget to 25k and include Madhapur.'
    )
    await state.set_state(RenterState.waiting_for_search_edit)


@router.message(RenterState.waiting_for_search_edit)
async def process_search_edit(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer('Please send the change as text, for example: budget up to 25k.')
        return
    decision = await _classify_renter_turn(message, state)
    if RenterIntent.EDIT_REQUIREMENTS in decision.intents:
        await _handle_conversational_decision(message, state, decision)
        return
    if await _handle_conversational_decision(message, state, decision):
        return
    await message.answer('What part of your saved search would you like to change?')


@router.message(Command("set_availability"))
async def cmd_set_availability(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state != RenterState.waiting_for_availability.state:
        await state.update_data(availability_return_state=current_state)
    await message.answer("When are you generally free to visit properties? (e.g., 'Weekends anytime, weekdays after 6 PM')")
    await state.set_state(RenterState.waiting_for_availability)

# ─── GREETING CATCH (after commands, before FSM states) ───

@router.message(F.text.lower().in_({"hi", "hello", "hey", "get started", "hey bot"}))
async def greeting_start(message: Message, state: FSMContext):
    if await state.get_state():
        decision = await _classify_renter_turn(message, state)
        await _handle_conversational_decision(message, state, decision)
        return
    if await check_and_handle_active_searches(message, state):
        return
        
    await state.clear()
    await message.answer(
        "👋 Hey there! I'm FlatHunter, your personal flat-finding assistant.\n\n"
        "Tell me what you're looking for — area, budget, type of place — "
        "and I'll start hunting for you!"
    )
    await _initialize_collection(state)

# ─── FSM STATE HANDLERS ───

@router.callback_query(F.data == CONFIRM_ACTION_CALLBACK)
async def confirm_conversational_action_callback(callback: CallbackQuery, state: FSMContext):
    await _confirm_pending_action(
        callback.message,
        state,
        callback.from_user,
        callback_message_id=getattr(callback.message, 'message_id', None),
    )
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == DECLINE_ACTION_CALLBACK)
async def decline_conversational_action_callback(callback: CallbackQuery, state: FSMContext):
    await _decline_pending_action(
        callback.message,
        state,
        callback.from_user,
        callback_message_id=getattr(callback.message, 'message_id', None),
    )
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer()


@router.message(RenterState.confirming_conversational_action)
async def process_conversational_confirmation(message: Message, state: FSMContext):
    if not message.text:
        await message.answer('Please use the buttons, or reply yes or no.')
        return
    decision = await _classify_renter_turn(message, state)
    if not {RenterIntent.CONFIRM, RenterIntent.DECLINE}.intersection(decision.intents):
        await message.answer('Please confirm the pending action or tell me to keep the current search.', reply_markup=_confirmation_keyboard())
        return
    await _handle_conversational_decision(message, state, decision)


async def _resolve_pending_requirement_conflict(
    message: Message,
    state: FSMContext,
    resolution: ConflictResolution,
    *,
    telegram_user=None,
) -> None:
    data = await state.get_data()
    raw_conflict = data.get('pending_requirement_conflict')
    if not raw_conflict:
        await message.answer('That requirement choice has expired. Please send the value again.')
        await state.set_state(RenterState.waiting_for_requirement)
        return
    draft = _load_collection_draft(data)
    progress = _load_collection_progress(data)
    conflict = PendingRequirementConflict(**raw_conflict)
    if (
        resolution == ConflictResolution.ADD_PROPOSED
        and conflict.staged_patch.operations[conflict.operation_index].operation.value
        == 'REMOVE'
    ):
        await message.answer(
            'That pending change removes a value, so it cannot be added. '
            'Choose Use new to remove it or Keep current.',
            reply_markup=_conflict_keyboard(conflict),
        )
        return
    try:
        merged = resolve_requirement_conflict(draft, conflict, resolution)
    except (TypeError, ValueError):
        logger.exception('Pending requirement conflict could not be resolved')
        await message.answer(
            'I kept the current value because that choice was no longer valid. '
            'Please send the change again.'
        )
        await state.set_state(RenterState.waiting_for_requirement)
        return
    if merged.pending_conflict:
        await _save_collection_state(
            state,
            draft,
            progress,
            requested_field=conflict.field,
            pending_conflict=merged.pending_conflict,
        )
        sent = await message.answer(
            'One more value conflicts with the current draft. Choose how to handle it.',
            reply_markup=_conflict_keyboard(merged.pending_conflict),
        )
        conflict_message_id = getattr(sent, 'message_id', None)
        if conflict_message_id is not None:
            await state.update_data(
                pending_conflict_message_id=int(conflict_message_id),
            )
        return

    updated = merged.draft
    raw_turns = list(data.get('raw_user_turns') or [])
    conflict_text = data.get('pending_conflict_raw_text')
    if merged.made_progress and conflict_text:
        raw_turns.append(str(conflict_text))
    requested = next_required_field(updated)
    updated_progress = advance_collection_progress(
        progress,
        updated,
        requested_field=requested,
        made_progress=merged.made_progress,
        next_prompt=next_requirement_prompt(updated, progress.mode),
    )
    persistence: dict = {}
    if (
        merged.made_progress
        and data.get('search_id')
        and not updated.missing_required_fields()
    ):
        try:
            persistence = await _persist_collection_draft(
                message,
                state,
                updated,
                telegram_user=telegram_user,
                raw_turns=raw_turns,
            )
        except Exception:
            logger.exception('Resolved conflict could not be persisted')
            await message.answer(
                'I could not save that choice, so I kept the last saved draft. Please retry.'
            )
            return
    await _save_collection_state(
        state,
        updated,
        updated_progress,
        requested_field=requested,
        extra={
            **persistence,
            'raw_user_turns': raw_turns,
            'pending_conflict_raw_text': None,
            'pending_conflict_message_id': None,
        },
    )
    if conflict_text:
        await _remember_turn(state, 'user', str(conflict_text))
    if requested is None:
        if merged.made_progress:
            changes = describe_requirement_changes(draft, updated)
            await message.answer('Saved: ' + '; '.join(changes))
        else:
            await message.answer('Kept the current value unchanged.')
        await _show_requirement_review(
            message, state, updated, updated_progress, telegram_user=telegram_user,
        )
        return
    await state.set_state(RenterState.waiting_for_requirement)
    prefix = 'Kept the current value.'
    if merged.made_progress:
        prefix = 'Saved: ' + '; '.join(describe_requirement_changes(draft, updated))
    await message.answer(
        prefix + '\n\n' + next_requirement_prompt(updated, updated_progress.mode),
        reply_markup=(
            _guided_keyboard(requested)
            if updated_progress.mode == CollectionMode.GUIDED
            else None
        ),
    )


@router.callback_query(F.data.in_({
    CONFLICT_USE_CALLBACK,
    CONFLICT_KEEP_CALLBACK,
    CONFLICT_ADD_CALLBACK,
}))
async def process_requirement_conflict_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    callback_message_id = getattr(callback.message, 'message_id', None)
    if (
        await state.get_state() != RenterState.confirming_requirement.state
        or not data.get('pending_requirement_conflict')
        or (
            callback_message_id is not None
            and data.get('pending_conflict_message_id') != callback_message_id
        )
    ):
        await callback.answer(
            'That requirement choice belongs to an older question.',
            show_alert=True,
        )
        return
    resolution = {
        CONFLICT_USE_CALLBACK: ConflictResolution.USE_PROPOSED,
        CONFLICT_KEEP_CALLBACK: ConflictResolution.KEEP_CURRENT,
        CONFLICT_ADD_CALLBACK: ConflictResolution.ADD_PROPOSED,
    }[callback.data]
    await _resolve_pending_requirement_conflict(
        callback.message, state, resolution, telegram_user=callback.from_user,
    )
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == CONFLICT_EDIT_CALLBACK)
async def process_requirement_conflict_edit(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    raw_conflict = data.get('pending_requirement_conflict')
    callback_message_id = getattr(callback.message, 'message_id', None)
    if (
        await state.get_state() != RenterState.confirming_requirement.state
        or not raw_conflict
        or (
            callback_message_id is not None
            and data.get('pending_conflict_message_id') != callback_message_id
        )
    ):
        await callback.answer('That choice expired. Please send the change again.', show_alert=True)
        return
    conflict = PendingRequirementConflict(**raw_conflict)
    await state.update_data(
        pending_requirement_conflict=None,
        pending_conflict_raw_text=None,
        pending_conflict_message_id=None,
        requested_field=conflict.field.value,
    )
    await state.set_state(RenterState.waiting_for_requirement)
    await callback.message.answer(REQUIRED_PROMPTS.get(conflict.field, 'What value should I use?'))
    await callback.answer()


@router.message(RenterState.confirming_requirement)
async def process_requirement_conflict_text(message: Message, state: FSMContext):
    normalized = (message.text or '').casefold().strip(' .!?')
    data = await state.get_data()
    raw_conflict = data.get('pending_requirement_conflict')
    if any(
        item.value == 'SHOW_SUMMARY'
        for item in detect_collection_control_intents(message.text or '')
    ):
        conflict = PendingRequirementConflict(**raw_conflict) if raw_conflict else None
        sent = await message.answer(
            format_requirements(
                _load_collection_draft(data),
                pending_change=conflict,
            ),
            reply_markup=_conflict_keyboard(conflict),
        )
        conflict_message_id = getattr(sent, 'message_id', None)
        if conflict_message_id is not None:
            await state.update_data(
                pending_conflict_message_id=int(conflict_message_id),
            )
        return
    if normalized in {'yes', 'use new', 'replace', 'confirm', 'go ahead'}:
        await _resolve_pending_requirement_conflict(
            message, state, ConflictResolution.USE_PROPOSED,
        )
        return
    if normalized in {'no', 'keep', 'keep current', 'never mind'}:
        await _resolve_pending_requirement_conflict(
            message, state, ConflictResolution.KEEP_CURRENT,
        )
        return
    if normalized in {'add', 'add it', 'keep both'}:
        await _resolve_pending_requirement_conflict(
            message, state, ConflictResolution.ADD_PROPOSED,
        )
        return
    sent = await message.answer(
        'Should I add or use the new value, keep the current value, or let you edit it?',
        reply_markup=_conflict_keyboard(
            PendingRequirementConflict(**raw_conflict) if raw_conflict else None
        ),
    )
    conflict_message_id = getattr(sent, 'message_id', None)
    if conflict_message_id is not None:
        await state.update_data(
            pending_conflict_message_id=int(conflict_message_id),
        )


async def _ensure_review_draft_state(
    callback: CallbackQuery,
    state: FSMContext,
    prefix: str,
) -> bool:
    '''Restore the exact owned durable review draft encoded in a callback.'''
    callback_data = callback.data or ''
    encoded_search_id = (
        callback_data.removeprefix(prefix)
        if callback_data.startswith(prefix)
        else None
    )
    expected_version = None
    if encoded_search_id and ':' in encoded_search_id:
        encoded_search_id, version_text = encoded_search_id.rsplit(':', 1)
        try:
            expected_version = int(version_text)
        except ValueError:
            await callback.message.answer(
                'That review card has an invalid version, so I changed nothing.'
            )
            return False
    data = await state.get_data()
    if encoded_search_id is None:
        if (
            data.get('collection_draft')
            and await state.get_state() in {
                RenterState.waiting_for_requirement.state,
                RenterState.collecting_extras.state,
                RenterState.reviewing_requirements.state,
            }
        ):
            return True
        await callback.message.answer(
            'That review card has expired. Please use /start to open your saved draft.'
        )
        return False
    try:
        search_id = UUID(encoded_search_id)
    except (TypeError, ValueError):
        await callback.message.answer('That review card is invalid, so I changed nothing.')
        return False
    if (
        str(data.get('search_id') or '') == str(search_id)
        and data.get('collection_draft')
    ):
        if (
            expected_version is not None
            and int(data.get('search_version') or 0) != expected_version
        ):
            await callback.message.answer(
                'That review card is for an older draft version. Use the newest review card.'
            )
            return False
        return True

    db = get_supabase_client()
    user = db.table('users').select('id').eq(
        'telegram_user_id',
        callback.from_user.id,
    ).execute()
    if not user.data:
        await callback.message.answer('That review card is no longer linked to your renter account.')
        return False
    user_id = str(user.data[0]['id'])
    owned = db.table('search_sessions').select(
        'id,version,status,creation_key',
    ).eq('id', str(search_id)).eq('user_id', user_id).execute()
    if not owned.data or owned.data[0].get('status') != SearchStatus.DRAFT.value:
        await callback.message.answer('That saved draft is no longer available for review.')
        return False
    if (
        expected_version is not None
        and int(owned.data[0].get('version') or 0) != expected_version
    ):
        await callback.message.answer(
            'That review card is for an older draft version. Use /start to reopen the latest review.'
        )
        return False
    requirements = db.table('search_requirements').select('*').eq(
        'search_id',
        str(search_id),
    ).execute()
    if not requirements.data:
        await callback.message.answer(
            'That saved draft has expired requirements. Please use /start to create a new one.'
        )
        return False

    row = owned.data[0]
    draft = RenterRequirementDraft.from_requirements(requirements.data[0])
    progress = CollectionProgress(mode=_configured_collection_mode())
    open_search = db.table('search_sessions').select('id,version,status').eq(
        'user_id',
        user_id,
    ).in_('status', [SearchStatus.ACTIVE.value, SearchStatus.PAUSED.value]).execute()
    replacement = open_search.data[0] if open_search.data else None
    await _save_collection_state(
        state,
        draft,
        progress,
        extra={
            'user_id': user_id,
            'search_id': str(search_id),
            'search_version': int(row.get('version') or 1),
            'creation_key': row.get('creation_key'),
            'persisted_snapshot_hash': (
                collection_signature(draft)
                if not draft.missing_required_fields()
                else None
            ),
            'unconfirmed_replacement_search_id': (
                str(replacement.get('id')) if replacement else None
            ),
            'unconfirmed_replacement_search_version': (
                int(replacement.get('version') or 1) if replacement else None
            ),
            'raw_user_turns': [],
        },
    )
    if draft.missing_required_fields():
        await state.set_state(RenterState.waiting_for_requirement)
        await callback.message.answer(
            format_requirements(draft, title='Your saved draft needs one more review')
            + '\n\n'
            + next_requirement_prompt(draft, progress.mode),
            reply_markup=(
                _guided_keyboard(next_required_field(draft))
                if progress.mode == CollectionMode.GUIDED
                else None
            ),
        )
        return False
    await state.set_state(RenterState.reviewing_requirements)
    return True


@router.callback_query(F.data.startswith(REVIEW_START_PREFIX))
@router.callback_query(F.data == REVIEW_START_CALLBACK)
async def process_review_start_callback(callback: CallbackQuery, state: FSMContext):
    if not await _ensure_review_draft_state(callback, state, REVIEW_START_PREFIX):
        await callback.answer()
        return
    data = await state.get_data()
    if data.get('unconfirmed_replacement_search_id'):
        pending = PendingRenterAction(
            action='resume_replacement_review',
            return_state=RenterState.reviewing_requirements.state,
            payload={
                'replacement_search_id': data.get(
                    'unconfirmed_replacement_search_id'
                ),
                'replacement_search_version': data.get(
                    'unconfirmed_replacement_search_version'
                ),
            },
        )
        await _set_pending_action(
            callback.message,
            state,
            pending,
            'You now have another active or paused search. Start this saved draft '
            'as its replacement only after you confirm?',
        )
        await callback.answer()
        return
    started = await _activate_reviewed_search(
        callback.message, state, telegram_user=callback.from_user,
    )
    if started:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    await callback.answer()


@router.callback_query(F.data.startswith(REVIEW_EDIT_PREFIX))
@router.callback_query(F.data == REVIEW_EDIT_CALLBACK)
async def process_review_edit_callback(callback: CallbackQuery, state: FSMContext):
    if not await _ensure_review_draft_state(callback, state, REVIEW_EDIT_PREFIX):
        await callback.answer()
        return
    data = await state.get_data()
    await callback.message.answer(
        'Which part of the reviewed search would you like to edit?',
        reply_markup=_edit_fields_keyboard(
            data.get('search_id'), data.get('search_version'),
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith(REVIEW_PREFS_PREFIX))
@router.callback_query(F.data == REVIEW_PREFS_CALLBACK)
async def process_review_preferences_callback(callback: CallbackQuery, state: FSMContext):
    if not await _ensure_review_draft_state(callback, state, REVIEW_PREFS_PREFIX):
        await callback.answer()
        return
    await state.update_data(requested_field=RequirementField.CORE_PREFERENCES.value)
    await state.set_state(RenterState.collecting_extras)
    await callback.message.answer(
        'Tell me any optional preferences, such as furnished, parking, no brokerage, '
        'or near metro. You can always say “that is all” to return to review.'
    )
    await callback.message.answer(
        'No more preferences?',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text='Skip / Back to review',
                callback_data='r:edit:back',
            ),
        ]]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith(REVIEW_CANCEL_PREFIX))
@router.callback_query(F.data == REVIEW_CANCEL_CALLBACK)
async def process_review_cancel_callback(callback: CallbackQuery, state: FSMContext):
    if not await _ensure_review_draft_state(callback, state, REVIEW_CANCEL_PREFIX):
        await callback.answer()
        return
    await _request_cancel_confirmation(
        callback.message, state, telegram_user=callback.from_user,
    )
    await callback.answer()


@router.callback_query(F.data.startswith(EDIT_CALLBACK_PREFIX))
async def process_review_field_callback(callback: CallbackQuery, state: FSMContext):
    raw_key = callback.data.removeprefix(EDIT_CALLBACK_PREFIX)
    key, separator, encoded_search_id = raw_key.partition(':')
    data = await state.get_data()
    if separator:
        prefix = f'{EDIT_CALLBACK_PREFIX}{key}:'
        if not await _ensure_review_draft_state(callback, state, prefix):
            await callback.answer()
            return
        data = await state.get_data()
    elif (
        await state.get_state() not in {
            RenterState.reviewing_requirements.state,
            RenterState.collecting_extras.state,
            RenterState.waiting_for_requirement.state,
        }
        or not data.get('collection_draft')
        or not data.get('search_id')
    ):
        await callback.answer(
            'That edit menu has expired. Open your reviewed draft again.',
            show_alert=True,
        )
        return
    if key == 'back':
        draft = _load_collection_draft(data)
        progress = _load_collection_progress(data)
        await state.set_state(RenterState.reviewing_requirements)
        await callback.message.answer(
            format_requirements(draft, title='Review your search before starting'),
            reply_markup=_review_keyboard(
                data.get('search_id'), data.get('search_version'),
            ),
        )
        await callback.answer()
        return
    field = {
        'arr': RequirementField.RENTAL_ARRANGEMENT,
        'cfg': RequirementField.HOME_CONFIGURATIONS,
        'loc': RequirementField.PREFERRED_LOCATIONS,
        'budget': RequirementField.BUDGET,
        'move': RequirementField.MOVE_IN_TIMING,
    }.get(key)
    if field is None:
        await callback.answer('That edit choice is not available.', show_alert=True)
        return
    await state.update_data(requested_field=field.value)
    await state.set_state(RenterState.waiting_for_requirement)
    await callback.message.answer(
        REQUIRED_PROMPTS[field],
        reply_markup=_guided_keyboard(field),
    )
    await callback.answer()


@router.callback_query(F.data.startswith(GUIDED_CALLBACK_PREFIX))
async def process_guided_requirement_callback(callback: CallbackQuery, state: FSMContext):
    key = callback.data.removeprefix(GUIDED_CALLBACK_PREFIX)
    data = await state.get_data()
    if (
        await state.get_state() not in {
            RenterState.waiting_for_requirement.state,
            RenterState.collecting_extras.state,
        }
        or not isinstance(data.get('collection_draft'), dict)
    ):
        await callback.answer(
            'That guided question has expired. Use /start to begin or resume safely.',
            show_alert=True,
        )
        return
    if key == 'cancel':
        await _request_cancel_confirmation(
            callback.message,
            state,
            telegram_user=callback.from_user,
        )
        await callback.answer()
        return
    expected_field = {
        'entire': RequirementField.RENTAL_ARRANGEMENT,
        'private': RequirementField.RENTAL_ARRANGEMENT,
        'shared': RequirementField.RENTAL_ARRANGEMENT,
        '1bhk': RequirementField.HOME_CONFIGURATIONS,
        '2bhk': RequirementField.HOME_CONFIGURATIONS,
        '3bhk': RequirementField.HOME_CONFIGURATIONS,
        'anybhk': RequirementField.HOME_CONFIGURATIONS,
        '20k': RequirementField.BUDGET,
        '30k': RequirementField.BUDGET,
        '40k': RequirementField.BUDGET,
        'other': RequirementField.BUDGET,
        'tomorrow': RequirementField.MOVE_IN_TIMING,
        '2weeks': RequirementField.MOVE_IN_TIMING,
        'firstweek': RequirementField.MOVE_IN_TIMING,
    }.get(key)
    if (
        expected_field is not None
        and data.get('requested_field') != expected_field.value
    ):
        await callback.answer(
            'That choice belongs to an older question.',
            show_alert=True,
        )
        return
    if key == 'retry':
        draft = _load_collection_draft(data)
        progress = _load_collection_progress(data).model_copy(
            update={'no_progress_count': 0, 'field_failure_count': 0},
        )
        raw_requested = data.get('requested_field')
        try:
            requested = (
                RequirementField(raw_requested)
                if raw_requested
                else next_required_field(draft)
            )
        except ValueError:
            requested = next_required_field(draft)
        await _save_collection_state(
            state, draft, progress, requested_field=requested,
        )
        await state.set_state(RenterState.waiting_for_requirement)
        await callback.message.answer(
            next_requirement_prompt(draft, CollectionMode.GUIDED)
            + ' Example: '
            + _guided_example(requested),
            reply_markup=_guided_keyboard(requested),
        )
        await callback.answer()
        return
    value = {
        'entire': 'Entire property',
        'private': 'Private room',
        'shared': 'Shared room',
        '1bhk': '1BHK',
        '2bhk': '2BHK',
        '3bhk': '3BHK',
        'anybhk': 'Any',
        '20k': '20k',
        '30k': '30k',
        '40k': '40k',
        'tomorrow': 'Tomorrow',
        '2weeks': 'Within 2 weeks',
        'firstweek': 'First week next month',
    }.get(key)
    if value is None:
        await callback.message.answer('Please type the value you want to use.')
        await callback.answer()
        return
    await _process_collection_turn(
        callback.message,
        state,
        text=value,
        telegram_user=callback.from_user,
    )
    await callback.answer()


@router.callback_query(F.data.in_({
    CANCEL_SETUP_CALLBACK,
    CANCEL_ACTIVE_CALLBACK,
    CANCEL_BOTH_CALLBACK,
    CANCEL_KEEP_CALLBACK,
}))
async def process_cancel_scope_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    raw_pending = data.get('pending_action')
    if not raw_pending:
        await callback.answer('That cancellation choice expired.', show_alert=True)
        return
    pending = PendingRenterAction(**raw_pending)
    callback_message_id = getattr(callback.message, 'message_id', None)
    if (
        await state.get_state() != RenterState.confirming_conversational_action.state
        or (
            callback_message_id is not None
            and pending.confirmation_message_id != callback_message_id
        )
    ):
        await callback.answer('That cancellation menu has expired.', show_alert=True)
        return
    if callback.data == CANCEL_KEEP_CALLBACK:
        await _decline_pending_action(
            callback.message,
            state,
            callback.from_user,
            callback_message_id=callback_message_id,
        )
        await callback.answer()
        return
    payload = pending.payload
    if callback.data == CANCEL_SETUP_CALLBACK:
        selected = PendingRenterAction(
            action='discard_setup',
            return_state=pending.return_state,
            search_id=payload.get('setup_search_id'),
            search_version=payload.get('setup_search_version'),
        )
    elif callback.data == CANCEL_ACTIVE_CALLBACK:
        selected = PendingRenterAction(
            action='cancel_search',
            return_state=pending.return_state,
            search_id=payload.get('active_search_id'),
            search_version=payload.get('active_search_version'),
            payload={'preserve_setup': True},
        )
    else:
        selected = PendingRenterAction(
            action='cancel_both',
            return_state=pending.return_state,
            payload=payload,
        )
    selected.confirmation_message_id = pending.confirmation_message_id
    await state.update_data(pending_action=selected.model_dump(mode='json'))
    await _confirm_pending_action(
        callback.message,
        state,
        callback.from_user,
        callback_message_id=callback_message_id,
    )
    await callback.answer()


async def _collection_failure_reply(
    message: Message,
    state: FSMContext,
    draft: RenterRequirementDraft,
    progress: CollectionProgress,
    *,
    parser_failed: bool,
    telegram_user=None,
    requested_field: Optional[RequirementField] = None,
) -> None:
    requested = requested_field or next_required_field(draft)
    prompt = (
        REQUIRED_PROMPTS.get(requested)
        if requested
        else next_requirement_prompt(draft, progress.mode)
    )
    if not prompt:
        prompt = next_requirement_prompt(draft, progress.mode)
    updated = advance_collection_progress(
        progress,
        draft,
        requested_field=requested,
        made_progress=False,
        parser_failed=parser_failed,
        next_prompt=prompt,
    )
    await _save_collection_state(
        state,
        draft,
        updated,
        requested_field=requested,
    )
    if updated.field_failure_count >= 3:
        text = (
            format_requirements(draft)
            + '\n\nI still could not map that answer after '
            + str(updated.field_failure_count)
            + ' attempts. A valid example is: '
            + _guided_example(requested)
        )
        keyboard = _guided_terminal_keyboard()
    elif updated.mode == CollectionMode.GUIDED:
        prefix = (
            'I kept everything you already told me. Let us finish this with '
            'a quick guided answer.'
            if updated.field_failure_count <= 1
            else
            'That still did not map to the requested field. Please use one '
            'of the guided choices or type the example format.'
        )
        text = prefix + '\n\n' + prompt
    else:
        text = (
            'I could not confidently map that answer, but I kept your '
            'existing requirements.\n\n'
            + prompt
        )
        keyboard = _guided_keyboard(requested)
    if updated.field_failure_count < 3:
        keyboard = _guided_keyboard(requested)
    await message.answer(text, reply_markup=keyboard)
    _trace_collection_turn(
        message,
        draft,
        updated,
        intents=['REQUIREMENT_INPUT'],
        failure_stage='parse' if parser_failed else 'no_progress',
        telegram_user=telegram_user,
        state_name=await state.get_state(),
    )


async def _process_collection_turn(
    message: Message,
    state: FSMContext,
    *,
    text: Optional[str] = None,
    telegram_user=None,
) -> None:
    renter_text = (text if text is not None else message.text or '').strip()
    if not renter_text:
        await message.answer(
            'Please send your requirement as text, or ask what I have collected.'
        )
        return
    data = await state.get_data()
    collection_state = await state.get_state()
    draft = _load_collection_draft(data)
    progress = _load_collection_progress(data)
    raw_requested = data.get('requested_field')
    try:
        requested = RequirementField(raw_requested) if raw_requested else next_required_field(draft)
    except ValueError:
        requested = next_required_field(draft)

    requirement_text, finish_requested = split_terminal_finish_phrase(renter_text)
    requirement_text, summary_requested = split_terminal_summary_phrase(
        requirement_text,
    )
    control_intents = detect_collection_control_intents(renter_text)
    if any(item.value == 'CANCEL' for item in control_intents):
        await _request_cancel_confirmation(message, state)
        return
    if finish_requested and not requirement_text:
        await _show_requirement_review(
            message,
            state,
            draft,
            progress,
            telegram_user=telegram_user,
        )
        return
    if summary_requested and not requirement_text:
        await message.answer(format_requirements(draft))
        await _resume_flow(message, state, draft.model_dump(mode='json'))
        return

    intent_text = requirement_text
    fast_decision = conversation_service._deterministic_decision(
        intent_text,
        await state.get_state(),
        bool(data.get('pending_action')),
    )
    requirement_text = strip_routed_non_requirement_clauses(intent_text)
    if (
        fast_decision
        and RenterIntent.REQUIREMENT_INPUT not in fast_decision.intents
        and not requirement_text
    ):
        if (
            RenterIntent.SHOW_REQUIREMENTS in fast_decision.intents
            or any(item.value == 'SHOW_SUMMARY' for item in control_intents)
        ):
            await message.answer(format_requirements(draft))
            await _resume_flow(message, state, draft.model_dump(mode='json'))
            return
        if await _handle_conversational_decision(message, state, fast_decision):
            return

    try:
        patch = parse_requirement_turn(
            requirement_text,
            requested_field=requested,
            timezone=settings.flathunter_default_timezone,
        )
    except Exception:
        logger.exception('Deterministic requirement parsing failed')
        await _collection_failure_reply(
            message, state, draft, progress, parser_failed=True,
            telegram_user=telegram_user,
            requested_field=requested,
        )
        return

    if patch.operations or text is not None:
        if fast_decision:
            combined_intents = list(fast_decision.intents)
            if RenterIntent.REQUIREMENT_INPUT not in combined_intents:
                combined_intents.insert(0, RenterIntent.REQUIREMENT_INPUT)
            decision = fast_decision.model_copy(update={
                'intents': combined_intents,
                'requirement_or_edit_text': requirement_text,
            })
        else:
            decision = RenterTurnDecision(
                intents=[RenterIntent.REQUIREMENT_INPUT],
                requirement_or_edit_text=requirement_text,
            )
    else:
        decision = fast_decision or await _classify_renter_turn(message, state)
    show_after = (
        RenterIntent.SHOW_REQUIREMENTS in decision.intents
        or any(item.value == 'SHOW_SUMMARY' for item in control_intents)
    )
    if RenterIntent.REQUIREMENT_INPUT not in decision.intents:
        if show_after:
            await message.answer(format_requirements(draft))
            await _resume_flow(message, state, draft.model_dump(mode='json'))
            return
        if RenterIntent.AMBIGUOUS in decision.intents:
            await _collection_failure_reply(
                message,
                state,
                draft,
                progress,
                parser_failed=decision.confidence <= 0,
                telegram_user=telegram_user,
                requested_field=requested,
            )
            return
        if await _handle_conversational_decision(message, state, decision):
            return

    deterministic_patch = patch
    if (
        progress.mode == CollectionMode.HYBRID
        and requirement_turn_needs_enrichment(requirement_text, deterministic_patch)
    ):
        try:
            prompt = build_requirement_patch_prompt(
                draft,
                requirement_text,
                requested,
                deterministic_patch,
            )
            extracted_patch = await req_service.llm.generate_structured(
                prompt,
                RequirementTurnPatch,
            )
            extracted_patch = validate_requirement_turn_patch_grounding(
                extracted_patch,
                requirement_text,
                timezone=settings.flathunter_default_timezone,
            )
            patch = combine_requirement_turn_patches(
                deterministic_patch,
                extracted_patch,
            )
        except Exception:
            logger.exception('Requirement patch extraction failed')
            if deterministic_patch.operations:
                patch = deterministic_patch
            else:
                await _collection_failure_reply(
                    message,
                    state,
                    draft,
                    progress,
                    parser_failed=True,
                    telegram_user=telegram_user,
                    requested_field=requested,
                )
                return

    if not patch.operations:
        await _collection_failure_reply(
            message,
            state,
            draft,
            progress,
            parser_failed=False,
            telegram_user=telegram_user,
            requested_field=requested,
        )
        return

    try:
        merged = apply_requirement_patch(draft, patch)
    except (TypeError, ValueError):
        logger.exception('Requirement patch validation failed')
        if patch is not deterministic_patch and deterministic_patch.operations:
            try:
                patch = deterministic_patch
                merged = apply_requirement_patch(draft, patch)
            except (TypeError, ValueError):
                logger.exception('Deterministic requirement patch validation failed')
                await _collection_failure_reply(
                    message,
                    state,
                    draft,
                    progress,
                    parser_failed=True,
                    telegram_user=telegram_user,
                    requested_field=requested,
                )
                return
        else:
            await _collection_failure_reply(
                message,
                state,
                draft,
                progress,
                parser_failed=True,
                telegram_user=telegram_user,
                requested_field=requested,
            )
            return
    if merged.pending_conflict:
        await _save_collection_state(
            state,
            draft,
            progress,
            requested_field=requested,
            pending_conflict=merged.pending_conflict,
            extra={'pending_conflict_raw_text': renter_text},
        )
        await state.set_state(RenterState.confirming_requirement)
        sent = await message.answer(
            'I already have a different value for '
            + merged.pending_conflict.field.value.replace('_', ' ')
            + '.\n\nCurrent: '
            + escape(str(merged.pending_conflict.current_value))
            + '\nProposed: '
            + escape(str(merged.pending_conflict.proposed_value)),
            reply_markup=_conflict_keyboard(merged.pending_conflict),
        )
        conflict_message_id = getattr(sent, 'message_id', None)
        if conflict_message_id is not None:
            await state.update_data(
                pending_conflict_message_id=int(conflict_message_id),
            )
        _trace_collection_turn(
            message,
            draft,
            progress,
            intents=[item.value for item in decision.intents],
            patch=patch,
            failure_stage='conflict',
            telegram_user=telegram_user,
            state_name=collection_state,
        )
        return

    if not merged.made_progress:
        if patch.operations and all(
            operation.operation == RequirementChangeOperation.REMOVE
            for operation in patch.operations
        ):
            await message.answer(
                'Okay. I am not treating that as a required preference.\n\n'
                + next_requirement_prompt(draft, progress.mode),
                reply_markup=(
                    _guided_keyboard(requested)
                    if progress.mode == CollectionMode.GUIDED
                    else None
                ),
            )
            return
        await _collection_failure_reply(
            message, state, draft, progress, parser_failed=False,
            telegram_user=telegram_user,
            requested_field=requested,
        )
        return

    updated_draft = merged.draft
    raw_turns = list(data.get('raw_user_turns') or [])
    raw_turns.append(renter_text)
    requested = next_required_field(updated_draft)
    next_prompt = next_requirement_prompt(updated_draft, progress.mode)
    updated_progress = advance_collection_progress(
        progress,
        updated_draft,
        requested_field=requested,
        made_progress=True,
        next_prompt=next_prompt,
    )
    persistence: dict = {}
    if data.get('search_id') and not updated_draft.missing_required_fields():
        try:
            persistence = await _persist_collection_draft(
                message,
                state,
                updated_draft,
                telegram_user=telegram_user,
                raw_turns=raw_turns,
            )
        except Exception:
            logger.exception('Validated requirement patch could not be persisted')
            await message.answer(
                'I understood that change, but could not save it to your review draft. '
                'I kept the last saved values; please retry or edit another value.'
            )
            _trace_collection_turn(
                message,
                draft,
                progress,
                intents=[item.value for item in decision.intents],
                patch=patch,
                failure_stage='persistence',
                telegram_user=telegram_user,
                state_name=collection_state,
            )
            return

    await _save_collection_state(
        state,
        updated_draft,
        updated_progress,
        requested_field=requested,
        extra={**persistence, 'raw_user_turns': raw_turns},
    )
    await _remember_turn(state, 'user', renter_text)
    changes = describe_requirement_changes(draft, updated_draft)
    recap = 'Saved: ' + '; '.join(changes)
    _trace_collection_turn(
        message,
        updated_draft,
        updated_progress,
        intents=[item.value for item in decision.intents],
        patch=patch,
        telegram_user=telegram_user,
        state_name=collection_state,
    )

    residual_intents = [
        intent
        for intent in decision.intents
        if intent not in {
            RenterIntent.REQUIREMENT_INPUT,
            RenterIntent.SHOW_REQUIREMENTS,
        }
    ]
    recap_sent = False
    if residual_intents:
        await message.answer(recap)
        recap_sent = True
        residual_decision = decision.model_copy(update={'intents': residual_intents})
        await _handle_conversational_decision(
            message,
            state,
            residual_decision,
            resume_flow=False,
        )
        if await state.get_state() in {
            RenterState.confirming_conversational_action.state,
            RenterState.waiting_for_availability.state,
        }:
            return

    if finish_requested:
        if not recap_sent:
            await message.answer(recap)
        await _show_requirement_review(
            message,
            state,
            updated_draft,
            updated_progress,
            telegram_user=telegram_user,
        )
        return

    if requested is None:
        if show_after:
            prefix = '' if recap_sent else recap + '\n\n'
            await message.answer(prefix + format_requirements(updated_draft))
        elif not recap_sent:
            await message.answer(recap)
        await _show_requirement_review(
            message,
            state,
            updated_draft,
            updated_progress,
            telegram_user=telegram_user,
        )
        return

    await state.set_state(RenterState.waiting_for_requirement)
    response = '' if recap_sent else recap
    if show_after:
        response += ('\n\n' if response else '') + format_requirements(updated_draft)
    response += ('\n\n' if response else '') + next_requirement_prompt(
        updated_draft,
        updated_progress.mode,
    )
    await message.answer(
        response,
        reply_markup=(
            _guided_keyboard(requested)
            if updated_progress.mode == CollectionMode.GUIDED
            else None
        ),
    )

@router.message(RenterState.waiting_for_requirement)
async def process_requirement(message: Message, state: FSMContext):
    await _process_collection_turn(message, state)

@router.message(RenterState.collecting_extras)
async def process_extras(message: Message, state: FSMContext):
    await _process_collection_turn(message, state)

@router.message(RenterState.reviewing_requirements)
async def process_requirement_review_message(message: Message, state: FSMContext):
    await _process_collection_turn(message, state)


@router.message(RenterState.waiting_for_availability)
async def process_availability(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer('Please describe your availability as text.')
        return
    decision = await _classify_renter_turn(message, state)
    if RenterIntent.SET_AVAILABILITY not in decision.intents:
        if await _handle_conversational_decision(message, state, decision):
            return
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    user_id = await get_or_create_user(message)
    
    from app.scheduling.service import SchedulingService
    sched_service = SchedulingService()
    
    try:
        await sched_service.parse_and_save_availability(user_id, None, message.text)
        await message.answer("✅ Availability saved! I'll use this when landlords propose times.")
    except Exception:
        logger.exception('Availability collection failed')
        await message.answer('I could not save that availability. Try something like weekends anytime or weekdays after 6 PM.')
    finally:
        data = await state.get_data()
        return_state = data.get('availability_return_state')
        if return_state:
            await state.update_data(availability_return_state=None)
            await state.set_state(return_state)
            requirements, _ = await _current_requirements(message, state)
            await _resume_flow(message, state, requirements)
        else:
            await state.clear()

@router.callback_query(F.data.startswith("search:start:"))
async def process_start_search_callback(callback: CallbackQuery, state: FSMContext):
    """Activate only the draft owned by the Telegram user pressing this button."""
    search_id = callback.data.removeprefix("search:start:")
    state_data = await state.get_data()
    if str(state_data.get('search_id') or '') == search_id:
        started = await _activate_reviewed_search(
            callback.message,
            state,
            telegram_user=callback.from_user,
        )
        if started:
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
        await callback.answer()
        return
    db = get_supabase_client()
    user_result = db.table("users").select("id").eq("telegram_user_id", callback.from_user.id).execute()
    if not user_result.data:
        await callback.answer("This confirmation is no longer valid. Please start again.", show_alert=True)
        return
    try:
        owned = db.table('search_sessions').select('id,version,status,creation_key').eq(
            'id', search_id,
        ).eq('user_id', user_result.data[0]['id']).execute()
        if not owned.data:
            raise ValueError('This draft does not belong to your renter account')
        row = owned.data[0]
        if row.get('status') == SearchStatus.ACTIVE.value:
            await callback.message.answer('Your search is already live.')
            await callback.answer()
            return
        if row.get('status') != SearchStatus.DRAFT.value:
            raise ValueError('This draft can no longer be reviewed')
        saved = db.table('search_requirements').select('*').eq(
            'search_id', search_id,
        ).execute()
        if not saved.data:
            raise ValueError('This saved draft has expired requirements')
        draft = RenterRequirementDraft.from_requirements(saved.data[0])
        progress = CollectionProgress(mode=_configured_collection_mode())
        missing = draft.missing_required_fields()
        await _save_collection_state(
            state,
            draft,
            progress,
            extra={
                'user_id': str(user_result.data[0]['id']),
                'search_id': str(search_id),
                'search_version': int(row.get('version') or 1),
                'creation_key': row.get('creation_key'),
                'persisted_snapshot_hash': (
                    collection_signature(draft) if not missing else None
                ),
                'raw_user_turns': [],
            },
        )
        if missing:
            await state.set_state(RenterState.waiting_for_requirement)
            await callback.message.answer(
                format_requirements(
                    draft,
                    title='Your saved draft needs one more review',
                )
                + '\n\n'
                + next_requirement_prompt(draft, progress.mode),
                reply_markup=(
                    _guided_keyboard(next_required_field(draft))
                    if progress.mode == CollectionMode.GUIDED
                    else None
                ),
            )
            await callback.answer()
            return
        await state.set_state(RenterState.reviewing_requirements)
    except Exception:
        logger.exception('Legacy renter start callback failed')
        await callback.message.answer(
            'I could not start this saved draft. It may be stale or incomplete. '
            'Please use /start to review a fresh setup.'
        )
        await callback.answer()
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        format_requirements(draft, title='Review your saved search before starting'),
        reply_markup=_review_keyboard(str(search_id), row.get('version')),
    )
    await callback.answer()
# ─── CALLBACK QUERIES ───

async def _callback_owns_search(callback: CallbackQuery, search_id: str) -> bool:
    """Authorize sensitive callbacks against persisted Telegram-user ownership."""
    db = get_supabase_client()
    user = db.table("users").select("id").eq("telegram_user_id", callback.from_user.id).execute()
    if not user.data:
        return False
    search = db.table("search_sessions").select("id").eq("id", search_id).eq("user_id", user.data[0]["id"]).execute()
    return bool(search.data)


def _match_id_from_callback(callback_data: str | None, action: str) -> str | None:
    """Extract and validate the UUID carried by a renter match action."""
    from uuid import UUID

    prefix = f"{action}_match_"
    if not callback_data or not callback_data.startswith(prefix):
        return None
    match_id = callback_data.removeprefix(prefix)
    try:
        UUID(match_id)
    except (TypeError, ValueError):
        return None
    return match_id


async def _callback_owned_match(callback: CallbackQuery, match_id: str) -> dict | None:
    db = get_supabase_client()
    result = db.table("matches").select(
        "id,search_id,listing_id,missing_information"
    ).eq("id", match_id).execute()
    if not result.data:
        return None
    match = result.data[0]
    if not await _callback_owns_search(callback, match["search_id"]):
        return None
    return match


@router.callback_query(F.data.startswith("details_match_"))
async def process_property_details_callback(callback: CallbackQuery):
    match_id = _match_id_from_callback(callback.data, "details")
    match = await _callback_owned_match(callback, match_id) if match_id else None
    if not match:
        await callback.answer("These property details are not available for your search.", show_alert=True)
        return

    listing_result = get_supabase_client().table("listings").select("*").eq(
        "id", match["listing_id"]
    ).execute()
    if not listing_result.data:
        await callback.answer("This property is no longer available.", show_alert=True)
        return

    await callback.answer()
    from app.matching.details import clarification_labels, draft_property_narrative, message_chunks

    narrative = await draft_property_narrative(listing_result.data[0])
    clarifications = clarification_labels(match.get("missing_information"))
    if clarifications:
        missing_section = (
            "Still to confirm with the owner or agent:\n"
            + "\n".join(f"• {item}" for item in clarifications)
            + "\n\nIf you choose Contact owner / agent, FlatHunter's agent will collect these clarifications "
              "and update you here."
        )
    else:
        missing_section = "No renter-specific owner clarifications are currently outstanding."

    response = f"Full property details\n\n{narrative}\n\n{missing_section}"
    for chunk in message_chunks(response):
        await callback.message.answer(chunk, parse_mode=None)


@router.callback_query(F.data.startswith("contact_match_"))
async def process_contact_callback(callback: CallbackQuery):
    from app.qualification.service import QualificationService

    match_id = _match_id_from_callback(callback.data, "contact")
    match = await _callback_owned_match(callback, match_id) if match_id else None
    if not match:
        await callback.answer("This action is not available for your search.", show_alert=True)
        return
    search_id, listing_id = match["search_id"], match["listing_id"]
    db = get_supabase_client()
    contacts_res = db.table("contacts").select("id").eq("listing_id", listing_id).execute()
    if not contacts_res.data:
        await callback.message.answer("This property does not have a contact listed.")
        await callback.answer()
        return

    qual_service = QualificationService()
    conv_id = qual_service.start_conversation(search_id, listing_id, contacts_res.data[0]["id"])
    await callback.message.answer("I have started qualifying this property and will update you here.")
    await qual_service.generate_initial_outreach(conv_id)
    await callback.answer()


@router.callback_query(F.data.startswith("visit_confirm_"))
async def process_visit_confirm(callback: CallbackQuery):
    visit_id = callback.data.removeprefix("visit_confirm_")
    db = get_supabase_client()
    visit = db.table("visits").select("search_id").eq("id", visit_id).execute()
    if not visit.data or not await _callback_owns_search(callback, visit.data[0]["search_id"]):
        await callback.answer("This visit is not available for your search.", show_alert=True)
        return
    from app.scheduling.service import SchedulingService
    SchedulingService().confirm_visit(visit_id)
    db.table("agent_jobs").insert({
        "job_type": "EMAIL_CONFIRM_VISIT", "status": "PENDING", "payload": {"visit_id": visit_id},
        "run_after": datetime.now(timezone.utc).isoformat(),
    }).execute()
    await callback.message.edit_text("✅ Visit confirmed! I have let the landlord know.")
    await callback.answer()


@router.callback_query(F.data.startswith("visit_decline_"))
async def process_visit_decline(callback: CallbackQuery):
    visit_id = callback.data.removeprefix("visit_decline_")
    db = get_supabase_client()
    visit = db.table("visits").select("search_id").eq("id", visit_id).execute()
    if not visit.data or not await _callback_owns_search(callback, visit.data[0]["search_id"]):
        await callback.answer("This visit is not available for your search.", show_alert=True)
        return
    from app.scheduling.service import SchedulingService
    SchedulingService().cancel_visit(visit_id)
    await callback.message.edit_text("❌ Visit declined. We will continue the search.")
    await callback.answer()


@router.callback_query(F.data.startswith("skip_match_"))
async def process_skip_callback(callback: CallbackQuery):
    match_id = _match_id_from_callback(callback.data, "skip")
    match = await _callback_owned_match(callback, match_id) if match_id else None
    if not match:
        await callback.answer("This result is not available for your search.", show_alert=True)
        return
    db = get_supabase_client()
    db.table("matches").update({"status": "SKIPPED"}).eq("id", match["id"]).execute()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer("Skipped. I will keep looking for a better fit.")
    await callback.answer()

# ─── FALLBACK (must be LAST) ───

@router.message()
async def renter_fallback(message: Message, state: FSMContext):
    if is_admin_menu_active(message.chat.id):
        return
    if not message.text or not message.text.strip():
        await message.answer('Please send text so I can help with your flat search, requirements, matches, or rental questions.')
        return
    decision = await _classify_renter_turn(message, state)
    if RenterIntent.REQUIREMENT_INPUT in decision.intents:
        data = await state.get_data()
        if data.get('collection_draft') is None:
            if await check_and_handle_active_searches(
                message,
                state,
                initial_requirement_text=message.text,
            ):
                return
            await _initialize_collection(state)
        await _process_collection_turn(
            message,
            state,
            text=message.text,
        )
        return
    if await _handle_conversational_decision(message, state, decision):
        await _remember_turn(state, 'user', message.text)
        tracer.log_event(
            'RENTER_TURN_ROUTED',
            override_telegram_user_id=message.from_user.id,
            payload={'intents': [item.value for item in decision.intents], 'confidence': decision.confidence},
        )
        return
    await message.answer('Tell me whether you want to start a search, view or edit your requirements, check matches, or ask a rental question.')
