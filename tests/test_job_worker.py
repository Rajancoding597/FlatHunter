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