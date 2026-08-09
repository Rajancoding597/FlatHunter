from app.requirements.schemas import RequirementExtractionResponse
from app.requirements.service import RequirementService


def requirements(**overrides):
    value = {
        "is_complete": True,
        "listing_types": ["PRIVATE_ROOM"],
        "preferred_locations": ["Gachibowli"],
        "acceptable_locations": [],
        "target_rent": 20_000,
        "max_rent": 23_000,
        "preferred_move_in_date": "2026-09-01",
        "latest_move_in_date": None,
    }
    value.update(overrides)
    return RequirementExtractionResponse(**value)


def test_core_search_requirements_are_complete():
    assert RequirementService.missing_core_requirements(requirements()) == []


def test_move_in_timing_is_required_before_search_activation():
    missing = RequirementService.missing_core_requirements(requirements(preferred_move_in_date=None))

    assert missing == ["move-in timing"]


def test_unlimited_budget_is_not_invented_for_incomplete_profile():
    missing = RequirementService.missing_core_requirements(requirements(max_rent=None, target_rent=None))

    assert missing == ["maximum budget"]