from types import SimpleNamespace

import pytest

from app.ingestion.schemas import FlatHunterExtractionV1
from app.ingestion.service import IngestionService


class Result:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, database, table):
        self.database = database
        self.table = table
        self.operation = None
        self.payload = None

    def select(self, *_args, **_kwargs):
        self.operation = "select"
        return self

    def update(self, payload):
        self.operation, self.payload = "update", payload
        return self

    def insert(self, payload):
        self.operation, self.payload = "insert", payload
        return self

    def eq(self, *_args):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def execute(self):
        if self.operation == "select" and self.table == "ingestion_inputs":
            return Result(self.database.inputs)
        if self.operation == "insert":
            self.database.inserts.append((self.table, self.payload))
            return Result([{"id": "draft-1"}])
        return Result([{"id": "updated"}])


class Database:
    def __init__(self):
        self.inputs = [
            {"id": "image-1", "input_type": "IMAGE", "is_information_bearing": True, "caption": None},
            {"id": "image-2", "input_type": "IMAGE", "is_information_bearing": True, "caption": "Latest details"},
            {"id": "gallery", "input_type": "IMAGE", "is_information_bearing": False, "caption": "Bedroom"},
            {"id": "text-1", "input_type": "TEXT", "is_information_bearing": True, "text_content": "Admin listing note"},
        ]
        self.inserts = []

    def table(self, name):
        return Query(self, name)


class CapturingVisionProvider:
    provider_name = "fake"
    model_name = "fake-model"
    last_metadata = {"request_id": "fake-request"}

    def __init__(self):
        self.calls = []

    async def extract_listing(self, **kwargs):
        self.calls.append(kwargs)
        return FlatHunterExtractionV1.model_validate({
            "content_type": "PROPERTY_LISTING",
            "canonical": {"listing_type": "PRIVATE_ROOM", "locality": "Kondapur", "rent": 15833},
        })


@pytest.mark.asyncio
async def test_single_ingestion_groups_information_images_as_one_property_and_excludes_gallery_media():
    database = Database()
    provider = CapturingVisionProvider()
    service = IngestionService(db=database, vision_provider=provider)

    draft_id = await service.complete_session_and_extract(
        "session-1", {"image-1": b"first", "image-2": b"second", "gallery": b"do-not-send"}
    )

    assert draft_id == "draft-1"
    assert len(provider.calls) == 1
    assert [image.source_id for image in provider.calls[0]["images"]] == ["image-1", "image-2"]
    assert provider.calls[0]["text_inputs"] == []
    assert provider.calls[0]["admin_notes"] == ["Latest details", "Admin listing note"]
    draft = next(payload for table, payload in database.inserts if table == "listing_drafts")
    assert draft["canonical_payload"]["listing_type"] == "PRIVATE_ROOM"
    assert draft["model_metadata"]["provider"] == "fake"
