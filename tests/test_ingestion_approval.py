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
        self.operation = 'select'
        return self

    def insert(self, payload):
        self.operation = 'insert'
        self.payload = payload
        return self

    def update(self, payload):
        self.operation = 'update'
        self.payload = payload
        return self

    def eq(self, *_args):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def execute(self):
        if self.operation == 'select' and self.table == 'listing_drafts':
            return Result([self.database.draft])
        if self.operation == 'select' and self.table == 'ingestion_inputs':
            return Result([])
        if self.operation == 'insert':
            self.database.inserts.append((self.table, self.payload))
            identifier = 'listing-1' if self.table == 'listings' else f'{self.table}-1'
            return Result([{'id': identifier}])
        if self.operation == 'update':
            self.database.updates.append((self.table, self.payload))
            return Result([self.payload])
        raise AssertionError(f'Unexpected operation for {self.table}')


class ApprovalDatabase:
    def __init__(self):
        self.draft = {
            'id': 'draft-1',
            'ingestion_session_id': 'session-1',
            'content_type': 'PROPERTY_LISTING',
            'extraction_status': 'SUCCESS',
            'canonical_payload': {
                'listing_type': 'ENTIRE_PROPERTY',
                'city': 'Hyderabad',
                'locality': 'Madhapur',
                'property_configuration': '2BHK',
                'rent': 30_000,
                'contacts': [],
            },
            'extracted_context': {},
        }
        self.inserts = []
        self.updates = []

    def table(self, name):
        return Query(self, name)


def test_approving_valid_draft_inserts_available_listing():
    database = ApprovalDatabase()
    service = IngestionService(db=database, llm=object())

    listing_id = service.approve_draft('draft-1')

    listing = next(payload for table, payload in database.inserts if table == 'listings')
    assert listing_id == 'listing-1'
    assert listing['availability_status'] == 'AVAILABLE'
    assert listing['created_from_draft_id'] == 'draft-1'
