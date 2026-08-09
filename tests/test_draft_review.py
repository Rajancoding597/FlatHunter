import pytest

from app.ingestion.service import DraftApprovalError, IngestionService
from app.telegram import admin_handlers


class Result:
    def __init__(self, data):
        self.data = data


class DraftQuery:
    def __init__(self, database):
        self.database = database
        self.operation = None
        self.payload = None

    def select(self, *_args, **_kwargs):
        self.operation = "select"
        return self

    def update(self, payload):
        self.operation, self.payload = "update", payload
        return self

    def eq(self, *_args):
        return self

    def execute(self):
        if self.operation == "select":
            return Result([self.database.draft])
        if self.operation == "update":
            self.database.draft.update(self.payload)
            return Result([self.database.draft])
        raise AssertionError("Unexpected query operation")


class DraftDatabase:
    def __init__(self):
        self.draft = {
            "content_type": "PROPERTY_LISTING",
            "extraction_status": "SUCCESS",
            "canonical_payload": {
                "listing_type": "ENTIRE_PROPERTY",
                "property_configuration": "2BHK",
                "city": "Hyderabad",
                "locality": "Manikonda",
                "rent": None,
                "maintenance": None,
                "deposit": None,
                "brokerage": None,
                "available_from": None,
                "furnishing": "SEMI_FURNISHED",
                "attached_bathroom": None,
                "car_parking": None,
                "bike_parking": None,
                "location_text": "Manikonda, Hyderabad",
                "landmark": None,
                "contacts": [],
            },
        }

    def table(self, name):
        assert name == "listing_drafts"
        return DraftQuery(self)


def test_review_sections_include_flexible_context_and_conflicts():
    sections = admin_handlers._draft_review_sections({
        "canonical_payload": {"locality": "Manikonda", "rent": None},
        "extracted_context": {"features": ["Balcony", "Lift"]},
        "conflicts": [{"field": "furnishing", "values": ["FURNISHED", "SEMI_FURNISHED"]}],
        "content_type": "PROPERTY_LISTING",
        "extraction_status": "SUCCESS",
        "created_at": "2026-08-09T10:00:00Z",
        "model_metadata": {"provider": "gemini", "model": "gemini-2.5-flash"},
    })

    assert [title for title, _content in sections] == [
        "Canonical fields",
        "Additional extracted information",
        "Conflicts requiring review",
        "Draft details",
        "Extraction metadata",
    ]


def test_draft_actions_offer_full_review_before_approval():
    keyboard = admin_handlers._draft_action_keyboard("draft-1")
    callback_data = [button.callback_data for row in keyboard.inline_keyboard for button in row]

    assert callback_data == [
        "review_draft_draft-1",
        "approve_draft_draft-1",
        "reject_draft_draft-1",
    ]


def test_edit_command_parses_money_booleans_enums_and_spaced_locality():
    edits = admin_handlers._parse_draft_edits(
        "/editdraft rent=₹25,000; locality=Financial District; attached_bathroom=yes; furnishing=semi furnished"
    )

    assert edits == {
        "rent": 25000,
        "locality": "Financial District",
        "attached_bathroom": True,
        "furnishing": "SEMI_FURNISHED",
    }


def test_service_validates_and_updates_missing_canonical_fields():
    database = DraftDatabase()
    service = IngestionService(db=database, llm=object())

    updated = service.update_draft_canonical("draft-1", {"rent": 25000})

    assert updated["rent"] == 25000
    assert updated["locality"] == "Manikonda"
    assert updated["contacts"] == []


def test_approval_reports_exact_missing_fields_before_writing_inventory():
    service = IngestionService(db=DraftDatabase(), llm=object())

    with pytest.raises(DraftApprovalError) as raised:
        service.approve_draft("draft-1")

    assert raised.value.missing_fields == ("rent",)


class FakeMessage:
    def __init__(self):
        self.answers = []

    async def answer(self, text, **_kwargs):
        self.answers.append(text)


class FakeState:
    def __init__(self):
        self.cleared = False
        self.data = {"draft_id": "draft-1"}
        self.current_state = None

    async def get_data(self):
        return self.data

    async def clear(self):
        self.cleared = True
        self.data = {}
        self.current_state = None

    async def update_data(self, **values):
        self.data.update(values)

    async def set_state(self, state):
        self.current_state = state


class FakeCallback:
    def __init__(self, data, message):
        self.data = data
        self.message = message
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))


class RejectingApprovalService:
    def approve_draft(self, _draft_id):
        raise DraftApprovalError(["rent"])


@pytest.mark.asyncio
async def test_approval_handler_returns_actionable_message_without_clearing_review_state(monkeypatch):
    message, state = FakeMessage(), FakeState()
    monkeypatch.setattr(admin_handlers, "ingest_service", RejectingApprovalService())

    await admin_handlers.cmd_approve_draft(message, state)

    assert "cannot be approved yet" in message.answers[0]
    assert "/editdraft" in message.answers[0]
    assert state.cleared is False


@pytest.mark.asyncio
async def test_review_callback_opens_full_preview_in_editable_state(monkeypatch):
    message, state = FakeMessage(), FakeState()
    callback = FakeCallback("review_draft_draft-42", message)
    previewed = []

    async def fake_preview(preview_message, draft_id):
        previewed.append((preview_message, draft_id))

    monkeypatch.setattr(admin_handlers, "_send_draft_preview", fake_preview)

    await admin_handlers.process_review_draft_callback(callback, state)

    assert state.data == {"draft_id": "draft-42"}
    assert state.current_state == admin_handlers.AdminState.confirming_listing
    assert previewed == [(message, "draft-42")]
    assert callback.answers == [(None, {})]
