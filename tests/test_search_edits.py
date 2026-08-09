from app.requirements.schemas import RequirementEditResponse
from uuid import UUID

import pytest

from app.requirements.schemas import (
    RequirementChangeOperation,
    RequirementEditPlan,
    RequirementFieldChange,
)
from app.requirements.service import RequirementService


def test_search_edit_preserves_unmentioned_requirements():
    current = {
        "listing_types": ["PRIVATE_ROOM"],
        "preferred_locations": ["Gachibowli"],
        "acceptable_locations": [],
        "max_rent": 23_000,
        "target_rent": 20_000,
        "preferred_move_in_date": "2026-09-01",
        "core_preferences": {"furnished": {"value": True, "importance": "PREFERRED"}},
    }

    merged = RequirementService.merge_live_requirements(
        current,
        RequirementEditResponse(max_rent=25_000, preferred_locations=["Gachibowli", "Madhapur"]),
    )

    assert merged["max_rent"] == 25_000
    assert merged["preferred_locations"] == ["Gachibowli", "Madhapur"]
    assert merged["listing_types"] == ["PRIVATE_ROOM"]
    assert merged["preferred_move_in_date"] == "2026-09-01"
    assert merged["core_preferences"]["furnished"]["value"] is True


def test_search_edit_merges_preference_without_dropping_existing_ones():
    current = {"core_preferences": {"furnished": {"value": True, "importance": "PREFERRED"}}}

    merged = RequirementService.merge_live_requirements(
        current,
        RequirementEditResponse(core_preferences={"parking": {"value": True, "importance": "PREFERRED"}}),
    )

    assert set(merged["core_preferences"]) == {"furnished", "parking"}


class Result:
    def __init__(self, data):
        self.data = data


class PlanQuery:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.filters = []
        self.payload = None
        self.operation = 'select'

    def select(self, fields):
        return self

    def update(self, payload):
        self.operation = 'update'
        self.payload = payload
        return self

    def insert(self, payload):
        self.operation = 'insert'
        self.payload = payload
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def execute(self):
        rows = self.db.rows[self.table]
        matching = [row for row in rows if all(str(row.get(field)) == str(value) for field, value in self.filters)]
        if self.operation == 'select':
            return Result([dict(row) for row in matching])
        if self.operation == 'insert':
            rows.append(dict(self.payload))
            return Result([dict(self.payload)])
        for row in matching:
            row.update(self.payload)
        return Result([dict(row) for row in matching])


class PlanDatabase:
    def __init__(self):
        self.rows = {
            'search_sessions': [{
                'id': '11111111-1111-1111-1111-111111111111',
                'user_id': '22222222-2222-2222-2222-222222222222',
                'status': 'ACTIVE',
                'version': 1,
            }],
            'search_requirements': [{
                'search_id': '11111111-1111-1111-1111-111111111111',
                'listing_types': ['PRIVATE_ROOM'],
                'preferred_locations': ['Gachibowli'],
                'acceptable_locations': [],
                'excluded_locations': [],
                'target_rent': 20_000,
                'max_rent': 23_000,
                'preferred_move_in_date': '2026-09-01',
                'latest_move_in_date': None,
                'core_preferences': {},
                'additional_preferences': {},
            }],
            'agent_jobs': [],
        }

    def table(self, name):
        return PlanQuery(self, name)


def test_operation_plan_persists_once_and_rejects_stale_confirmation():
    db = PlanDatabase()
    service = RequirementService(db=db, llm=object())
    plan = RequirementEditPlan(changes=[RequirementFieldChange(
        field='preferred_locations',
        operation=RequirementChangeOperation.ADD,
        value=['Madhapur'],
    )])
    user_id = UUID('22222222-2222-2222-2222-222222222222')
    search_id = UUID('11111111-1111-1111-1111-111111111111')

    version, updated = service.update_live_search_from_plan(
        user_id,
        search_id,
        plan,
        'add Madhapur',
        expected_version=1,
    )

    assert version == 2
    assert updated['preferred_locations'] == ['Gachibowli', 'Madhapur']
    assert db.rows['search_requirements'][0]['preferred_locations'] == ['Gachibowli', 'Madhapur']
    assert db.rows['agent_jobs'][0]['idempotency_key'].endswith(':2')

    with pytest.raises(RuntimeError, match='changed elsewhere'):
        service.update_live_search_from_plan(
            user_id,
            search_id,
            plan,
            'add Madhapur',
            expected_version=1,
        )
