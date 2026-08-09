import os

import pytest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_TELEGRAM_IDS", "1")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from app.jobs.worker import JobWorker


class Response:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self


class Query:
    def __init__(self, writes):
        self.writes = writes

    def update(self, payload):
        self.writes.append(payload)
        return self

    def eq(self, *_args):
        return self

    def execute(self):
        return Response([])


class FakeDatabase:
    def __init__(self, jobs):
        self.jobs = jobs
        self.writes = []
        self.claim_args = None

    def rpc(self, name, args):
        self.claim_args = (name, args)
        return Response(self.jobs)

    def table(self, _name):
        return Query(self.writes)


class FakeMatcher:
    def __init__(self):
        self.searches = []

    def process_new_search(self, search_id):
        self.searches.append(search_id)


@pytest.mark.asyncio
async def test_claimed_matching_job_runs_once_and_is_completed():
    db = FakeDatabase([{"id": "job-1", "job_type": "MATCH_ACTIVE_SEARCH", "payload": {"search_id": "search-1"}, "attempts": 1}])
    matcher = FakeMatcher()
    worker = JobWorker(db=db, matching_engine=matcher, worker_id="test-worker")

    processed = await worker.run_once()

    assert processed is True
    assert db.claim_args == ("claim_next_agent_job", {"p_worker_id": "test-worker"})
    assert matcher.searches == ["search-1"]
    assert any(write["status"] == "SUCCEEDED" for write in db.writes)


@pytest.mark.asyncio
async def test_failed_job_is_requeued_before_retry_limit():
    db = FakeDatabase([{"id": "job-2", "job_type": "UNKNOWN", "payload": {}, "attempts": 1}])
    worker = JobWorker(db=db, matching_engine=FakeMatcher(), worker_id="test-worker")

    await worker.run_once()

    # Unknown jobs log a warning but are terminally handled, so emulate a real failure directly.
    worker._record_failure({"id": "job-3", "attempts": 1}, RuntimeError("temporary failure"))
    assert any(write["status"] == "PENDING" and "run_after" in write for write in db.writes)


class NotificationQuery:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        return Response(self.rows)


class NotificationDatabase:
    def __init__(self):
        self.rows = {
            "matches": [{
                "id": "a30559d3-defd-4088-b78d-109368a0caf3",
                "status": "STRONG_MATCH",
                "fit_score": 94,
                "positive_reasons": ["Within budget"],
                "missing_information": [],
            }],
            "search_sessions": [{
                "user_id": "user-1",
                "status": "ACTIVE",
                "version": 3,
            }],
            "users": [{"telegram_user_id": 12345}],
            "listings": [{
                "property_configuration": "2BHK",
                "locality": "Manikonda",
                "rent": 37_000,
                "maintenance": 3_000,
            }],
            "visits": [{
                "id": "visit-1",
                "search_id": "search-1",
                "status": "AWAITING_RENTER_CONFIRMATION",
                "proposed_start": "2026-08-12T11:00:00+05:30",
            }],
        }

    def table(self, name):
        return NotificationQuery(self.rows[name])


class SequencedSearchDatabase(NotificationDatabase):
    def __init__(self, search_states):
        super().__init__()
        self.search_states = list(search_states)
        self.search_reads = 0

    def table(self, name):
        if name == "search_sessions":
            index = min(self.search_reads, len(self.search_states) - 1)
            self.search_reads += 1
            return NotificationQuery([self.search_states[index]])
        return super().table(name)


def install_fake_bot(monkeypatch, sent_messages):
    import aiogram

    class FakeSession:
        async def close(self):
            return None

    class FakeBot:
        def __init__(self, **_kwargs):
            self.session = FakeSession()

        async def send_message(self, **kwargs):
            sent_messages.append(kwargs)

    monkeypatch.setattr(aiogram, "Bot", FakeBot)


@pytest.mark.asyncio
async def test_strong_match_notification_sends_details_with_valid_match_actions(monkeypatch):
    sent_messages = []
    install_fake_bot(monkeypatch, sent_messages)
    worker = JobWorker(db=NotificationDatabase(), matching_engine=FakeMatcher())

    await worker.process_job({
        "job_type": "SEND_RENTER_NOTIFICATION",
        "payload": {
            "search_id": "search-1",
            "listing_id": "listing-1",
            "search_version": 3,
        },
    })

    assert len(sent_messages) == 1
    message = sent_messages[0]
    assert "2BHK in Manikonda" in message["text"]
    assert "Maintenance: ₹3,000" in message["text"]
    callback_data = [button.callback_data for row in message["reply_markup"].inline_keyboard for button in row]
    assert callback_data == [
        "details_match_a30559d3-defd-4088-b78d-109368a0caf3",
        "contact_match_a30559d3-defd-4088-b78d-109368a0caf3",
        "skip_match_a30559d3-defd-4088-b78d-109368a0caf3",
    ]
    assert all(len(value.encode("utf-8")) <= 64 for value in callback_data)


@pytest.mark.asyncio
async def test_match_notification_is_skipped_if_search_pauses_at_send_boundary(monkeypatch):
    sent_messages = []
    install_fake_bot(monkeypatch, sent_messages)
    db = SequencedSearchDatabase([
        {"user_id": "user-1", "status": "ACTIVE", "version": 3},
        {"user_id": "user-1", "status": "PAUSED", "version": 4},
    ])
    worker = JobWorker(db=db, matching_engine=FakeMatcher())

    await worker.process_job({
        "job_type": "SEND_RENTER_NOTIFICATION",
        "payload": {
            "search_id": "search-1",
            "listing_id": "listing-1",
            "search_version": 3,
        },
    })

    assert db.search_reads == 2
    assert sent_messages == []


@pytest.mark.asyncio
async def test_match_notification_is_skipped_for_stale_search_version(monkeypatch):
    sent_messages = []
    install_fake_bot(monkeypatch, sent_messages)
    db = NotificationDatabase()
    worker = JobWorker(db=db, matching_engine=FakeMatcher())

    await worker.process_job({
        "job_type": "SEND_RENTER_NOTIFICATION",
        "payload": {
            "search_id": "search-1",
            "listing_id": "listing-1",
            "search_version": 2,
        },
    })

    assert sent_messages == []


@pytest.mark.asyncio
async def test_visit_proposal_is_skipped_if_search_pauses_at_send_boundary(monkeypatch):
    sent_messages = []
    install_fake_bot(monkeypatch, sent_messages)
    db = SequencedSearchDatabase([
        {"user_id": "user-1", "status": "ACTIVE", "version": 3},
        {"user_id": "user-1", "status": "PAUSED", "version": 4},
    ])
    worker = JobWorker(db=db, matching_engine=FakeMatcher())

    await worker.process_job({
        "job_type": "PROPOSE_VISIT_TO_RENTER",
        "payload": {
            "visit_id": "visit-1",
            "search_id": "search-1",
            "search_version": 3,
        },
    })

    assert db.search_reads == 2
    assert sent_messages == []
