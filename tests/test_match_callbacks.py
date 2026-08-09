import pytest

from app.telegram import renter_handlers


MATCH_ID = "a30559d3-defd-4088-b78d-109368a0caf3"


def test_match_callback_parser_accepts_compact_uuid_and_rejects_invalid_data():
    assert renter_handlers._match_id_from_callback(f"contact_match_{MATCH_ID}", "contact") == MATCH_ID
    assert renter_handlers._match_id_from_callback(f"skip_match_{MATCH_ID}", "skip") == MATCH_ID
    assert renter_handlers._match_id_from_callback(f"details_match_{MATCH_ID}", "details") == MATCH_ID
    assert renter_handlers._match_id_from_callback("contact_match_not-a-uuid", "contact") is None
    assert renter_handlers._match_id_from_callback(f"skip_match_{MATCH_ID}", "contact") is None


class MatchQuery:
    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def execute(self):
        return type("Result", (), {"data": [{
            "id": MATCH_ID,
            "search_id": "search-1",
            "listing_id": "listing-1",
        }]})()


class MatchDatabase:
    def table(self, name):
        assert name == "matches"
        return MatchQuery()


@pytest.mark.asyncio
async def test_match_callback_resolves_match_then_checks_search_ownership(monkeypatch):
    ownership_checks = []

    async def fake_owns_search(callback, search_id):
        ownership_checks.append((callback, search_id))
        return True

    callback = object()
    monkeypatch.setattr(renter_handlers, "get_supabase_client", lambda: MatchDatabase())
    monkeypatch.setattr(renter_handlers, "_callback_owns_search", fake_owns_search)

    match = await renter_handlers._callback_owned_match(callback, MATCH_ID)

    assert match == {"id": MATCH_ID, "search_id": "search-1", "listing_id": "listing-1"}
    assert ownership_checks == [(callback, "search-1")]


class DetailsMessage:
    def __init__(self):
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


class DetailsCallback:
    def __init__(self):
        self.data = f"details_match_{MATCH_ID}"
        self.message = DetailsMessage()
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))


class ListingQuery:
    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def execute(self):
        return type("Result", (), {"data": [{"locality": "Kondapur", "rent": 15_833}]})()


class ListingDatabase:
    def table(self, name):
        assert name == "listings"
        return ListingQuery()


@pytest.mark.asyncio
async def test_property_details_show_narrative_and_missing_owner_clarifications(monkeypatch):
    from app.matching import details

    async def fake_owned_match(_callback, _match_id):
        return {
            "id": MATCH_ID,
            "search_id": "search-1",
            "listing_id": "listing-1",
            "missing_information": [
                {"field": "availability_status", "priority": 1},
                {"field": "maintenance", "priority": 3},
            ],
        }

    async def fake_narrative(_listing):
        return "A private room in Kondapur for ₹15,833 per month."

    callback = DetailsCallback()
    monkeypatch.setattr(renter_handlers, "_callback_owned_match", fake_owned_match)
    monkeypatch.setattr(renter_handlers, "get_supabase_client", lambda: ListingDatabase())
    monkeypatch.setattr(details, "draft_property_narrative", fake_narrative)

    await renter_handlers.process_property_details_callback(callback)

    response = "\n".join(text for text, _kwargs in callback.message.answers)
    assert "A private room in Kondapur" in response
    assert "whether the property is still available" in response
    assert "maintenance charges and whether they are mandatory" in response
    assert "FlatHunter's agent will collect these clarifications" in response
    assert callback.answers == [(None, {})]
