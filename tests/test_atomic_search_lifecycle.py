from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.common.enums import SearchStatus
from app.requirements.schemas import RequirementExtractionResponse
from app.requirements.service import (
    CreationKeyPayloadMismatch,
    RequirementService,
)


USER_ID = UUID('22222222-2222-2222-2222-222222222222')
SEARCH_ID = UUID('11111111-1111-1111-1111-111111111111')
REPLACED_SEARCH_ID = UUID('33333333-3333-3333-3333-333333333333')
CREATION_KEY = UUID('44444444-4444-4444-4444-444444444444')


def session_row(**overrides):
    row = {
        'id': str(SEARCH_ID),
        'user_id': str(USER_ID),
        'status': 'DRAFT',
        'version': 1,
        'city': 'Hyderabad',
        'created_at': '2026-08-09T10:00:00+00:00',
        'updated_at': '2026-08-09T10:00:00+00:00',
        'started_at': None,
        'last_activated_at': None,
        'paused_at': None,
        'closed_at': None,
    }
    row.update(overrides)
    return row


def requirements(**overrides):
    value = {
        'is_complete': True,
        'listing_types': ['PRIVATE_ROOM'],
        'preferred_locations': [' Gachibowli '],
        'acceptable_locations': ['Madhapur'],
        'excluded_locations': [],
        'work_location': ' Financial District ',
        'target_rent': 20_000,
        'max_rent': 25_000,
        'preferred_move_in_date': '2026-09-01',
        'latest_move_in_date': '2026-09-07',
        'preferred_property_configurations': ['2BHK'],
        'core_preferences': {
            'parking': {'value': True, 'importance': 'PREFERRED'},
        },
        'additional_preferences': {'near_metro': 'yes'},
    }
    value.update(overrides)
    return RequirementExtractionResponse(**value)


class RpcExecution:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error

    def execute(self):
        if self.error:
            raise self.error
        return SimpleNamespace(data=self.data)


class LifecycleDatabase:
    def __init__(self, responses=None, errors=None):
        self.responses = responses or {}
        self.errors = errors or {}
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return RpcExecution(self.responses.get(name), self.errors.get(name))


def test_create_draft_rpc_uses_typed_normalized_payload_and_creation_key():
    db = LifecycleDatabase(responses={
        'create_renter_search_draft': [{
            'session': session_row(),
            'created': True,
        }],
    })
    service = RequirementService(db=db, llm=object())

    result = service.create_renter_search_draft(
        USER_ID,
        requirements(),
        'private room in Gachibowli',
        creation_key=CREATION_KEY,
    )

    assert result.created is True
    assert result.session.id == SEARCH_ID
    assert db.calls[0] == (
        'get_renter_search_draft_by_creation_key',
        {
            'p_user_id': str(USER_ID),
            'p_creation_key': str(CREATION_KEY),
        },
    )
    name, params = db.calls[1]
    assert name == 'create_renter_search_draft'
    assert params == {
        'p_listing_types': ['PRIVATE_ROOM'],
        'p_preferred_locations': ['Gachibowli'],
        'p_target_rent': 20_000,
        'p_max_rent': 25_000,
        'p_acceptable_locations': ['Madhapur'],
        'p_excluded_locations': [],
        'p_work_location': 'Financial District',
        'p_preferred_move_in_date': '2026-09-01',
        'p_latest_move_in_date': '2026-09-07',
        'p_preferred_property_configurations': ['2BHK'],
        'p_core_preferences': {
            'parking': {'value': True, 'importance': 'PREFERRED'},
        },
        'p_additional_preferences': {'near_metro': 'yes'},
        'p_raw_requirement_text': 'private room in Gachibowli',
        'p_creation_key': str(CREATION_KEY),
        'p_user_id': str(USER_ID),
        'p_city': 'Hyderabad',
    }


def test_exact_creation_key_replay_is_read_only():
    persisted = {
        'listing_types': ['PRIVATE_ROOM'],
        'preferred_locations': ['Gachibowli'],
        'acceptable_locations': ['Madhapur'],
        'excluded_locations': [],
        'work_location': 'Financial District',
        'target_rent': 20_000,
        'max_rent': 25_000,
        'preferred_move_in_date': '2026-09-01',
        'latest_move_in_date': '2026-09-07',
        'preferred_property_configurations': ['2BHK'],
        'core_preferences': {
            'parking': {'value': True, 'importance': 'PREFERRED'},
        },
        'additional_preferences': {'near_metro': 'yes'},
        'raw_requirement_text': 'private room in Gachibowli',
    }
    db = LifecycleDatabase(responses={
        'get_renter_search_draft_by_creation_key': [{
            'session': session_row(version=4),
            'requirements': persisted,
        }],
    })
    service = RequirementService(db=db, llm=object())

    result = service.create_renter_search_draft(
        USER_ID,
        requirements(),
        'private room in Gachibowli',
        creation_key=CREATION_KEY,
    )

    assert result.created is False
    assert result.session.version == 4
    assert [name for name, _ in db.calls] == [
        'get_renter_search_draft_by_creation_key',
    ]


def test_changed_creation_key_replay_never_mutates_existing_draft():
    db = LifecycleDatabase(responses={
        'get_renter_search_draft_by_creation_key': [{
            'session': session_row(version=4),
            'requirements': {
                'listing_types': ['PRIVATE_ROOM'],
                'preferred_locations': ['Older Area'],
            },
        }],
    })
    service = RequirementService(db=db, llm=object())

    with pytest.raises(CreationKeyPayloadMismatch):
        service.create_renter_search_draft(
            USER_ID,
            requirements(),
            'newer payload',
            creation_key=CREATION_KEY,
        )

    assert [name for name, _ in db.calls] == [
        'get_renter_search_draft_by_creation_key',
    ]


def test_legacy_create_wrapper_keeps_session_return_contract():
    db = LifecycleDatabase(responses={
        'create_renter_search_draft': [{
            'session': session_row(),
            'created': False,
        }],
    })
    service = RequirementService(db=db, llm=object())

    session = service.create_draft_search(
        USER_ID,
        requirements(),
        'retry',
        creation_key=CREATION_KEY,
    )

    assert session.id == SEARCH_ID
    assert db.calls[0][1]['p_creation_key'] == str(CREATION_KEY)


def test_relative_date_is_rejected_before_any_database_call():
    db = LifecycleDatabase()
    service = RequirementService(db=db, llm=object())

    with pytest.raises(ValueError, match='exact date in YYYY-MM-DD'):
        service.create_renter_search_draft(
            USER_ID,
            requirements(preferred_move_in_date='first week of next month'),
            'relative date',
            creation_key=CREATION_KEY,
        )

    assert db.calls == []


def test_invalid_rent_window_is_rejected_before_any_database_call():
    db = LifecycleDatabase()
    service = RequirementService(db=db, llm=object())

    with pytest.raises(ValueError, match='no higher than'):
        service.create_renter_search_draft(
            USER_ID,
            requirements(target_rent=30_000, max_rent=25_000),
            'invalid rent',
            creation_key=CREATION_KEY,
        )

    assert db.calls == []


def test_update_draft_rpc_carries_owner_and_expected_version():
    db = LifecycleDatabase(responses={
        'update_renter_search_draft': [{
            'session': session_row(
                version=2,
                updated_at='2026-08-09T10:05:00+00:00',
            ),
            'updated': True,
        }],
    })
    service = RequirementService(db=db, llm=object())

    updated = service.update_renter_search_draft(
        USER_ID,
        SEARCH_ID,
        requirements(),
        'add parking',
        expected_version=1,
    )

    assert updated.id == SEARCH_ID
    assert updated.version == 2
    name, params = db.calls[0]
    assert name == 'update_renter_search_draft'
    assert params['p_user_id'] == str(USER_ID)
    assert params['p_search_id'] == str(SEARCH_ID)
    assert params['p_expected_version'] == 1


def test_activation_rpc_carries_atomic_replacement_and_returns_flags():
    active = session_row(
        status='ACTIVE',
        started_at='2026-08-09T10:10:00+00:00',
        last_activated_at='2026-08-09T10:10:00+00:00',
    )
    db = LifecycleDatabase(responses={
        'activate_renter_search': [{
            'session': active,
            'activated': True,
            'job_enqueued': True,
            'replaced_search_id': str(REPLACED_SEARCH_ID),
        }],
    })
    service = RequirementService(db=db, llm=object())

    result = service.activate_renter_search(
        USER_ID,
        SEARCH_ID,
        expected_version=1,
        replace_search_id=REPLACED_SEARCH_ID,
        replace_expected_version=4,
    )

    assert result.session.status == SearchStatus.ACTIVE
    assert result.activated is True
    assert result.job_enqueued is True
    assert result.replaced_search_id == REPLACED_SEARCH_ID
    assert db.calls == [(
        'activate_renter_search',
        {
            'p_user_id': str(USER_ID),
            'p_search_id': str(SEARCH_ID),
            'p_expected_version': 1,
            'p_replace_search_id': str(REPLACED_SEARCH_ID),
            'p_replace_expected_version': 4,
        },
    )]


def test_legacy_activation_wrapper_returns_search_session():
    db = LifecycleDatabase(responses={
        'activate_renter_search': [{
            'session': session_row(status='ACTIVE'),
            'activated': False,
            'job_enqueued': False,
            'replaced_search_id': None,
        }],
    })
    service = RequirementService(db=db, llm=object())

    session = service.activate_search(USER_ID, SEARCH_ID, expected_version=1)

    assert session.status == SearchStatus.ACTIVE


def test_stable_rpc_error_is_mapped_without_exposing_provider_details():
    db = LifecycleDatabase(errors={
        'activate_renter_search': RuntimeError(
            'P0001 STALE_SEARCH_VERSION internal database detail'
        ),
    })
    service = RequirementService(db=db, llm=object())

    with pytest.raises(RuntimeError) as captured:
        service.activate_renter_search(
            USER_ID,
            SEARCH_ID,
            expected_version=1,
        )

    assert 'changed elsewhere' in str(captured.value)
    assert 'internal database detail' not in str(captured.value)


class TableQuery:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.filters = []
        self.statuses = None
        self.payload = None
        self.operation = 'select'
        self.limit_count = None

    def select(self, *_args):
        return self

    def update(self, payload):
        self.operation = 'update'
        self.payload = payload
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def in_(self, field, values):
        assert field == 'status'
        self.statuses = set(values)
        return self

    def order(self, field, desc=False):
        assert field == 'created_at'
        self.db.order_desc = desc
        return self

    def limit(self, count):
        self.limit_count = count
        return self

    def execute(self):
        rows = [
            row for row in self.db.rows[self.table]
            if all(str(row.get(field)) == str(value) for field, value in self.filters)
        ]
        if self.statuses is not None:
            rows = [row for row in rows if row.get('status') in self.statuses]
        if self.operation == 'update':
            for row in rows:
                row.update(self.payload)
            self.db.last_update = {
                'table': self.table,
                'filters': list(self.filters),
                'statuses': set(self.statuses or []),
                'payload': dict(self.payload),
            }
        elif self.db.order_desc:
            rows.sort(key=lambda row: row.get('created_at') or '', reverse=True)
        if self.limit_count is not None:
            rows = rows[:self.limit_count]
        return SimpleNamespace(data=[dict(row) for row in rows])


class TableDatabase:
    def __init__(self, sessions, search_requirements):
        self.rows = {
            'search_sessions': sessions,
            'search_requirements': search_requirements,
        }
        self.order_desc = False
        self.last_update = None

    def table(self, name):
        return TableQuery(self, name)


def test_current_search_resolver_excludes_newer_draft_and_aliases_editable():
    paused = session_row(
        id=str(REPLACED_SEARCH_ID),
        status='PAUSED',
        created_at='2026-08-09T10:00:00+00:00',
    )
    draft = session_row(created_at='2026-08-09T11:00:00+00:00')
    requirement_row = {'search_id': str(REPLACED_SEARCH_ID), 'max_rent': 25_000}
    db = TableDatabase([paused, draft], [requirement_row])
    service = RequirementService(db=db, llm=object())

    current, current_requirements = service.get_current_search(USER_ID)
    editable, editable_requirements = service.get_editable_search(USER_ID)

    assert current['id'] == str(REPLACED_SEARCH_ID)
    assert editable['id'] == str(REPLACED_SEARCH_ID)
    assert current_requirements == requirement_row
    assert editable_requirements == requirement_row


def test_owned_draft_resolver_returns_latest_durable_requirements():
    older = session_row(created_at='2026-08-09T09:00:00+00:00')
    latest = session_row(
        id=str(REPLACED_SEARCH_ID),
        created_at='2026-08-09T12:00:00+00:00',
    )
    requirement_row = {
        'search_id': str(REPLACED_SEARCH_ID),
        'max_rent': 25_000,
    }
    db = TableDatabase([older, latest], [requirement_row])
    service = RequirementService(db=db, llm=object())

    recovered = service.get_owned_search_draft(USER_ID)

    assert recovered is not None
    assert recovered.session.id == REPLACED_SEARCH_ID
    assert recovered.requirements == requirement_row


def test_close_owned_search_uses_owner_version_and_explicit_allowed_status():
    active = session_row(status='ACTIVE', version=3)
    db = TableDatabase([active], [])
    service = RequirementService(db=db, llm=object())

    closed = service.close_owned_search(
        USER_ID,
        SEARCH_ID,
        expected_version=3,
        allowed_statuses={SearchStatus.ACTIVE},
    )

    assert closed.status == SearchStatus.CLOSED
    assert db.last_update['filters'] == [
        ('id', str(SEARCH_ID)),
        ('user_id', str(USER_ID)),
        ('version', 3),
    ]
    assert db.last_update['statuses'] == {'ACTIVE'}
    assert db.last_update['payload']['status'] == 'CLOSED'
    assert db.last_update['payload']['closed_at']


def test_close_owned_search_rejects_closed_as_a_source_status():
    service = RequirementService(db=object(), llm=object())

    with pytest.raises(ValueError, match='CLOSED cannot'):
        service.close_owned_search(
            USER_ID,
            SEARCH_ID,
            expected_version=1,
            allowed_statuses={SearchStatus.CLOSED},
        )


def test_atomic_lifecycle_migration_contains_required_guards_and_recovery():
    sql = Path('migrations/004_atomic_renter_search_lifecycle.sql').read_text(
        encoding='utf-8'
    )

    assert 'uq_search_sessions_creation_key' in sql
    assert 'uq_search_sessions_one_open_per_user' in sql
    assert 'OPEN_SEARCH_PRECHECK_FAILED' in sql
    assert 'CREATE OR REPLACE FUNCTION public.create_renter_search_draft' in sql
    assert 'CREATE OR REPLACE FUNCTION public.update_renter_search_draft' in sql
    assert 'CREATE OR REPLACE FUNCTION public.activate_renter_search' in sql
    assert 'ON CONFLICT (idempotency_key) DO NOTHING' in sql
    assert 'MATCH_ACTIVE_SEARCH:' in sql
    assert "locked_at < now() - interval '10 minutes'" in sql
    assert 'FOR UPDATE SKIP LOCKED' in sql
    assert "MESSAGE = 'PAUSED_FLAG_REQUIRED'" in sql
    assert 'configuration.value IS NULL' in sql
    assert 'FROM PUBLIC, anon, authenticated' in sql
    assert 'TO service_role' in sql
    assert 'SET version = version + 1' in sql


def test_atomic_lifecycle_migration_rejects_wrong_same_name_indexes():
    sql = Path('migrations/004_atomic_renter_search_lifecycle.sql').read_text(
        encoding='utf-8'
    )
    assertion_start = sql.index(
        '-- CREATE INDEX IF NOT EXISTS checks only the relation name.'
    )
    assertion_end = sql.index(
        'CREATE OR REPLACE FUNCTION public.validate_renter_search_requirements'
    )
    assertion_sql = sql[assertion_start:assertion_end]

    assert "MESSAGE = 'SEARCH_INDEX_DEFINITION_MISMATCH'" in assertion_sql
    assert "'public.search_sessions'::regclass" in assertion_sql
    assert 'IS DISTINCT FROM TRUE' in assertion_sql
    assert 'IS DISTINCT FROM FALSE' in assertion_sql
    assert "IS DISTINCT FROM 'btree'" in assertion_sql
    assert 'key_count IS DISTINCT FROM 1' in assertion_sql
    assert 'total_attribute_count IS DISTINCT FROM 1' in assertion_sql
    assert 'has_no_expressions IS DISTINCT FROM TRUE' in assertion_sql
    assert "ARRAY['creation_key']::TEXT[]" in assertion_sql
    assert "ARRAY['user_id']::TEXT[]" in assertion_sql
    assert assertion_sql.count('SELECT attribute.attname::TEXT') == 2
    assert "IS DISTINCT FROM 'creation_keyisnotnull'" in assertion_sql
    assert (
        "'user_idisnotnullandstatus=anyarray['" in assertion_sql
    )
    assert "'[[:space:]()\"]+'" in assertion_sql


def test_atomic_lifecycle_runbook_checks_exact_index_definitions():
    runbook = Path(
        'migrations/004_atomic_renter_search_lifecycle_RUNBOOK.md'
    ).read_text(encoding='utf-8')

    assert 'has_exact_single_plain_key' in runbook
    assert 'predicate_matches_exactly' in runbook
    assert 'actual.indnatts = 1' in runbook
    assert 'actual.has_no_expressions' in runbook
    assert "actual.access_method = 'btree'" in runbook
    assert 'actual.canonical_predicate = expected.canonical_predicate' in runbook
    assert 'SELECT attribute.attname::text' in runbook
    assert 'required_predicate_terms' not in runbook
    assert 'predicate ILIKE' not in runbook
    assert 'SEARCH_INDEX_DEFINITION_MISMATCH' in runbook
