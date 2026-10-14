"""Deferred triage.

Triage is the slow part of intake. Running it after the citizen has been
answered is what stops the webhook timing out, but only if a deferred job is
safe to lose, safe to repeat, and recoverable when it fails.
"""

import pytest

import jobs
import triage
from tests.fakes import FakeFirestore

DOC = "report-1"


class FakeParsed:
    category = "Infrastructure Upgrade"
    priority = "Critical"
    feasibility = "Moderate"
    extracted_location = "Hazratganj"
    summary = "Sewage overflow"
    original_language = "Hindi"
    constituency_zone = "Central"
    estimated_budget = 250000
    responsible_body = "Jal Sansthan (Water Board)"
    ward_no = "Ward 7"
    ward_officer = "Ramesh Yadav"
    semantic_tag = "sewage_overflow"
    visual_evidence = "sewage across the road"


class FakeClient:
    def __init__(self, error=None):
        self.calls = 0
        self._error = error
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls += 1
        if self._error:
            raise self._error
        return type("R", (), {"parsed": FakeParsed()})()


@pytest.fixture
def db():
    store = FakeFirestore()
    store.data.setdefault("messages", {})[DOC] = {
        "id": DOC,
        "reference_id": "UOTEST01",
        "body": "sewage overflowing near the market",
        "sender": "whatsapp:+919812345678",
        "triage_status": jobs.STATUS_PENDING,
        "status": "Open",
    }
    return store


def report(db):
    return db.data["messages"][DOC]


def test_triage_fills_in_the_structured_fields(db):
    ok = jobs.process_triage(db, DOC, gemini_client=FakeClient())

    assert ok is True
    r = report(db)
    assert r["category"] == "Infrastructure Upgrade"
    assert r["priority"] == "Critical"
    assert r["semantic_tag"] == "sewage_overflow"
    assert r["triage_status"] == jobs.STATUS_DONE
    assert r["triaged_at"]


def test_a_repeated_job_does_not_call_the_model_again(db):
    client = FakeClient()
    jobs.process_triage(db, DOC, gemini_client=client)
    jobs.process_triage(db, DOC, gemini_client=client)

    assert client.calls == 1, "a duplicate job must not re-bill the model"


def test_a_model_failure_leaves_the_report_retriable(db):
    ok = jobs.process_triage(db, DOC, gemini_client=FakeClient(error=RuntimeError("429 quota")))

    assert ok is False
    r = report(db)
    assert r["triage_status"] == jobs.STATUS_FAILED
    assert r["status"] == "Open", "the citizen's report itself must survive"
    assert r.get("category") is None


def test_a_failed_report_is_picked_up_by_a_later_run(db):
    jobs.process_triage(db, DOC, gemini_client=FakeClient(error=RuntimeError("429 quota")))

    recovered = jobs.process_triage(db, DOC, gemini_client=FakeClient())

    assert recovered is True
    assert report(db)["triage_status"] == jobs.STATUS_DONE


def test_a_missing_report_is_not_an_error(db):
    assert jobs.process_triage(db, "does-not-exist", gemini_client=FakeClient()) is False


def test_pending_and_failed_reports_are_both_listed(db):
    db.data["messages"]["r2"] = {"id": "r2", "body": "x", "triage_status": jobs.STATUS_FAILED}
    db.data["messages"]["r3"] = {"id": "r3", "body": "x", "triage_status": jobs.STATUS_DONE}

    found = jobs.pending_reports(db, limit=10)

    assert set(found) == {DOC, "r2"}, "already-triaged reports must not be redone"


def test_draining_the_backlog_reports_what_it_did(db):
    db.data["messages"]["r2"] = {"id": "r2", "body": "broken pipe", "triage_status": jobs.STATUS_FAILED}

    result = jobs.retriage_pending(db, limit=10, gemini_client=FakeClient())

    assert result == {"found": 2, "triaged": 2, "still_pending": 0}


def test_draining_reports_what_it_could_not_fix(db):
    result = jobs.retriage_pending(db, limit=10, gemini_client=FakeClient(error=RuntimeError("429")))

    assert result["found"] == 1
    assert result["triaged"] == 0
    assert result["still_pending"] == 1


# --- dispatch -------------------------------------------------------------

class Recorder:
    def __init__(self):
        self.tasks = []

    def add_task(self, fn, *args):
        self.tasks.append((fn, args))


def test_inline_is_the_default_so_behaviour_is_unchanged(db, monkeypatch):
    monkeypatch.delenv("TRIAGE_BACKEND", raising=False)
    ran = []

    used = jobs.dispatch(db, DOC, Recorder(), runner=lambda *_: ran.append(1))

    assert used == jobs.BACKEND_INLINE
    assert ran == [1], "inline must run immediately"


def test_background_mode_defers_the_work(db, monkeypatch):
    monkeypatch.setenv("TRIAGE_BACKEND", "background")
    tasks = Recorder()
    ran = []

    used = jobs.dispatch(db, DOC, tasks, runner=lambda *_: ran.append(1))

    assert used == jobs.BACKEND_BACKGROUND
    assert ran == [], "the work must not run during the request"
    assert len(tasks.tasks) == 1, "it must be queued to run after the response"


def test_background_mode_falls_back_when_there_is_no_queue(db, monkeypatch):
    monkeypatch.setenv("TRIAGE_BACKEND", "background")
    ran = []

    used = jobs.dispatch(db, DOC, None, runner=lambda *_: ran.append(1))

    assert used == jobs.BACKEND_INLINE
    assert ran == [1], "a report must never be silently dropped"


def test_an_unknown_backend_falls_back_to_inline(monkeypatch):
    monkeypatch.setenv("TRIAGE_BACKEND", "kafka-someday")

    assert jobs.backend() == jobs.BACKEND_INLINE


def test_a_gps_pin_still_sets_the_ward_without_the_model(db):
    db.data["messages"][DOC].update({"latitude": 26.8467, "longitude": 80.9462})

    jobs.process_triage(db, DOC, gemini_client=FakeClient(error=RuntimeError("down")))

    expected = triage.ward_from_coordinates(26.8467, 80.9462)
    assert expected is not None
