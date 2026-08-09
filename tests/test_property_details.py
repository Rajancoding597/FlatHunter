import pytest

from app.matching.details import (
    clarification_labels,
    draft_property_narrative,
    renter_safe_property_data,
)
from app.qualification.service import QualificationService


def test_renter_property_data_preserves_facts_but_removes_contacts_and_internal_notes():
    safe = renter_safe_property_data({
        "locality": "Kondapur",
        "rent": 15_833,
        "maintenance": None,
        "extracted_context": {
            "monthly_costs": [{"type": "UTILITIES", "amount": 663}],
            "phone_number": "+91-secret",
            "nested": {
                "owner_email": "secret@example.com",
                "room_feature": "Wardrobe",
                "description": "Call +91 98765 43210 for details",
            },
            "uncertain_fields": ["maintenance"],
            "extraction_notes": ["model note"],
        },
        "created_from_draft_id": "internal-id",
    })

    assert safe == {
        "property": {"locality": "Kondapur", "rent": 15_833},
        "additional_information": {
            "monthly_costs": [{"type": "UTILITIES", "amount": 663}],
            "nested": {"room_feature": "Wardrobe"},
        },
    }


def test_clarifications_are_ordered_deduplicated_and_human_readable():
    labels = clarification_labels([
        {"field": "maintenance", "priority": 3},
        {"field": "availability_status", "priority": 1},
        {"field": "maintenance", "priority": 4},
        {"field": "balcony", "priority": 2},
    ])

    assert labels == [
        "whether the property is still available",
        "balcony",
        "maintenance charges and whether they are mandatory",
    ]


class NarrativeLLM:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    async def generate_text(self, prompt):
        self.prompts.append(prompt)
        return self.response


@pytest.mark.asyncio
async def test_llm_narrative_uses_safe_data_and_accepts_supported_numbers():
    llm = NarrativeLLM("A private room in Kondapur is listed for ₹15,833, with utilities of ₹663.")
    narrative = await draft_property_narrative({
        "listing_type": "PRIVATE_ROOM",
        "locality": "Kondapur",
        "rent": 15_833,
        "extracted_context": {
            "monthly_costs": [{"type": "UTILITIES", "amount": 663}],
            "phone": "+91-secret",
        },
    }, llm=llm)

    assert narrative == "A private room in Kondapur is listed for ₹15,833, with utilities of ₹663."
    assert "+91-secret" not in llm.prompts[0]


@pytest.mark.asyncio
async def test_llm_narrative_falls_back_when_it_invents_a_number():
    llm = NarrativeLLM("This property has a ₹5,000 maintenance charge.")
    narrative = await draft_property_narrative({"locality": "Kondapur", "rent": 15_833}, llm=llm)

    assert "₹5,000" not in narrative
    assert "Rent: 15833" in narrative


class QueryResult:
    def __init__(self, data):
        self.data = data


class QualificationQuery:
    def __init__(self, database, table):
        self.database = database
        self.table = table
        self.operation = "select"
        self.payload = None

    def select(self, *_args):
        self.operation = "select"
        return self

    def insert(self, payload):
        self.operation, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.operation, self.payload = "update", payload
        return self

    def eq(self, *_args):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        if self.operation == "select":
            return QueryResult(self.database.rows[self.table])
        self.database.writes.append((self.table, self.operation, self.payload))
        return QueryResult([self.payload])


class QualificationDatabase:
    def __init__(self):
        self.rows = {
            "conversations": [{"id": "conv-1", "search_id": "search-1", "listing_id": "listing-1"}],
            "search_requirements": [{"listing_types": ["PRIVATE_ROOM"], "target_rent": 20_000}],
            "matches": [{"missing_information": [
                {"field": "availability_status", "priority": 1},
                {"field": "deposit", "priority": 2},
            ]}],
        }
        self.writes = []

    def table(self, name):
        return QualificationQuery(self, name)


@pytest.mark.asyncio
async def test_owner_outreach_includes_the_match_clarifications():
    database = QualificationDatabase()
    llm = NarrativeLLM("Hello, is the property available and what is the deposit?")
    service = QualificationService(db=database, llm=llm)

    response = await service.generate_initial_outreach("conv-1")

    assert response == "Hello, is the property available and what is the deposit?"
    assert "whether the property is still available" in llm.prompts[0]
    assert "the security deposit" in llm.prompts[0]
    saved_messages = [write for write in database.writes if write[0] == "messages"]
    assert saved_messages[0][2]["text"] == response
