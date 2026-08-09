from app.ingestion.service import IngestionService


class Response:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self


class Query:
    def __init__(self, db, table):
        self.db = db
        self.table_name = table

    def insert(self, payload):
        self.db.writes.append((self.table_name, payload))
        return self

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def execute(self):
        if self.table_name == "ingestion_inputs" and self.db.read_inputs:
            return Response(self.db.read_inputs)
        return Response([{"id": f"{self.table_name}-id"}])


class FakeDatabase:
    def __init__(self, read_inputs=None):
        self.writes = []
        self.read_inputs = read_inputs or []

    def table(self, name):
        return Query(self, name)


def test_image_input_stores_telegram_references_not_base64_text():
    db = FakeDatabase()
    service = IngestionService(db=db, llm=object())

    service.add_image_input("session-1", "file-id", "unique-id", is_information_bearing=True, caption="details")

    table, payload = db.writes[0]
    assert table == "ingestion_inputs"
    assert payload["telegram_file_id"] == "file-id"
    assert payload["telegram_file_unique_id"] == "unique-id"
    assert payload["text_content"] if "text_content" in payload else None is None
    assert "base64" not in repr(payload).lower()


def test_approval_provenance_separates_source_images_from_property_media():
    db = FakeDatabase(read_inputs=[
        {"input_type": "TEXT", "text_content": "2BHK details", "sort_order": 0},
        {"input_type": "IMAGE", "is_information_bearing": True, "telegram_file_id": "screen", "telegram_file_unique_id": "screen-u", "caption": "rent"},
        {"input_type": "IMAGE", "is_information_bearing": False, "telegram_file_id": "photo", "telegram_file_unique_id": "photo-u", "caption": "front", "sort_order": 2},
    ])
    service = IngestionService(db=db, llm=object())

    service._persist_sources_and_media("listing-1", "session-1")

    source_types = [payload["source_type"] for table, payload in db.writes if table == "listing_sources"]
    media = [payload for table, payload in db.writes if table == "listing_media"]
    assert source_types == ["ADMIN_TEXT", "ADMIN_SCREENSHOT"]
    assert media == [{"listing_id": "listing-1", "telegram_file_id": "photo", "telegram_file_unique_id": "photo-u", "media_type": "PHOTO", "sort_order": 2, "caption": "front"}]
