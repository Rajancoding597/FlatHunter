from app.jobs.worker import JobWorker

from app.jobs.worker import _match_action_keyboard


def test_ranked_card_includes_explanation_and_qualification_gaps():
    card = JobWorker.build_match_card(
        1,
        {
            "fit_score": 91.4,
            "status": "NEEDS_QUALIFICATION",
            "positive_reasons": ["Preferred location", "Within target budget"],
            "missing_information": [{"field": "maintenance"}],
        },
        {"property_configuration": "2BHK", "locality": "Madhapur", "rent": 24_000},
    )

    assert "#1 · 91% fit" in card
    assert "2BHK in Madhapur" in card
    assert "₹24,000" in card
    assert "Preferred location" in card
    assert "I will confirm: maintenance" in card


def test_ranked_card_has_a_safe_fallback_explanation():
    card = JobWorker.build_match_card(2, {"fit_score": 70, "status": "STRONG_MATCH"}, {})

    assert "Matches your saved search" in card
    assert "Property in Location to confirm" in card


def test_match_actions_fit_telegram_callback_limit_and_use_only_match_id():
    match_id = "a30559d3-defd-4088-b78d-109368a0caf3"
    keyboard = _match_action_keyboard(match_id)
    callback_data = [button.callback_data for row in keyboard.inline_keyboard for button in row]

    assert callback_data == [
        f"details_match_{match_id}",
        f"contact_match_{match_id}",
        f"skip_match_{match_id}",
    ]
    assert all(len(value.encode("utf-8")) <= 64 for value in callback_data)


def test_match_card_shows_available_property_details_and_contact_consent():
    card = JobWorker.build_match_card(
        1,
        {"fit_score": 95, "status": "STRONG_MATCH", "positive_reasons": ["Within budget"]},
        {
            "property_configuration": "2BHK",
            "locality": "Manikonda",
            "rent": 37_000,
            "maintenance": 3_000,
            "deposit": 74_000,
            "furnishing": "SEMI_FURNISHED",
            "attached_bathroom": True,
            "car_parking": True,
        },
    )

    assert "Rent: ₹37,000" in card
    assert "Maintenance: ₹3,000" in card
    assert "Deposit: ₹74,000" in card
    assert "Furnishing: Semi Furnished" in card
    assert "Attached bathroom: Yes" in card
    assert "Parking: Car" in card
    assert "contact the owner or agent" in card
