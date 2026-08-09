from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.common.enums import SearchStatus
from app.requirements.collector import (
    CollectionProgress,
    PendingRequirementConflict,
    RenterRequirementDraft,
    RequirementField,
    RequirementPatchOperation,
    RequirementTurnPatch,
    apply_requirement_patch,
    collection_signature,
)
from app.requirements.schemas import RequirementChangeOperation
from app.telegram import renter_handlers
from app.telegram.states import RenterState


USER_ID = UUID('11111111-1111-1111-1111-111111111111')
DRAFT_ID = UUID('22222222-2222-2222-2222-222222222222')
ACTIVE_ID = UUID('33333333-3333-3333-3333-333333333333')


class FakeState:
    def __init__(self, current_state=None, data=None):
        self.current_state = current_state
        self.data = deepcopy(data or {})
        self.cleared = False

    async def get_state(self):
        return self.current_state

    async def get_data(self):
        return deepcopy(self.data)

    async def update_data(self, **values):
        self.data.update(deepcopy(values))

    async def set_state(self, value):
        self.current_state = value.state if hasattr(value, 'state') else value

    async def clear(self):
        self.current_state = None
        self.data.clear()
        self.cleared = True


class FakeMessage:
    def __init__(self, text='', *, telegram_user_id=42):
        self.text = text
        self.answers = []
        self.edited_reply_markups = []
        self.from_user = SimpleNamespace(
            id=telegram_user_id,
            username='renter',
            full_name='Test Renter',
        )
        self.chat = SimpleNamespace(id=telegram_user_id)
        self.bot = SimpleNamespace()

    async def answer(self, text, **kwargs):
        self.answers.append({'text': text, **kwargs})

    async def edit_reply_markup(self, **kwargs):
        self.edited_reply_markups.append(kwargs)


class FakeCallback:
    def __init__(self, data, message, *, telegram_user_id=42):
        self.data = data
        self.message = message
        self.from_user = SimpleNamespace(
            id=telegram_user_id,
            username='callback-renter',
            full_name='Callback Renter',
        )
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))


def complete_draft() -> dict:
    return RenterRequirementDraft(
        listing_types=['PRIVATE_ROOM'],
        preferred_locations=['Kondapur'],
        target_rent=30_000,
        max_rent=30_000,
        preferred_move_in_date='2026-09-01',
        latest_move_in_date='2026-09-07',
        configuration_answered=True,
    ).model_dump(mode='json')


def review_state(**overrides) -> FakeState:
    draft = complete_draft()
    data = {
        'collection_draft': draft,
        'collection_progress': CollectionProgress().model_dump(mode='json'),
        'user_id': str(USER_ID),
        'search_id': str(DRAFT_ID),
        'search_version': 7,
        'replacement_search_id': str(ACTIVE_ID),
        'replacement_search_version': 11,
        'persisted_snapshot_hash': collection_signature(
            RenterRequirementDraft.from_requirements(draft),
        ),
    }
    data.update(overrides)
    return FakeState(RenterState.reviewing_requirements.state, data)


def staged_location_conflict() -> tuple[RenterRequirementDraft, PendingRequirementConflict]:
    draft = RenterRequirementDraft(
        listing_types=['PRIVATE_ROOM'],
        preferred_locations=['Kondapur'],
        target_rent=30_000,
        max_rent=30_000,
        configuration_answered=True,
    )
    patch = RequirementTurnPatch(operations=[
        RequirementPatchOperation(
            field=RequirementField.PREFERRED_LOCATIONS,
            operation=RequirementChangeOperation.SET,
            value=['Madhapur'],
        ),
        RequirementPatchOperation(
            field=RequirementField.BUDGET,
            operation=RequirementChangeOperation.REPLACE,
            value={'target_rent': 40_000, 'max_rent': 40_000},
        ),
    ])
    merged = apply_requirement_patch(draft, patch)
    assert merged.pending_conflict is not None
    assert merged.draft.model_dump(mode='json') == draft.model_dump(mode='json')
    return draft, merged.pending_conflict


def conflict_state() -> FakeState:
    draft, conflict = staged_location_conflict()
    return FakeState(
        RenterState.confirming_requirement.state,
        {
            'collection_draft': draft.model_dump(mode='json'),
            'collection_progress': CollectionProgress().model_dump(mode='json'),
            'pending_requirement_conflict': conflict.model_dump(mode='json'),
            'pending_conflict_raw_text': 'Madhapur and increase budget to 40k',
            'raw_user_turns': [],
        },
    )


@pytest.mark.asyncio
async def test_review_start_activates_with_draft_and_replacement_versions(monkeypatch):
    calls = []

    def activate(user_id, search_id, **kwargs):
        calls.append((user_id, search_id, kwargs))
        return SimpleNamespace(
            session=SimpleNamespace(id=search_id),
            activated=True,
            job_enqueued=True,
            replaced_search_id=kwargs['replace_search_id'],
        )

    trace_events = []
    monkeypatch.setattr(
        renter_handlers.req_service,
        'get_current_search',
        lambda user_id: ({
            'id': str(ACTIVE_ID),
            'status': SearchStatus.ACTIVE.value,
            'version': 11,
        }, {}),
    )
    monkeypatch.setattr(renter_handlers.req_service, 'activate_renter_search', activate)
    monkeypatch.setattr(
        renter_handlers.tracer,
        'log_event',
        lambda *args, **kwargs: trace_events.append((args, kwargs)),
    )
    state = review_state()
    message = FakeMessage()
    callback = FakeCallback(
        renter_handlers.REVIEW_START_CALLBACK,
        message,
        telegram_user_id=8123,
    )

    await renter_handlers.process_review_start_callback(callback, state)

    assert calls == [(
        USER_ID,
        DRAFT_ID,
        {
            'expected_version': 7,
            'replace_search_id': ACTIVE_ID,
            'replace_expected_version': 11,
        },
    )]
    assert state.cleared is True
    assert message.edited_reply_markups == [{'reply_markup': None}]
    assert callback.answers == [(None, {})]
    assert trace_events[0][1]['override_telegram_user_id'] == 8123


@pytest.mark.asyncio
async def test_review_start_rpc_failure_preserves_fsm_and_review_controls(monkeypatch):
    def fail_activation(*args, **kwargs):
        raise RuntimeError('provider connection details')

    monkeypatch.setattr(
        renter_handlers.req_service,
        'activate_renter_search',
        fail_activation,
    )
    monkeypatch.setattr(
        renter_handlers.req_service,
        'get_current_search',
        lambda user_id: ({
            'id': str(ACTIVE_ID),
            'status': SearchStatus.ACTIVE.value,
            'version': 11,
        }, {}),
    )
    state = review_state()
    original_data = deepcopy(state.data)
    message = FakeMessage()
    callback = FakeCallback(renter_handlers.REVIEW_START_CALLBACK, message)

    await renter_handlers.process_review_start_callback(callback, state)

    assert state.current_state == RenterState.reviewing_requirements.state
    assert state.data == original_data
    assert state.cleared is False
    assert message.edited_reply_markups == []
    assert 'retry Start Search' in message.answers[-1]['text']
    assert 'provider connection details' not in message.answers[-1]['text']
    assert message.answers[-1]['reply_markup'] is not None


@pytest.mark.asyncio
async def test_old_version_review_card_cannot_activate_newer_draft(monkeypatch):
    activation_calls = []
    monkeypatch.setattr(
        renter_handlers.req_service,
        'activate_renter_search',
        lambda *args, **kwargs: activation_calls.append((args, kwargs)),
    )
    state = review_state(search_version=8)
    message = FakeMessage()
    callback = FakeCallback(
        renter_handlers.REVIEW_START_PREFIX + str(DRAFT_ID) + ':7',
        message,
    )

    await renter_handlers.process_review_start_callback(callback, state)

    assert activation_calls == []
    assert 'older draft version' in message.answers[-1]['text']
    assert state.current_state == RenterState.reviewing_requirements.state


@pytest.mark.asyncio
async def test_start_retries_persistence_before_activating_unsaved_snapshot(monkeypatch):
    reviews = []
    activation_calls = []

    async def show(message, state, draft, progress, **kwargs):
        reviews.append(draft.model_dump(mode='json'))
        return False

    monkeypatch.setattr(renter_handlers, '_show_requirement_review', show)
    monkeypatch.setattr(
        renter_handlers.req_service,
        'activate_renter_search',
        lambda *args, **kwargs: activation_calls.append((args, kwargs)),
    )
    state = review_state(
        persisted_snapshot_hash=None,
        persistence_pending=True,
    )
    callback = FakeCallback(renter_handlers.REVIEW_START_CALLBACK, FakeMessage())

    await renter_handlers.process_review_start_callback(callback, state)

    assert len(reviews) == 1
    assert activation_calls == []
    assert state.cleared is False


@pytest.mark.asyncio
async def test_in_memory_failed_review_buttons_remain_usable():
    state = FakeState(
        RenterState.reviewing_requirements.state,
        {
            'collection_draft': complete_draft(),
            'collection_progress': CollectionProgress().model_dump(mode='json'),
            'persistence_pending': True,
        },
    )
    message = FakeMessage()
    callback = FakeCallback(renter_handlers.REVIEW_EDIT_CALLBACK, message)

    await renter_handlers.process_review_edit_callback(callback, state)

    assert state.current_state == RenterState.reviewing_requirements.state
    assert 'Which part' in message.answers[-1]['text']


@pytest.mark.parametrize(
    ('callback_data', 'expected_locations'),
    [
        (renter_handlers.CONFLICT_USE_CALLBACK, ['Madhapur']),
        (renter_handlers.CONFLICT_KEEP_CALLBACK, ['Kondapur']),
        (renter_handlers.CONFLICT_ADD_CALLBACK, ['Kondapur', 'Madhapur']),
    ],
)
@pytest.mark.asyncio
async def test_conflict_buttons_apply_the_entire_staged_patch_only_after_choice(
    callback_data,
    expected_locations,
):
    state = conflict_state()
    before = deepcopy(state.data['collection_draft'])
    message = FakeMessage()
    callback = FakeCallback(callback_data, message)

    assert before['preferred_locations'] == ['Kondapur']
    assert before['max_rent'] == 30_000

    await renter_handlers.process_requirement_conflict_callback(callback, state)

    assert state.data['collection_draft']['preferred_locations'] == expected_locations
    assert state.data['collection_draft']['target_rent'] == 40_000
    assert state.data['collection_draft']['max_rent'] == 40_000
    assert state.data['pending_requirement_conflict'] is None
    assert state.data['raw_user_turns'] == [
        'Madhapur and increase budget to 40k'
    ]


@pytest.mark.parametrize(
    ('answer', 'expected_locations'),
    [
        ('yes', ['Madhapur']),
        ('no', ['Kondapur']),
    ],
)
@pytest.mark.asyncio
async def test_conflict_natural_yes_no_matches_button_atomic_semantics(
    answer,
    expected_locations,
):
    state = conflict_state()
    message = FakeMessage(answer)

    await renter_handlers.process_requirement_conflict_text(message, state)

    assert state.data['collection_draft']['preferred_locations'] == expected_locations
    assert state.data['collection_draft']['max_rent'] == 40_000
    assert state.data['pending_requirement_conflict'] is None


@pytest.mark.asyncio
async def test_guided_callback_uses_the_callback_actor_not_the_bot_message_actor(monkeypatch):
    observed = []

    async def process(message, state, *, text=None, telegram_user=None):
        observed.append((message, state, text, telegram_user))

    monkeypatch.setattr(renter_handlers, '_process_collection_turn', process)
    state = FakeState(
        RenterState.waiting_for_requirement.state,
        {
            'requested_field': RequirementField.BUDGET.value,
            'collection_draft': RenterRequirementDraft().model_dump(mode='json'),
        },
    )
    message = FakeMessage(telegram_user_id=999_999)
    callback = FakeCallback(
        renter_handlers.GUIDED_CALLBACK_PREFIX + '30k',
        message,
        telegram_user_id=1234,
    )

    await renter_handlers.process_guided_requirement_callback(callback, state)

    assert observed[0][2] == '30k'
    assert observed[0][3] is callback.from_user
    assert observed[0][3].id == 1234


@pytest.mark.asyncio
async def test_stale_guided_button_cannot_create_or_cancel_a_new_setup():
    state = FakeState()
    message = FakeMessage()
    callback = FakeCallback(
        renter_handlers.GUIDED_CALLBACK_PREFIX + 'retry',
        message,
    )

    await renter_handlers.process_guided_requirement_callback(callback, state)

    assert state.current_state is None
    assert state.data == {}
    assert callback.answers[-1][1]['show_alert'] is True
    assert 'expired' in callback.answers[-1][0]


@pytest.mark.asyncio
async def test_admin_mode_guard_rejects_stale_renter_callbacks(monkeypatch):
    handled = []

    async def downstream(event, data):
        handled.append((event, data))

    monkeypatch.setattr(
        renter_handlers,
        'is_admin_menu_active',
        lambda chat_id: True,
    )
    callback = FakeCallback('r:guided:30k', FakeMessage())

    await renter_handlers._AdminModeCallbackGuard()(
        downstream,
        callback,
        {},
    )

    assert handled == []
    assert callback.answers[-1][1]['show_alert'] is True
    assert '/renter' in callback.answers[-1][0]


@pytest.mark.asyncio
async def test_review_edit_exposes_field_choices_without_discarding_review_state():
    state = review_state()
    original_data = deepcopy(state.data)
    message = FakeMessage()
    callback = FakeCallback(renter_handlers.REVIEW_EDIT_CALLBACK, message)

    await renter_handlers.process_review_edit_callback(callback, state)

    assert state.current_state == RenterState.reviewing_requirements.state
    assert state.data == original_data
    callback_values = [
        button.callback_data
        for row in message.answers[-1]['reply_markup'].inline_keyboard
        for button in row
    ]
    assert any(value.startswith('r:edit:loc:') for value in callback_values)
    assert any(value.startswith('r:edit:budget:') for value in callback_values)


@pytest.mark.asyncio
async def test_review_preferences_enters_optional_collection_without_mutating_draft():
    state = review_state()
    original_draft = deepcopy(state.data['collection_draft'])
    message = FakeMessage()
    callback = FakeCallback(renter_handlers.REVIEW_PREFS_CALLBACK, message)

    await renter_handlers.process_review_preferences_callback(callback, state)

    assert state.current_state == RenterState.collecting_extras.state
    assert state.data['requested_field'] == RequirementField.CORE_PREFERENCES.value
    assert state.data['collection_draft'] == original_draft
    assert any('optional preferences' in item['text'] for item in message.answers)
    keyboard = message.answers[-1]['reply_markup']
    assert any(
        button.callback_data == 'r:edit:back'
        for row in keyboard.inline_keyboard
        for button in row
    )


async def fake_user(message, telegram_user=None):
    return USER_ID


@pytest.mark.asyncio
async def test_cancel_search_discovers_durable_setup_after_process_restart(monkeypatch):
    recovered = SimpleNamespace(
        session=SimpleNamespace(id=DRAFT_ID, version=7),
        requirements={
            **complete_draft(),
            'raw_requirement_text': 'private room in Kondapur',
        },
    )
    monkeypatch.setattr(renter_handlers, 'get_or_create_user', fake_user)
    monkeypatch.setattr(
        renter_handlers.req_service,
        'get_owned_search_draft',
        lambda user_id: recovered,
    )
    monkeypatch.setattr(
        renter_handlers.req_service,
        'get_current_search',
        lambda user_id: (_ for _ in ()).throw(ValueError('none')),
    )
    monkeypatch.setattr(
        renter_handlers.req_service,
        'get_editable_search',
        lambda user_id: (_ for _ in ()).throw(ValueError('none')),
    )
    state = FakeState()
    message = FakeMessage('/cancel_search')

    await renter_handlers._request_cancel_confirmation(message, state)

    assert state.current_state == RenterState.confirming_conversational_action.state
    assert state.data['pending_action']['action'] == 'discard_setup'
    assert state.data['pending_action']['search_id'] == str(DRAFT_ID)
    assert state.data['pending_action']['search_version'] == 7


@pytest.mark.asyncio
async def test_lost_update_response_recovers_exact_committed_draft(monkeypatch):
    recovered = SimpleNamespace(
        session=SimpleNamespace(id=DRAFT_ID, version=8),
        requirements=complete_draft(),
    )
    monkeypatch.setattr(
        renter_handlers.req_service,
        'update_renter_search_draft',
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError('transport response lost')
        ),
    )
    monkeypatch.setattr(
        renter_handlers.req_service,
        'get_owned_search_draft',
        lambda user_id, search_id: recovered,
    )
    monkeypatch.setattr(
        renter_handlers.req_service,
        'recovered_draft_matches',
        lambda *args, **kwargs: True,
    )
    state = review_state(search_version=7)
    message = FakeMessage()
    draft = RenterRequirementDraft.from_requirements(complete_draft())

    persisted = await renter_handlers._persist_collection_draft(
        message,
        state,
        draft,
        raw_turns=['private room in Kondapur'],
    )

    assert persisted['search_id'] == str(DRAFT_ID)
    assert persisted['search_version'] == 8
    assert persisted['persisted_snapshot_hash'] == collection_signature(draft)


@pytest.mark.asyncio
async def test_setup_cancellation_waits_for_confirmation_and_closes_owned_version(
    monkeypatch,
):
    cancel_calls = []
    monkeypatch.setattr(renter_handlers, 'get_or_create_user', fake_user)
    monkeypatch.setattr(
        renter_handlers.req_service,
        'get_editable_search',
        lambda user_id: (_ for _ in ()).throw(ValueError('none')),
    )
    monkeypatch.setattr(
        renter_handlers.req_service,
        'cancel_renter_searches',
        lambda user_id, **kwargs: cancel_calls.append((user_id, kwargs)),
    )
    state = FakeState(
        RenterState.reviewing_requirements.state,
        {
            'collection_draft': complete_draft(),
            'search_id': str(DRAFT_ID),
            'search_version': 7,
        },
    )
    message = FakeMessage()
    original_draft = deepcopy(state.data['collection_draft'])

    await renter_handlers._request_cancel_confirmation(message, state)

    assert cancel_calls == []
    assert state.data['collection_draft'] == original_draft
    assert state.data['pending_action']['action'] == 'discard_setup'

    await renter_handlers._confirm_pending_action(message, state)

    assert cancel_calls == [(
        USER_ID,
        {
            'draft_search_id': DRAFT_ID,
            'draft_expected_version': 7,
        },
    )]
    assert state.cleared is True


@pytest.mark.asyncio
async def test_active_cancellation_waits_for_confirmation_and_closes_owned_version(
    monkeypatch,
):
    cancel_calls = []
    monkeypatch.setattr(renter_handlers, 'get_or_create_user', fake_user)
    monkeypatch.setattr(
        renter_handlers.req_service,
        'get_editable_search',
        lambda user_id: (
            {'id': str(ACTIVE_ID), 'status': 'ACTIVE', 'version': 13},
            {},
        ),
    )
    monkeypatch.setattr(
        renter_handlers.req_service,
        'cancel_renter_searches',
        lambda user_id, **kwargs: cancel_calls.append((user_id, kwargs)),
    )
    state = FakeState()
    message = FakeMessage()

    await renter_handlers._request_cancel_confirmation(message, state)

    assert cancel_calls == []
    assert state.data['pending_action']['action'] == 'cancel_search'

    await renter_handlers._confirm_pending_action(message, state)

    assert cancel_calls == [(
        USER_ID,
        {
            'open_search_id': ACTIVE_ID,
            'open_expected_version': 13,
        },
    )]
    assert state.cleared is True


@pytest.mark.asyncio
async def test_cancel_both_waits_for_scope_choice_then_uses_both_owned_versions(
    monkeypatch,
):
    cancel_calls = []
    monkeypatch.setattr(renter_handlers, 'get_or_create_user', fake_user)
    monkeypatch.setattr(
        renter_handlers.req_service,
        'get_editable_search',
        lambda user_id: (
            {'id': str(ACTIVE_ID), 'status': 'PAUSED', 'version': 13},
            {},
        ),
    )
    monkeypatch.setattr(
        renter_handlers.req_service,
        'cancel_renter_searches',
        lambda user_id, **kwargs: cancel_calls.append((user_id, kwargs)),
    )
    state = FakeState(
        RenterState.reviewing_requirements.state,
        {
            'collection_draft': complete_draft(),
            'search_id': str(DRAFT_ID),
            'search_version': 7,
        },
    )
    message = FakeMessage()
    original_draft = deepcopy(state.data['collection_draft'])

    await renter_handlers._request_cancel_confirmation(message, state)

    assert cancel_calls == []
    assert state.data['collection_draft'] == original_draft
    assert state.data['pending_action']['action'] == 'cancel_scope'
    callback = FakeCallback(renter_handlers.CANCEL_BOTH_CALLBACK, message)

    await renter_handlers.process_cancel_scope_callback(callback, state)

    assert cancel_calls == [(
        USER_ID,
        {
            'draft_search_id': DRAFT_ID,
            'draft_expected_version': 7,
            'open_search_id': ACTIVE_ID,
            'open_expected_version': 13,
        },
    )]
    assert state.cleared is True


@pytest.mark.asyncio
async def test_expired_confirmation_never_clears_a_newer_collection():
    state = FakeState(
        RenterState.waiting_for_requirement.state,
        {'collection_draft': complete_draft()},
    )
    message = FakeMessage()

    await renter_handlers._confirm_pending_action(
        message,
        state,
        callback_message_id=10,
    )

    assert state.cleared is False
    assert state.data['collection_draft'] == complete_draft()


@pytest.mark.asyncio
async def test_old_confirmation_button_cannot_confirm_new_pending_action():
    pending = {
        'action': 'discard_setup',
        'return_state': RenterState.reviewing_requirements.state,
        'search_id': str(DRAFT_ID),
        'search_version': 7,
        'confirmation_message_id': 200,
    }
    state = FakeState(
        RenterState.confirming_conversational_action.state,
        {
            'collection_draft': complete_draft(),
            'pending_action': pending,
        },
    )
    message = FakeMessage()

    await renter_handlers._confirm_pending_action(
        message,
        state,
        callback_message_id=100,
    )

    assert state.data['pending_action'] == pending
    assert state.cleared is False
    assert 'older request' in message.answers[-1]['text']


@pytest.mark.asyncio
async def test_cancel_active_scope_preserves_unfinished_setup(monkeypatch):
    cancel_calls = []
    monkeypatch.setattr(renter_handlers, 'get_or_create_user', fake_user)
    monkeypatch.setattr(
        renter_handlers.req_service,
        'get_editable_search',
        lambda user_id: (
            {'id': str(ACTIVE_ID), 'status': 'ACTIVE', 'version': 13},
            {},
        ),
    )
    monkeypatch.setattr(
        renter_handlers.req_service,
        'cancel_renter_searches',
        lambda user_id, **kwargs: cancel_calls.append((user_id, kwargs)),
    )
    state = FakeState(
        RenterState.reviewing_requirements.state,
        {
            'collection_draft': complete_draft(),
            'search_id': str(DRAFT_ID),
            'search_version': 7,
        },
    )
    message = FakeMessage()
    await renter_handlers._request_cancel_confirmation(message, state)
    callback = FakeCallback(renter_handlers.CANCEL_ACTIVE_CALLBACK, message)

    await renter_handlers.process_cancel_scope_callback(callback, state)

    assert cancel_calls == [(
        USER_ID,
        {
            'open_search_id': ACTIVE_ID,
            'open_expected_version': 13,
        },
    )]
    assert state.current_state == RenterState.reviewing_requirements.state
    assert state.data['collection_draft'] == complete_draft()
    assert state.cleared is False
