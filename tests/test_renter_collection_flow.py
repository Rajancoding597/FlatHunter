from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest

from app.requirements.collector import (
    CollectionMode,
    CollectionProgress,
    CollectionControlIntent,
    ConflictResolution,
    RequirementPatchOperation,
    RequirementField,
    RequirementTurnPatch,
    RenterRequirementDraft,
    advance_collection_progress,
    apply_requirement_patch,
    collection_signature,
    describe_requirement_changes,
    detect_collection_control_intents,
    next_required_field,
    parse_budget,
    parse_requirement_turn,
    resolve_requirement_conflict,
    validate_requirement_turn_patch_grounding,
)
from app.requirements.presentation import format_requirements, missing_core_fields
from app.requirements.schemas import RequirementChangeOperation
from app.telegram import renter_handlers
from app.telegram.renter_conversation import RenterIntent, RenterTurnDecision
from app.telegram.states import RenterState


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
        self.current_state = value.state if hasattr(value, "state") else value

    async def clear(self):
        self.current_state = None
        self.data.clear()
        self.cleared = True


class FakeBot:
    def __init__(self):
        self.actions = []

    async def send_chat_action(self, **kwargs):
        self.actions.append(kwargs)


class FakeMessage:
    def __init__(self, text, *, telegram_user_id=42):
        self.text = text
        self.answers = []
        self.from_user = SimpleNamespace(
            id=telegram_user_id,
            username="renter",
            full_name="Test Renter",
        )
        self.chat = SimpleNamespace(id=telegram_user_id)
        self.bot = FakeBot()

    async def answer(self, text, **kwargs):
        self.answers.append({"text": text, **kwargs})


class StatusResult:
    def __init__(self, data):
        self.data = data


class StatusQuery:
    def __init__(self, database, table_name):
        self.database = database
        self.table_name = table_name

    def select(self, fields):
        return self

    def eq(self, field, value):
        return self

    def in_(self, field, values):
        if self.table_name == "search_sessions" and field == "status":
            self.database.requested_statuses = list(values)
        return self

    def order(self, field, **kwargs):
        return self

    def limit(self, value):
        return self

    def execute(self):
        if self.table_name == "users":
            return StatusResult([{"id": "user-1"}])
        if self.table_name == "search_sessions":
            return StatusResult([])
        raise AssertionError(f"Unexpected table: {self.table_name}")


class StatusDatabase:
    def __init__(self):
        self.requested_statuses = None

    def table(self, name):
        return StatusQuery(self, name)


def requirement_decision(text):
    return RenterTurnDecision(
        intents=[RenterIntent.REQUIREMENT_INPUT],
        requirement_or_edit_text=text,
    )


def operation_values(patch):
    return {operation.field: operation.value for operation in patch.operations}


def all_callback_data(keyboard):
    return [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    ]


def test_screenshot_phrasing_extracts_only_explicit_fields():
    patch = parse_requirement_turn(
        "I am looking for a single room to chip in. It could possibly be a 3BHK, "
        "but 2BHK could also work fine. My rent would be in range 20k-25k."
    )

    values = operation_values(patch)

    assert values[RequirementField.RENTAL_ARRANGEMENT] == ["PRIVATE_ROOM"]
    assert values[RequirementField.HOME_CONFIGURATIONS] == ["3BHK", "2BHK"]
    assert values[RequirementField.BUDGET] == {
        "target_rent": 20_000,
        "max_rent": 25_000,
    }
    assert RequirementField.PREFERRED_LOCATIONS not in values


def test_screenshot_turns_accumulate_without_forgetting_previous_fields():
    draft = RenterRequirementDraft()
    turns = [
        (
            "Kondapur 2bhk 30k",
            RequirementField.RENTAL_ARRANGEMENT,
        ),
        (
            "Tomorrow",
            RequirementField.RENTAL_ARRANGEMENT,
        ),
        (
            "Full property",
            RequirementField.RENTAL_ARRANGEMENT,
        ),
    ]

    snapshots = []
    for text, requested_field in turns:
        result = apply_requirement_patch(
            draft,
            parse_requirement_turn(
                text,
                requested_field=requested_field,
                now=date(2026, 8, 9),
            ),
        )
        assert result.pending_conflict is None
        draft = result.draft
        snapshots.append(draft.model_dump(mode="json"))

    assert snapshots[0]["preferred_locations"] == ["Kondapur"]
    assert snapshots[0]["preferred_property_configurations"] == ["2BHK"]
    assert snapshots[0]["max_rent"] == 30_000
    assert snapshots[1]["preferred_locations"] == ["Kondapur"]
    assert snapshots[1]["preferred_property_configurations"] == ["2BHK"]
    assert snapshots[1]["preferred_move_in_date"] == "2026-08-10"
    assert draft.listing_types == ["ENTIRE_PROPERTY"]
    assert draft.preferred_locations == ["Kondapur"]
    assert draft.preferred_property_configurations == ["2BHK"]
    assert draft.max_rent == 30_000
    assert draft.preferred_move_in_date == date(2026, 8, 10)
    assert draft.latest_move_in_date == date(2026, 8, 10)
    assert next_required_field(draft) is None


def test_parser_failure_preserves_draft_and_enters_guided_mode():
    draft = RenterRequirementDraft(
        preferred_locations=["Kondapur"],
        max_rent=30_000,
    )
    original = draft.model_dump(mode="json")

    progress = advance_collection_progress(
        CollectionProgress(mode=CollectionMode.HYBRID),
        draft,
        requested_field=RequirementField.RENTAL_ARRANGEMENT,
        made_progress=False,
        parser_failed=True,
        next_prompt="Are you looking for an entire property, a private room, or a shared room?",
    )

    assert progress.mode == CollectionMode.GUIDED
    assert progress.no_progress_count == 1
    assert draft.model_dump(mode="json") == original


def test_conflicting_screenshot_date_stages_change_without_losing_snapshot():
    draft = RenterRequirementDraft(
        listing_types=["ENTIRE_PROPERTY"],
        preferred_property_configurations=["2BHK"],
        configuration_answered=True,
        preferred_locations=["Kondapur"],
        max_rent=30_000,
        preferred_move_in_date=date(2026, 8, 10),
        latest_move_in_date=date(2026, 8, 10),
    )
    original = draft.model_dump(mode="json")

    merged = apply_requirement_patch(
        draft,
        parse_requirement_turn(
            "First week of next month",
            requested_field=RequirementField.MOVE_IN_TIMING,
            now=date(2026, 8, 9),
        ),
    )

    assert merged.pending_conflict is not None
    assert merged.pending_conflict.field == RequirementField.MOVE_IN_TIMING
    assert merged.draft.model_dump(mode="json") == original
    assert merged.draft.preferred_locations == ["Kondapur"]
    assert merged.draft.max_rent == 30_000


def test_relative_move_in_phrases_use_the_configured_local_date():
    tomorrow = operation_values(
        parse_requirement_turn("Tomorrow", now=date(2026, 8, 9))
    )[RequirementField.MOVE_IN_TIMING]
    first_week = operation_values(
        parse_requirement_turn(
            "Starting week next month",
            now=date(2026, 8, 9),
        )
    )[RequirementField.MOVE_IN_TIMING]

    assert tomorrow == {
        "preferred_move_in_date": "2026-08-10",
        "latest_move_in_date": "2026-08-10",
    }
    assert first_week == {
        "preferred_move_in_date": "2026-09-01",
        "latest_move_in_date": "2026-09-07",
    }


def test_show_requirements_and_done_are_control_intents_not_requirement_evidence():
    summary = detect_collection_control_intents(
        "What is the final requirement you captured? Show me."
    )
    finish = detect_collection_control_intents(
        "That's all, don't ask anything else"
    )

    assert summary == [CollectionControlIntent.SHOW_SUMMARY]
    assert finish == [CollectionControlIntent.FINISH]
    assert parse_requirement_turn(
        "What is the final requirement you captured? Show me."
    ).operations == []


@pytest.mark.asyncio
async def test_show_requirements_does_not_mutate_collection_state(monkeypatch):
    requirements = {
        "listing_types": ["PRIVATE_ROOM"],
        "preferred_property_configurations": ["2BHK", "3BHK"],
        "preferred_locations": ["Gachibowli", "Madhapur"],
        "target_rent": 20_000,
        "max_rent": 25_000,
    }

    async def fake_current_requirements(message, state, telegram_user=None):
        return deepcopy(requirements), None

    monkeypatch.setattr(
        renter_handlers,
        "_current_requirements",
        fake_current_requirements,
    )
    state = FakeState(
        RenterState.waiting_for_requirement.state,
        {
            "collection_draft": requirements,
            "collection_mode": "HYBRID",
            "no_progress_count": 0,
        },
    )
    original = deepcopy(state.data)
    message = FakeMessage("What is the final requirement you captured? Show me.")

    await renter_handlers.process_requirement(message, state)

    combined = "\n".join(answer["text"] for answer in message.answers)
    assert "Gachibowli" in combined
    assert "2BHK" in combined.upper()
    assert state.data == original
    assert state.current_state == RenterState.waiting_for_requirement.state


@pytest.mark.asyncio
async def test_handler_replays_screenshot_turns_without_full_history_reextraction(
    monkeypatch,
):
    async def fake_decision(message, state):
        return requirement_decision(message.text)

    async def forbidden_full_extraction(text):
        raise AssertionError("collection must not re-extract the full chat history")

    async def forbidden_llm_patch(prompt, schema):
        raise AssertionError("these structured turns are deterministic")

    async def fake_persist(message, state, draft, **kwargs):
        return {
            "user_id": "user-1",
            "creation_key": "11111111-1111-1111-1111-111111111111",
            "search_id": "22222222-2222-2222-2222-222222222222",
            "search_version": 1,
        }

    monkeypatch.setattr(
        renter_handlers,
        "_classify_renter_turn",
        fake_decision,
    )
    monkeypatch.setattr(
        renter_handlers.req_service,
        "parse_requirements",
        forbidden_full_extraction,
    )
    monkeypatch.setattr(
        renter_handlers.req_service.llm,
        "generate_structured",
        forbidden_llm_patch,
    )
    monkeypatch.setattr(
        renter_handlers,
        "_persist_collection_draft",
        fake_persist,
    )
    monkeypatch.setattr(
        renter_handlers.tracer,
        "log_event",
        lambda *args, **kwargs: None,
    )
    state = FakeState(
        RenterState.waiting_for_requirement.state,
        {
            "collection_draft": {},
            "collection_progress": {
                "mode": "HYBRID",
                "no_progress_count": 0,
                "field_failure_count": 0,
            },
        },
    )

    first = FakeMessage("Kondapur 2bhk 30k")
    await renter_handlers.process_requirement(first, state)
    first_snapshot = deepcopy(state.data["collection_draft"])

    assert first_snapshot["preferred_locations"] == ["Kondapur"]
    assert first_snapshot["preferred_property_configurations"] == ["2BHK"]
    assert first_snapshot["max_rent"] == 30_000

    second = FakeMessage("Tomorrow")
    await renter_handlers.process_requirement(second, state)
    second_snapshot = deepcopy(state.data["collection_draft"])

    assert second_snapshot["preferred_locations"] == ["Kondapur"]
    assert second_snapshot["preferred_property_configurations"] == ["2BHK"]
    assert second_snapshot["max_rent"] == 30_000
    assert second_snapshot["preferred_move_in_date"] is not None

    third = FakeMessage("Full property")
    await renter_handlers.process_requirement(third, state)
    final_snapshot = state.data["collection_draft"]

    assert final_snapshot["listing_types"] == ["ENTIRE_PROPERTY"]
    assert final_snapshot["preferred_locations"] == ["Kondapur"]
    assert final_snapshot["preferred_property_configurations"] == ["2BHK"]
    assert final_snapshot["max_rent"] == 30_000
    assert state.current_state == RenterState.reviewing_requirements.state
    assert state.data["search_id"] == "22222222-2222-2222-2222-222222222222"


@pytest.mark.asyncio
async def test_done_with_complete_requirements_opens_review(monkeypatch):
    requirements = {
        "listing_types": ["ENTIRE_PROPERTY"],
        "preferred_property_configurations": ["2BHK"],
        "configuration_answered": True,
        "preferred_locations": ["Kondapur"],
        "target_rent": 30_000,
        "max_rent": 30_000,
        "preferred_move_in_date": "2026-09-01",
        "latest_move_in_date": "2026-09-07",
    }

    async def fake_persist(message, state, draft, **kwargs):
        return {
            "user_id": "user-1",
            "creation_key": "11111111-1111-1111-1111-111111111111",
            "search_id": "22222222-2222-2222-2222-222222222222",
            "search_version": 1,
        }

    monkeypatch.setattr(
        renter_handlers,
        "_persist_collection_draft",
        fake_persist,
    )
    state = FakeState(
        RenterState.waiting_for_requirement.state,
        {
            "collection_draft": requirements,
            "collection_progress": {
                "mode": "HYBRID",
                "no_progress_count": 0,
                "field_failure_count": 0,
            },
        },
    )
    message = FakeMessage("That's all")

    await renter_handlers._process_collection_turn(message, state)

    assert state.current_state == RenterState.reviewing_requirements.state
    assert state.data["search_id"] == "22222222-2222-2222-2222-222222222222"
    assert "Review your search" in message.answers[-1]["text"]
    draft_id = "22222222-2222-2222-2222-222222222222"
    assert all_callback_data(message.answers[-1]["reply_markup"]) == [
        renter_handlers.REVIEW_START_PREFIX + draft_id + ':1',
        renter_handlers.REVIEW_EDIT_PREFIX + draft_id + ':1',
        renter_handlers.REVIEW_PREFS_PREFIX + draft_id + ':1',
        renter_handlers.REVIEW_CANCEL_PREFIX + draft_id + ':1',
    ]


@pytest.mark.asyncio
async def test_value_plus_finish_is_saved_before_review(monkeypatch):
    requirements = {
        'listing_types': ['PRIVATE_ROOM'],
        'preferred_locations': [],
        'target_rent': 30_000,
        'max_rent': 30_000,
        'preferred_move_in_date': '2026-09-01',
        'latest_move_in_date': '2026-09-07',
    }

    async def fake_persist(message, state, draft, **kwargs):
        assert draft.preferred_locations == ['Kondapur']
        return {
            'user_id': 'user-1',
            'search_id': '22222222-2222-2222-2222-222222222222',
            'search_version': 1,
            'persisted_snapshot_hash': collection_signature(draft),
        }

    monkeypatch.setattr(
        renter_handlers,
        '_persist_collection_draft',
        fake_persist,
    )
    state = FakeState(
        RenterState.waiting_for_requirement.state,
        {
            'collection_draft': requirements,
            'collection_progress': CollectionProgress().model_dump(mode='json'),
            'requested_field': RequirementField.PREFERRED_LOCATIONS.value,
        },
    )
    message = FakeMessage("Kondapur, that's all")

    await renter_handlers._process_collection_turn(message, state)

    assert state.data['collection_draft']['preferred_locations'] == ['Kondapur']
    assert state.current_state == RenterState.reviewing_requirements.state
    assert 'Review your search' in message.answers[-1]['text']


@pytest.mark.asyncio
async def test_start_resumes_latest_owned_durable_review(monkeypatch):
    draft = {
        'listing_types': ['PRIVATE_ROOM'],
        'preferred_locations': ['Kondapur'],
        'target_rent': 30_000,
        'max_rent': 30_000,
        'preferred_move_in_date': '2026-09-01',
        'latest_move_in_date': '2026-09-07',
        'raw_requirement_text': 'private room in Kondapur',
    }
    recovered = SimpleNamespace(
        session=SimpleNamespace(
            id='22222222-2222-2222-2222-222222222222',
            version=4,
        ),
        requirements=draft,
    )

    async def fake_user(message, telegram_user=None):
        return '11111111-1111-1111-1111-111111111111'

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
    state = FakeState()
    message = FakeMessage('/start')

    await renter_handlers.cmd_start(message, state)

    assert state.current_state == RenterState.reviewing_requirements.state
    assert state.data['search_version'] == 4
    callbacks = all_callback_data(message.answers[-1]['reply_markup'])
    assert callbacks[0].endswith(':4')
    assert 'Resumed your saved review' in message.answers[-1]['text']


@pytest.mark.asyncio
async def test_llm_failure_keeps_snapshot_and_switches_to_guided_mode(monkeypatch):
    async def fake_decision(message, state):
        return requirement_decision(message.text)

    async def broken_structured(prompt, schema):
        raise RuntimeError("provider secret must not reach renter")

    monkeypatch.setattr(
        renter_handlers,
        "_classify_renter_turn",
        fake_decision,
    )
    monkeypatch.setattr(
        renter_handlers.req_service.llm,
        "generate_structured",
        broken_structured,
    )
    monkeypatch.setattr(
        renter_handlers.tracer,
        "log_event",
        lambda *args, **kwargs: None,
    )
    original = {
        "listing_types": [],
        "preferred_locations": ["Kondapur"],
        "max_rent": 30_000,
    }
    state = FakeState(
        RenterState.waiting_for_requirement.state,
        {
            "collection_draft": original,
            "collection_progress": {
                "mode": "HYBRID",
                "no_progress_count": 0,
                "field_failure_count": 0,
            },
            "requested_field": "listing_types",
        },
    )
    message = FakeMessage("I am not sure")

    await renter_handlers._process_collection_turn(message, state)

    assert state.data["collection_progress"]["mode"] == "GUIDED"
    assert state.data["collection_draft"]["preferred_locations"] == ["Kondapur"]
    assert state.data["collection_draft"]["max_rent"] == 30_000
    assert "guided" in message.answers[-1]["text"].casefold()
    assert "provider secret" not in message.answers[-1]["text"]
    assert message.answers[-1]["reply_markup"] is not None


def test_every_review_and_conflict_callback_stays_under_telegram_limit():
    callback_data = all_callback_data(renter_handlers._review_keyboard())
    callback_data += all_callback_data(renter_handlers._conflict_keyboard())

    assert callback_data
    assert all(len(value.encode("utf-8")) <= 64 for value in callback_data)


@pytest.mark.asyncio
async def test_replacement_confirmation_never_closes_the_current_search(monkeypatch):
    async def fake_user(message, telegram_user=None):
        return "user-1"

    def unexpected_database_write():
        raise AssertionError("replacement collection must not close the live search")

    monkeypatch.setattr(renter_handlers, "get_or_create_user", fake_user)
    monkeypatch.setattr(
        renter_handlers,
        "get_supabase_client",
        unexpected_database_write,
    )
    state = FakeState(
        RenterState.confirming_conversational_action.state,
        {
            "pending_action": {
                "action": "replace_search",
                "search_id": "search-1",
                "search_version": 4,
            },
        },
    )
    message = FakeMessage("yes")

    await renter_handlers._confirm_pending_action(message, state)

    assert state.current_state == RenterState.waiting_for_requirement.state
    assert state.data["replacement_search_id"] == "search-1"
    assert state.data["replacement_search_version"] == 4
    assert "stay active" in message.answers[-1]["text"]


@pytest.mark.asyncio
async def test_cancel_search_recognizes_unfinished_setup_without_active_search(
    monkeypatch,
):
    async def fake_user(message, telegram_user=None):
        return "user-1"

    def no_saved_search(user_id):
        raise ValueError("no active search")

    monkeypatch.setattr(renter_handlers, "get_or_create_user", fake_user)
    monkeypatch.setattr(
        renter_handlers.req_service,
        "get_editable_search",
        no_saved_search,
    )
    setup = {
        "preferred_locations": ["Kondapur"],
        "max_rent": 30_000,
    }
    state = FakeState(
        RenterState.waiting_for_requirement.state,
        {"collection_draft": setup},
    )
    message = FakeMessage("/cancel_search")

    await renter_handlers.cmd_cancel_search(message, state)

    assert state.data["collection_draft"] == setup
    assert "setup" in message.answers[-1]["text"].casefold()
    assert message.answers[-1].get("reply_markup") is not None


@pytest.mark.asyncio
async def test_mysearch_status_never_selects_review_drafts(monkeypatch):
    database = StatusDatabase()
    monkeypatch.setattr(
        renter_handlers,
        "get_supabase_client",
        lambda: database,
    )
    message = FakeMessage("/mysearch")

    await renter_handlers.cmd_mysearch(message)

    assert database.requested_statuses == ["ACTIVE", "PAUSED"]
    assert "active searches" in message.answers[-1]["text"]


def test_removing_existing_preference_requires_confirmation():
    draft = RenterRequirementDraft(
        core_preferences={
            "parking": {"value": True, "importance": "PREFERRED"},
        },
    )

    patch = parse_requirement_turn("remove parking")
    merged = apply_requirement_patch(draft, patch)

    assert merged.pending_conflict is not None
    assert "parking" in merged.draft.core_preferences
    confirmed = resolve_requirement_conflict(
        draft,
        merged.pending_conflict,
        ConflictResolution.USE_PROPOSED,
    )
    assert "parking" not in confirmed.draft.core_preferences


def test_new_required_preference_requires_confirmation():
    draft = RenterRequirementDraft()
    patch = RequirementTurnPatch(operations=[
        RequirementPatchOperation(
            field=RequirementField.CORE_PREFERENCES,
            operation=RequirementChangeOperation.ADD,
            value={
                "parking": {"value": True, "importance": "REQUIRED"},
            },
        ),
    ])

    merged = apply_requirement_patch(draft, patch)

    assert merged.pending_conflict is not None
    assert merged.draft.core_preferences == {}


def test_multiple_core_conflicts_converge_without_partial_application():
    draft = RenterRequirementDraft(
        preferred_locations=["Kondapur"],
        target_rent=30_000,
        max_rent=30_000,
    )
    patch = RequirementTurnPatch(operations=[
        RequirementPatchOperation(
            field=RequirementField.PREFERRED_LOCATIONS,
            operation=RequirementChangeOperation.SET,
            value=["Madhapur"],
        ),
        RequirementPatchOperation(
            field=RequirementField.BUDGET,
            operation=RequirementChangeOperation.SET,
            value={"target_rent": 40_000, "max_rent": 40_000},
        ),
    ])

    first = apply_requirement_patch(draft, patch)
    assert first.pending_conflict is not None
    assert first.draft.preferred_locations == ["Kondapur"]
    assert first.draft.max_rent == 30_000

    second = resolve_requirement_conflict(
        draft,
        first.pending_conflict,
        ConflictResolution.USE_PROPOSED,
    )
    assert second.pending_conflict is not None
    assert second.draft.preferred_locations == ["Kondapur"]
    assert second.draft.max_rent == 30_000

    final = resolve_requirement_conflict(
        draft,
        second.pending_conflict,
        ConflictResolution.USE_PROPOSED,
    )
    assert final.pending_conflict is None
    assert final.draft.preferred_locations == ["Madhapur"]
    assert final.draft.max_rent == 40_000


def test_mixed_location_addition_and_preference_are_both_applied():
    draft = RenterRequirementDraft(preferred_locations=["Kondapur"])

    patch = parse_requirement_turn("add Madhapur and parking")
    merged = apply_requirement_patch(draft, patch)

    assert merged.pending_conflict is None
    assert merged.draft.preferred_locations == ["Kondapur", "Madhapur"]
    assert merged.draft.core_preferences["parking"].value is True


def test_explicit_location_replacement_extracts_only_the_new_area():
    draft = RenterRequirementDraft(preferred_locations=["Kondapur"])

    patch = parse_requirement_turn("replace Kondapur with Madhapur")
    merged = apply_requirement_patch(draft, patch)

    assert merged.pending_conflict is None
    assert merged.draft.preferred_locations == ["Madhapur"]


def test_switching_from_private_room_to_entire_property_requires_configuration():
    private = apply_requirement_patch(
        RenterRequirementDraft.from_requirements({}),
        parse_requirement_turn("private room"),
    ).draft
    assert RequirementField.HOME_CONFIGURATIONS not in private.missing_required_fields()

    staged = apply_requirement_patch(
        private,
        parse_requirement_turn("entire property"),
    )
    assert staged.pending_conflict is not None
    entire = resolve_requirement_conflict(
        private,
        staged.pending_conflict,
        ConflictResolution.USE_PROPOSED,
    ).draft

    assert RequirementField.HOME_CONFIGURATIONS in entire.missing_required_fields()


def test_uncertain_reply_is_not_saved_as_a_location():
    patch = parse_requirement_turn(
        "I am not sure",
        requested_field=RequirementField.PREFERRED_LOCATIONS,
    )

    assert patch.operations == []


def test_removing_last_bhk_from_entire_property_makes_configuration_missing():
    draft = RenterRequirementDraft(
        listing_types=["ENTIRE_PROPERTY"],
        preferred_property_configurations=["2BHK"],
        configuration_answered=True,
    )
    staged = apply_requirement_patch(
        draft,
        parse_requirement_turn(
            "remove 2BHK",
            requested_field=RequirementField.HOME_CONFIGURATIONS,
        ),
    )
    assert staged.pending_conflict is not None

    removed = resolve_requirement_conflict(
        draft,
        staged.pending_conflict,
        ConflictResolution.USE_PROPOSED,
    ).draft
    assert RequirementField.HOME_CONFIGURATIONS in removed.missing_required_fields()


def test_invalid_llm_arrangement_never_enters_canonical_state():
    draft = RenterRequirementDraft()
    patch = RequirementTurnPatch(operations=[
        RequirementPatchOperation(
            field=RequirementField.RENTAL_ARRANGEMENT,
            operation=RequirementChangeOperation.SET,
            value=["PENTHOUSE"],
        ),
    ])

    with pytest.raises(ValueError, match="Unsupported rental arrangement"):
        apply_requirement_patch(draft, patch)
    assert draft.listing_types == []


@pytest.mark.asyncio
async def test_hybrid_turn_keeps_deterministic_room_and_llm_only_preference(
    monkeypatch,
):
    async def extracted_patch(prompt, schema):
        assert 'already_extracted_deterministically' in prompt
        return RequirementTurnPatch(operations=[
            RequirementPatchOperation(
                field=RequirementField.ADDITIONAL_PREFERENCES,
                operation=RequirementChangeOperation.ADD,
                value={'balcony': 'preferred'},
            ),
        ])

    monkeypatch.setattr(
        renter_handlers.req_service.llm,
        'generate_structured',
        extracted_patch,
    )
    state = FakeState(
        RenterState.waiting_for_requirement.state,
        {
            'collection_draft': {},
            'collection_progress': CollectionProgress().model_dump(mode='json'),
            'requested_field': RequirementField.RENTAL_ARRANGEMENT.value,
        },
    )
    message = FakeMessage('private room with balcony')

    await renter_handlers._process_collection_turn(message, state)

    assert state.data['collection_draft']['listing_types'] == ['PRIVATE_ROOM']
    assert state.data['collection_draft']['additional_preferences'] == {
        'balcony': 'preferred',
    }


@pytest.mark.asyncio
async def test_llm_enrichment_failure_still_accepts_deterministic_fact(monkeypatch):
    async def broken_patch(prompt, schema):
        raise RuntimeError('provider detail')

    monkeypatch.setattr(
        renter_handlers.req_service.llm,
        'generate_structured',
        broken_patch,
    )
    state = FakeState(
        RenterState.waiting_for_requirement.state,
        {
            'collection_draft': {},
            'collection_progress': CollectionProgress().model_dump(mode='json'),
            'requested_field': RequirementField.RENTAL_ARRANGEMENT.value,
        },
    )
    message = FakeMessage('private room with balcony')

    await renter_handlers._process_collection_turn(message, state)

    assert state.data['collection_draft']['listing_types'] == ['PRIVATE_ROOM']
    assert state.data['collection_progress']['mode'] == CollectionMode.HYBRID.value
    assert all('provider detail' not in item['text'] for item in message.answers)


@pytest.mark.asyncio
async def test_requirement_and_pause_intents_both_execute_in_order(monkeypatch):
    pause_calls = []

    async def pause(message, state):
        pause_calls.append(message.text)
        await message.answer('Paused the active search.')

    monkeypatch.setattr(renter_handlers, 'cmd_pause_search', pause)
    state = FakeState(
        RenterState.waiting_for_requirement.state,
        {
            'collection_draft': {},
            'collection_progress': CollectionProgress().model_dump(mode='json'),
            'requested_field': RequirementField.RENTAL_ARRANGEMENT.value,
        },
    )
    message = FakeMessage('add parking and pause my search')

    await renter_handlers._process_collection_turn(message, state)

    assert state.data['collection_draft']['core_preferences']['parking']['value'] is True
    assert pause_calls == ['add parking and pause my search']
    assert 'Saved:' in message.answers[0]['text']
    assert 'Paused' in message.answers[1]['text']


def test_or_adds_alternative_locations_but_explicit_replace_still_replaces():
    draft = RenterRequirementDraft(preferred_locations=['Kondapur'])

    added = apply_requirement_patch(
        draft,
        parse_requirement_turn(
            'Madhapur or Gachibowli',
            requested_field=RequirementField.PREFERRED_LOCATIONS,
        ),
    )
    assert added.pending_conflict is None
    assert added.draft.preferred_locations == [
        'Kondapur', 'Madhapur', 'Gachibowli',
    ]

    replaced = apply_requirement_patch(
        draft,
        parse_requirement_turn(
            'replace Kondapur with Madhapur or Gachibowli',
            requested_field=RequirementField.PREFERRED_LOCATIONS,
        ),
    )
    assert replaced.pending_conflict is None
    assert replaced.draft.preferred_locations == ['Madhapur', 'Gachibowli']


def test_persisted_explicit_any_is_visible_and_not_reported_missing():
    stored = {
        'listing_types': ['ENTIRE_PROPERTY'],
        'preferred_property_configurations': [],
        'preferred_locations': ['Kondapur'],
        'max_rent': 30_000,
        'preferred_move_in_date': '2026-09-01',
        'additional_preferences': {
            '__flathunter_configuration_answered': 'true',
        },
    }

    assert 'home configuration' not in missing_core_fields(stored)
    rendered = format_requirements(stored)
    assert '<b>Configuration:</b> Any' in rendered
    assert '__flathunter_' not in rendered


def test_requirement_delta_escapes_untrusted_location_markup():
    before = RenterRequirementDraft()
    after = RenterRequirementDraft(preferred_locations=['<b>Kondapur</b>'])

    recap = '; '.join(describe_requirement_changes(before, after))

    assert '<b>Kondapur</b>' not in recap
    assert '&lt;B&gt;Kondapur&lt;/B&gt;' in recap


@pytest.mark.parametrize(
    'text',
    [
        'Is parking usually included?',
        'What documents do I need?',
    ],
)
def test_information_questions_never_become_requirement_patches(text):
    assert parse_requirement_turn(text).operations == []


def test_negative_preferences_preserve_polarity_and_do_not_create_location():
    parking = parse_requirement_turn(
        'I do not need parking',
        requested_field=RequirementField.PREFERRED_LOCATIONS,
    )
    assert len(parking.operations) == 1
    assert parking.operations[0].operation == RequirementChangeOperation.REMOVE

    without_parking = parse_requirement_turn(
        'without parking',
        requested_field=RequirementField.PREFERRED_LOCATIONS,
    )
    assert all(
        operation.field != RequirementField.PREFERRED_LOCATIONS
        for operation in without_parking.operations
    )

    unfurnished = parse_requirement_turn('not furnished')
    assert unfurnished.operations[0].value['furnished']['value'] is False

    metro = parse_requirement_turn(
        'I do not need to be near metro',
        requested_field=RequirementField.PREFERRED_LOCATIONS,
    )
    assert all(
        operation.field != RequirementField.PREFERRED_LOCATIONS
        for operation in metro.operations
    )


def test_mixed_operation_verbs_defer_instead_of_leaking_across_fields():
    assert parse_requirement_turn(
        'remove parking and add Madhapur',
    ).operations == []
    assert parse_requirement_turn(
        'remove Gachibowli and set budget to 30k',
    ).operations == []

    assert parse_requirement_turn(
        'increase budget to 40k and Gachibowli',
    ).operations == []
    assert parse_requirement_turn(
        'change my budget to 40k and Gachibowli',
    ).operations == []


def test_terminal_finish_phrase_does_not_become_or_drop_a_location():
    patch = parse_requirement_turn(
        "Kondapur, that's all",
        requested_field=RequirementField.PREFERRED_LOCATIONS,
    )
    assert operation_values(patch)[RequirementField.PREFERRED_LOCATIONS] == [
        'Kondapur',
    ]


@pytest.mark.parametrize(
    'text',
    ['Gachibowli and 40k', '40k and Gachibowli'],
)
def test_location_and_budget_are_extracted_in_either_order(text):
    values = operation_values(parse_requirement_turn(text))
    assert values[RequirementField.PREFERRED_LOCATIONS] == ['Gachibowli']
    assert values[RequirementField.BUDGET]['max_rent'] == 40_000


def test_summary_suffix_is_not_saved_as_a_location():
    values = operation_values(parse_requirement_turn(
        'add parking and show me everything',
        requested_field=RequirementField.PREFERRED_LOCATIONS,
    ))
    assert RequirementField.PREFERRED_LOCATIONS not in values
    assert values[RequirementField.CORE_PREFERENCES]['parking']['value'] is True

    values = operation_values(parse_requirement_turn(
        'Kondapur and show me everything',
        requested_field=RequirementField.PREFERRED_LOCATIONS,
    ))
    assert values[RequirementField.PREFERRED_LOCATIONS] == ['Kondapur']

    values = operation_values(parse_requirement_turn(
        'show me everything and add parking',
        requested_field=RequirementField.PREFERRED_LOCATIONS,
    ))
    assert RequirementField.PREFERRED_LOCATIONS not in values
    assert values[RequirementField.CORE_PREFERENCES]['parking']['value'] is True


@pytest.mark.parametrize(
    'text,expected_locations',
    [
        ('add parking and pause my search', None),
        ('Kondapur and pause my search', ['Kondapur']),
    ],
)
def test_lifecycle_action_clause_is_not_saved_as_location(text, expected_locations):
    values = operation_values(parse_requirement_turn(
        text,
        requested_field=RequirementField.PREFERRED_LOCATIONS,
    ))
    if expected_locations is None:
        assert RequirementField.PREFERRED_LOCATIONS not in values
    else:
        assert values[RequirementField.PREFERRED_LOCATIONS] == expected_locations


def test_hi_tech_city_is_parsed_as_a_location_not_a_greeting():
    values = operation_values(parse_requirement_turn(
        'Hi Tech City and Kondapur',
        requested_field=RequirementField.PREFERRED_LOCATIONS,
    ))
    assert values[RequirementField.PREFERRED_LOCATIONS] == [
        'Hi Tech City',
        'Kondapur',
    ]


def test_llm_collection_patch_must_be_grounded_in_latest_turn():
    hallucinated = RequirementTurnPatch(operations=[
        RequirementPatchOperation(
            field=RequirementField.PREFERRED_LOCATIONS,
            operation=RequirementChangeOperation.ADD,
            value=['Madhapur'],
        ),
    ])

    with pytest.raises(ValueError, match='Unverified requirement patch field'):
        validate_requirement_turn_patch_grounding(
            hallucinated,
            'I would like parking',
        )

    grounded = RequirementTurnPatch(operations=[
        RequirementPatchOperation(
            field=RequirementField.ADDITIONAL_PREFERENCES,
            operation=RequirementChangeOperation.ADD,
            value={'balcony': True},
        ),
    ])
    assert validate_requirement_turn_patch_grounding(
        grounded,
        'A balcony would be nice',
    ) == grounded


@pytest.mark.parametrize(
    'text',
    ['20k to 25000', '20000 to 25k'],
)
def test_mixed_unit_budget_ranges_normalize_both_orders(text):
    budget = parse_budget(text)
    assert budget is not None
    assert budget.target_rent == 20_000
    assert budget.max_rent == 25_000

    patch = parse_requirement_turn(
        "Kondapur, don't ask anything else",
        requested_field=RequirementField.PREFERRED_LOCATIONS,
    )
    assert operation_values(patch)[RequirementField.PREFERRED_LOCATIONS] == [
        'Kondapur',
    ]


@pytest.mark.asyncio
async def test_ambiguous_collection_input_advances_to_guided_recovery(monkeypatch):
    async def ambiguous(message, state):
        return RenterTurnDecision(
            intents=[RenterIntent.AMBIGUOUS],
            clarification_question='Which requirement did you mean?',
            confidence=0.5,
        )

    monkeypatch.setattr(renter_handlers, '_classify_renter_turn', ambiguous)
    state = FakeState(
        RenterState.waiting_for_requirement.state,
        {
            'collection_draft': {},
            'collection_progress': CollectionProgress().model_dump(mode='json'),
            'requested_field': RequirementField.RENTAL_ARRANGEMENT.value,
        },
    )
    first = FakeMessage('Gandu')
    second = FakeMessage('still unclear')

    await renter_handlers._process_collection_turn(first, state)
    assert state.data['collection_progress']['no_progress_count'] == 1
    assert state.data['collection_progress']['mode'] == CollectionMode.HYBRID.value

    await renter_handlers._process_collection_turn(second, state)
    assert state.data['collection_progress']['mode'] == CollectionMode.GUIDED.value
    assert first.answers[-1]['text'] != second.answers[-1]['text']
    assert second.answers[-1]['reply_markup'] is not None


@pytest.mark.asyncio
async def test_idle_requirement_starts_collection_and_keeps_first_turn(monkeypatch):
    async def classify(message, state):
        return RenterTurnDecision(
            intents=[RenterIntent.REQUIREMENT_INPUT],
            requirement_or_edit_text=message.text,
        )

    async def no_active(message, state, **kwargs):
        return False

    monkeypatch.setattr(renter_handlers, '_classify_renter_turn', classify)
    monkeypatch.setattr(
        renter_handlers,
        'check_and_handle_active_searches',
        no_active,
    )
    state = FakeState()
    message = FakeMessage('2BHK in Kondapur under 30k')

    await renter_handlers.renter_fallback(message, state)

    assert state.current_state == RenterState.waiting_for_requirement.state
    assert state.data['collection_draft']['preferred_locations'] == ['Kondapur']
    assert state.data['collection_draft']['max_rent'] == 30_000
    assert state.data['collection_draft']['preferred_property_configurations'] == [
        '2BHK',
    ]


@pytest.mark.asyncio
async def test_idle_classifier_cannot_replace_original_renter_evidence(monkeypatch):
    async def classify(message, state):
        return RenterTurnDecision(
            intents=[RenterIntent.REQUIREMENT_INPUT],
            requirement_or_edit_text='2BHK in invented Madhapur under 40k',
        )

    captured = {}

    async def no_active(message, state, **kwargs):
        captured['initial'] = kwargs.get('initial_requirement_text')
        return False

    async def process(message, state, *, text=None, **kwargs):
        captured['processed'] = text

    monkeypatch.setattr(renter_handlers, '_classify_renter_turn', classify)
    monkeypatch.setattr(
        renter_handlers,
        'check_and_handle_active_searches',
        no_active,
    )
    monkeypatch.setattr(renter_handlers, '_process_collection_turn', process)
    state = FakeState()
    message = FakeMessage('help me find a place')

    await renter_handlers.renter_fallback(message, state)

    assert captured == {
        'initial': 'help me find a place',
        'processed': 'help me find a place',
    }


@pytest.mark.asyncio
async def test_classifier_edit_span_cannot_replace_original_renter_evidence(monkeypatch):
    captured = []

    async def edit(message, state, text, **kwargs):
        captured.append(text)

    monkeypatch.setattr(renter_handlers, '_handle_search_edit', edit)
    message = FakeMessage('please update what I asked for')
    decision = RenterTurnDecision(
        intents=[RenterIntent.EDIT_REQUIREMENTS],
        requirement_or_edit_text='increase budget to 40k',
    )

    handled = await renter_handlers._handle_conversational_decision(
        message,
        FakeState(),
        decision,
    )

    assert handled is True
    assert captured == ['please update what I asked for']


@pytest.mark.asyncio
async def test_idle_natural_start_uses_shared_start_action(monkeypatch):
    async def classify(message, state):
        return RenterTurnDecision(intents=[RenterIntent.START_SEARCH])

    start_calls = []

    async def start(message, state):
        start_calls.append(message.text)

    monkeypatch.setattr(renter_handlers, '_classify_renter_turn', classify)
    monkeypatch.setattr(renter_handlers, 'cmd_start', start)
    state = FakeState()
    message = FakeMessage('start searching')

    await renter_handlers.renter_fallback(message, state)

    assert start_calls == ['start searching']
