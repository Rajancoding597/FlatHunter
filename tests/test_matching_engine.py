from app.common.enums import MatchStatus
from app.matching.engine import MatchingEngine


def search_requirements(**overrides):
    requirements = {
        "listing_types": ["PRIVATE_ROOM"],
        "preferred_locations": ["Gachibowli"],
        "acceptable_locations": ["Madhapur"],
        "excluded_locations": ["Kondapur"],
        "target_rent": 20_000,
        "max_rent": 23_000,
        "preferred_move_in_date": "2026-09-01",
        "latest_move_in_date": "2026-09-07",
        "core_preferences": {
            "attached_bathroom": {"value": True, "importance": "REQUIRED"},
            "car_parking": {"value": True, "importance": "PREFERRED"},
        },
    }
    requirements.update(overrides)
    return requirements


def listing(**overrides):
    candidate = {
        "listing_type": "PRIVATE_ROOM",
        "city": "Hyderabad",
        "locality": "Gachibowli",
        "rent": 21_000,
        "maintenance": None,
        "availability_status": "AVAILABLE",
        "available_from": "2026-09-01",
        "attached_bathroom": True,
        "car_parking": True,
    }
    candidate.update(overrides)
    return candidate


def evaluate(requirements=None, candidate=None):
    return MatchingEngine(db=object()).evaluate_match(requirements or search_requirements(), candidate or listing())


def test_unknown_required_preference_needs_qualification_not_rejection():
    evaluation = evaluate(candidate=listing(attached_bathroom=None, availability_status="UNKNOWN"))

    assert evaluation.status is MatchStatus.NEEDS_QUALIFICATION
    assert evaluation.hard_rejection_reasons == []
    assert {gap["field"] for gap in evaluation.missing_information} >= {"attached_bathroom", "availability_status"}


def test_explicit_required_preference_mismatch_is_rejected():
    evaluation = evaluate(candidate=listing(attached_bathroom=False))

    assert evaluation.status is MatchStatus.REJECTED
    assert any(reason["code"] == "REQUIRED_PREFERENCE_MISMATCH" for reason in evaluation.hard_rejection_reasons)


def test_known_mandatory_maintenance_can_reject_budget():
    evaluation = evaluate(candidate=listing(rent=22_500, maintenance=1_000, maintenance_mandatory=True))

    assert evaluation.status is MatchStatus.REJECTED
    assert any(reason["code"] == "MAX_MONTHLY_BUDGET_EXCEEDED" for reason in evaluation.hard_rejection_reasons)


def test_unknown_maintenance_is_not_assumed_to_be_zero():
    evaluation = evaluate(candidate=listing(rent=22_500, maintenance=None))

    assert evaluation.status is MatchStatus.NEEDS_QUALIFICATION
    assert any(gap["field"] == "maintenance" for gap in evaluation.missing_information)


def test_acceptable_location_scores_below_preferred_location():
    preferred = evaluate()
    acceptable = evaluate(candidate=listing(locality="Madhapur"))

    assert preferred.fit_score > acceptable.fit_score
    assert acceptable.status is MatchStatus.POSSIBLE_MATCH


def test_excluded_locality_is_rejected():
    evaluation = evaluate(candidate=listing(locality="Kondapur"))

    assert evaluation.status is MatchStatus.REJECTED
    assert any(reason["code"] == "EXCLUDED_LOCALITY" for reason in evaluation.hard_rejection_reasons)