from app.requirements.schemas import RequirementEditResponse
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
