from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.common.enums import SearchStatus
from app.requirements.schemas import (
    RequirementChangeOperation,
    RequirementEditPlan,
    RequirementEditResponse,
    RequirementFieldChange,
)
from app.requirements.service import RequirementService


USER_ID = UUID('22222222-2222-2222-2222-222222222222')
DRAFT_ID = UUID('11111111-1111-1111-1111-111111111111')
OPEN_ID = UUID('33333333-3333-3333-3333-333333333333')


def session_row(search_id, *, status, version):
    return {
        'id': str(search_id),
        'user_id': str(USER_ID),
        'status': status,
        'version': version,
        'city': 'Hyderabad',
        'created_at': '2026-08-09T10:00:00+00:00',
        'updated_at': '2026-08-09T10:05:00+00:00',
        'started_at': None,
        'last_activated_at': None,
        'paused_at': None,
        'closed_at': (
            '2026-08-09T10:05:00+00:00'
            if status == SearchStatus.CLOSED.value
            else None
        ),
    }


def requirement_row(**overrides):
    row = {
        'id': '55555555-5555-5555-5555-555555555555',
        'search_id': str(OPEN_ID),
        'listing_types': ['ENTIRE_PROPERTY'],
        'preferred_locations': ['Gachibowli'],
        'acceptable_locations': [],
        'excluded_locations': [],
        'work_location': None,
        'target_rent': 20_000,
        'max_rent': 25_000,
        'preferred_move_in_date': '2026-09-01',
        'latest_move_in_date': '2026-09-07',
        'preferred_property_configurations': ['2BHK'],
        'core_preferences': {},
        'additional_preferences': {},
        'raw_requirement_text': 'initial search',
    }
    row.update(overrides)
    return row


class Execution:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error

    def execute(self):
        if self.error is not None:
            raise self.error
        return SimpleNamespace(data=self.data)


class RequirementQuery:
    def __init__(self, db):
        self.db = db
        self.search_id = None

    def select(self, *_args):
        return self

    def eq(self, field, value):
        assert field == 'search_id'
        self.search_id = value
        return self

    def execute(self):
        self.db.table_execute_count += 1
        rows = [
            dict(row)
            for row in self.db.requirements
            if str(row.get('search_id')) == str(self.search_id)
        ]
        return SimpleNamespace(data=rows)


class MutationDatabase:
    def __init__(self, *, requirements=None, responses=None, errors=None):
        self.requirements = list(requirements or [])
        self.responses = dict(responses or {})
        self.errors = dict(errors or {})
        self.rpc_calls = []
        self.table_names = []
        self.table_execute_count = 0

    def table(self, name):
        self.table_names.append(name)
        assert name == 'search_requirements'
        return RequirementQuery(self)

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        return Execution(self.responses.get(name), self.errors.get(name))


def test_cancel_both_sends_one_validated_rpc_and_preserves_result_shape():
    db = MutationDatabase(responses={
        'cancel_renter_searches': [{
            'draft_session': session_row(
                DRAFT_ID, status=SearchStatus.CLOSED.value, version=3
            ),
            'open_session': session_row(
                OPEN_ID, status=SearchStatus.CLOSED.value, version=8
            ),
            'cancelled_count': 2,
        }],
    })
    service = RequirementService(db=db, llm=object())

    result = service.cancel_renter_searches(
        USER_ID,
        draft_search_id=DRAFT_ID,
        draft_expected_version=2,
        open_search_id=OPEN_ID,
        open_expected_version=7,
    )

    assert result.cancelled_count == 2
    assert result.draft_session.id == DRAFT_ID
    assert result.open_session.id == OPEN_ID
    assert db.table_names == []
    assert db.rpc_calls == [(
        'cancel_renter_searches',
        {
            'p_user_id': str(USER_ID),
            'p_draft_search_id': str(DRAFT_ID),
            'p_draft_expected_version': 2,
            'p_open_search_id': str(OPEN_ID),
            'p_open_expected_version': 7,
        },
    )]


@pytest.mark.parametrize(
    ('kwargs', 'message'),
    [
        ({}, 'Choose a search'),
        (
            {'draft_search_id': DRAFT_ID, 'draft_expected_version': 0},
            'positive draft search version',
        ),
        (
            {'open_search_id': OPEN_ID, 'open_expected_version': None},
            'positive active search version',
        ),
        (
            {'draft_expected_version': 1, 'open_search_id': OPEN_ID, 'open_expected_version': 1},
            'draft search ID',
        ),
        (
            {
                'draft_search_id': OPEN_ID,
                'draft_expected_version': 1,
                'open_search_id': OPEN_ID,
                'open_expected_version': 1,
            },
            'must be different',
        ),
    ],
)
def test_invalid_cancel_scope_fails_before_any_database_call(kwargs, message):
    db = MutationDatabase()
    service = RequirementService(db=db, llm=object())

    with pytest.raises(ValueError, match=message):
        service.cancel_renter_searches(USER_ID, **kwargs)

    assert db.rpc_calls == []
    assert db.table_names == []


def test_cancel_rpc_error_is_stable_and_hides_provider_details():
    db = MutationDatabase(errors={
        'cancel_renter_searches': RuntimeError(
            'P0001 STALE_OPEN_SEARCH_VERSION private provider detail'
        ),
    })
    service = RequirementService(db=db, llm=object())

    with pytest.raises(RuntimeError) as captured:
        service.cancel_renter_searches(
            USER_ID,
            open_search_id=OPEN_ID,
            open_expected_version=7,
        )

    assert 'active search changed' in str(captured.value)
    assert 'private provider detail' not in str(captured.value)


def test_plan_edit_sends_full_typed_snapshot_and_returns_existing_tuple_shape():
    current = requirement_row()
    persisted = requirement_row(
        preferred_locations=['Gachibowli', 'Madhapur'],
        raw_requirement_text='initial search\nUpdate: add Madhapur',
    )
    db = MutationDatabase(
        requirements=[current],
        responses={
            'update_live_renter_search': [{
                'session': session_row(
                    OPEN_ID, status=SearchStatus.ACTIVE.value, version=8
                ),
                'requirements': persisted,
                'job_enqueued': True,
            }],
        },
    )
    service = RequirementService(db=db, llm=object())
    plan = RequirementEditPlan(changes=[RequirementFieldChange(
        field='preferred_locations',
        operation=RequirementChangeOperation.ADD,
        value=['Madhapur'],
    )])

    version, updated = service.update_live_search_from_plan(
        USER_ID,
        OPEN_ID,
        plan,
        'add Madhapur',
        expected_version=7,
    )

    assert version == 8
    assert updated['preferred_locations'] == ['Gachibowli', 'Madhapur']
    assert db.table_names == ['search_requirements']
    name, params = db.rpc_calls[0]
    assert name == 'update_live_renter_search'
    assert params == {
        'p_listing_types': ['ENTIRE_PROPERTY'],
        'p_preferred_locations': ['Gachibowli', 'Madhapur'],
        'p_target_rent': 20_000,
        'p_max_rent': 25_000,
        'p_acceptable_locations': [],
        'p_excluded_locations': [],
        'p_work_location': None,
        'p_preferred_move_in_date': '2026-09-01',
        'p_latest_move_in_date': '2026-09-07',
        'p_preferred_property_configurations': ['2BHK'],
        'p_core_preferences': {},
        'p_additional_preferences': {},
        'p_raw_requirement_text': 'initial search\nUpdate: add Madhapur',
        'p_user_id': str(USER_ID),
        'p_search_id': str(OPEN_ID),
        'p_expected_version': 7,
    }


def test_private_to_entire_edit_requires_configuration_or_explicit_any():
    current = requirement_row(
        listing_types=['PRIVATE_ROOM'],
        preferred_property_configurations=[],
        additional_preferences={
            '__flathunter_configuration_answered': 'true',
        },
    )
    db = MutationDatabase(requirements=[current])
    service = RequirementService(db=db, llm=object())
    plan = RequirementEditPlan(changes=[RequirementFieldChange(
        field='listing_types',
        operation=RequirementChangeOperation.REPLACE,
        value=['ENTIRE_PROPERTY'],
    )])

    with pytest.raises(ValueError, match='configuration or explicit Any'):
        service.update_live_search_from_plan(
            USER_ID,
            OPEN_ID,
            plan,
            'change to entire property',
            expected_version=7,
        )

    assert db.rpc_calls == []


def test_live_edit_rejects_unknown_home_configuration_before_rpc():
    current = requirement_row()
    db = MutationDatabase(requirements=[current])
    service = RequirementService(db=db, llm=object())
    plan = RequirementEditPlan(changes=[RequirementFieldChange(
        field='preferred_property_configurations',
        operation=RequirementChangeOperation.REPLACE,
        value=['PENTHOUSE'],
    )])

    with pytest.raises(ValueError, match='not supported'):
        service.update_live_search_from_plan(
            USER_ID,
            OPEN_ID,
            plan,
            'make it a penthouse',
            expected_version=7,
        )

    assert db.rpc_calls == []


def test_entire_property_configuration_can_be_explicitly_replaced_with_any():
    current = requirement_row(
        listing_types=['ENTIRE_PROPERTY'],
        preferred_property_configurations=['2BHK'],
    )
    persisted = requirement_row(
        listing_types=['ENTIRE_PROPERTY'],
        preferred_property_configurations=[],
        additional_preferences={
            '__flathunter_configuration_answered': 'true',
        },
    )
    db = MutationDatabase(
        requirements=[current],
        responses={
            'update_live_renter_search': [{
                'session': session_row(
                    OPEN_ID,
                    status=SearchStatus.ACTIVE.value,
                    version=8,
                ),
                'requirements': persisted,
            }],
        },
    )
    service = RequirementService(db=db, llm=object())
    plan = RequirementEditPlan(changes=[RequirementFieldChange(
        field='preferred_property_configurations',
        operation=RequirementChangeOperation.REPLACE,
        value=[],
    )])

    version, updated = service.update_live_search_from_plan(
        USER_ID,
        OPEN_ID,
        plan,
        'any configuration works',
        expected_version=7,
    )

    assert version == 8
    assert updated['preferred_property_configurations'] == []
    assert updated['additional_preferences'][
        '__flathunter_configuration_answered'
    ] == 'true'
    assert db.rpc_calls[0][1]['p_additional_preferences'][
        '__flathunter_configuration_answered'
    ] == 'true'


def test_resume_transition_uses_new_version_and_requires_catch_up_job():
    db = MutationDatabase(responses={
        'set_renter_search_paused': [{
            'session': session_row(
                OPEN_ID,
                status=SearchStatus.ACTIVE.value,
                version=8,
            ),
            'changed': True,
            'job_enqueued': True,
        }],
    })
    service = RequirementService(db=db, llm=object())

    result = service.set_renter_search_paused(
        USER_ID,
        OPEN_ID,
        expected_version=7,
        paused=False,
    )

    assert result.session.version == 8
    assert result.job_enqueued is True
    assert db.rpc_calls == [(
        'set_renter_search_paused',
        {
            'p_user_id': str(USER_ID),
            'p_search_id': str(OPEN_ID),
            'p_expected_version': 7,
            'p_paused': False,
        },
    )]


def test_resume_transition_rejects_missing_catch_up_job():
    db = MutationDatabase(responses={
        'set_renter_search_paused': [{
            'session': session_row(
                OPEN_ID,
                status=SearchStatus.ACTIVE.value,
                version=8,
            ),
            'changed': True,
            'job_enqueued': False,
        }],
    })
    service = RequirementService(db=db, llm=object())

    with pytest.raises(RuntimeError, match='catch-up matching'):
        service.set_renter_search_paused(
            USER_ID,
            OPEN_ID,
            expected_version=7,
            paused=False,
        )


def test_patch_edit_keeps_integer_return_contract():
    persisted = requirement_row(max_rent=30_000)
    db = MutationDatabase(
        requirements=[requirement_row()],
        responses={
            'update_live_renter_search': [{
                'session': session_row(
                    OPEN_ID, status=SearchStatus.PAUSED.value, version=8
                ),
                'requirements': persisted,
                'job_enqueued': True,
            }],
        },
    )
    service = RequirementService(db=db, llm=object())

    version = service.update_live_search(
        USER_ID,
        OPEN_ID,
        RequirementEditResponse(max_rent=30_000),
        'raise budget to 30k',
        expected_version=7,
    )

    assert version == 8


def test_invalid_live_edit_fails_validation_before_rpc():
    db = MutationDatabase(requirements=[requirement_row()])
    service = RequirementService(db=db, llm=object())
    plan = RequirementEditPlan(changes=[RequirementFieldChange(
        field='max_rent',
        operation=RequirementChangeOperation.SET,
        value=15_000,
    )])

    with pytest.raises(ValueError, match='no higher than'):
        service.update_live_search_from_plan(
            USER_ID,
            OPEN_ID,
            plan,
            'lower maximum to 15k',
            expected_version=7,
        )

    assert db.rpc_calls == []


def test_expected_version_is_required_before_loading_requirements():
    db = MutationDatabase(requirements=[requirement_row()])
    service = RequirementService(db=db, llm=object())

    with pytest.raises(ValueError, match='positive expected search version'):
        service.update_live_search(
            USER_ID,
            OPEN_ID,
            RequirementEditResponse(max_rent=30_000),
            'raise budget',
            expected_version=0,
        )

    assert db.table_names == []
    assert db.rpc_calls == []


def test_stale_live_edit_error_is_stable_and_no_job_is_written_by_service():
    db = MutationDatabase(
        requirements=[requirement_row()],
        errors={
            'update_live_renter_search': RuntimeError(
                'P0001 STALE_SEARCH_VERSION internal transaction detail'
            ),
        },
    )
    service = RequirementService(db=db, llm=object())

    with pytest.raises(RuntimeError) as captured:
        service.update_live_search(
            USER_ID,
            OPEN_ID,
            RequirementEditResponse(max_rent=30_000),
            'raise budget',
            expected_version=7,
        )

    assert 'changed elsewhere' in str(captured.value)
    assert 'internal transaction detail' not in str(captured.value)
    assert db.table_names == ['search_requirements']


def test_migration_defines_service_only_atomic_cancel_and_live_update():
    sql = Path('migrations/004_atomic_renter_search_lifecycle.sql').read_text(
        encoding='utf-8'
    )

    cancel_start = sql.index(
        'CREATE OR REPLACE FUNCTION public.cancel_renter_searches'
    )
    update_start = sql.index(
        'CREATE OR REPLACE FUNCTION public.update_live_renter_search'
    )
    pause_start = sql.index(
        'CREATE OR REPLACE FUNCTION public.set_renter_search_paused'
    )
    claim_start = sql.index(
        'CREATE OR REPLACE FUNCTION public.claim_next_agent_job'
    )
    cancel_sql = sql[cancel_start:pause_start]
    pause_sql = sql[pause_start:update_start]
    update_sql = sql[update_start:claim_start]

    assert 'SECURITY INVOKER' in cancel_sql
    assert 'SECURITY INVOKER' in pause_sql
    assert 'SECURITY INVOKER' in update_sql
    assert 'FROM public.search_sessions' in cancel_sql
    assert 'FROM public.search_sessions' in update_sql
    assert cancel_sql.index('STALE_OPEN_SEARCH_VERSION') < cancel_sql.index(
        'UPDATE public.search_sessions'
    )
    assert cancel_sql.count('UPDATE public.search_sessions') == 2
    assert "'RESUMED'" in pause_sql
    assert "'MATCH_ACTIVE_SEARCH:' || p_search_id::TEXT || ':' || v_session.version::TEXT" in pause_sql
    assert 'PERFORM public.validate_renter_search_requirements' in update_sql
    assert update_sql.index(
        'PERFORM public.validate_renter_search_requirements'
    ) < update_sql.index('UPDATE public.search_requirements')
    assert 'SET version = version + 1' in update_sql
    assert "'SEARCH_UPDATED:' || p_search_id::TEXT" in update_sql
    assert 'ON CONFLICT (idempotency_key) DO NOTHING' in update_sql
    assert 'REVOKE ALL PRIVILEGES ON FUNCTION public.cancel_renter_searches' in sql
    assert 'GRANT EXECUTE ON FUNCTION public.cancel_renter_searches' in sql
    assert 'REVOKE ALL PRIVILEGES ON FUNCTION public.update_live_renter_search' in sql
    assert 'GRANT EXECUTE ON FUNCTION public.update_live_renter_search' in sql
    assert 'FROM PUBLIC, anon, authenticated' in sql
    assert ') TO service_role;' in sql
