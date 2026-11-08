"""The triage queue. A report must survive every way the queue can let it down:
a failed enqueue, a dead model, a task that runs out of retries. And the sweep
must leave alone everything that was not filed through the queue.
"""

import base64
import json
from datetime import datetime, timedelta

import pytest
from fastapi import BackgroundTasks
from flask import Flask

import jobs
import main
import sim
import triage
import triage_rules
from tests.fakes import FakeFirestore
from tests.test_jobs import FakeClient

DOC = "report-1"
SENDER = "whatsapp:+919812345678"
NOW = datetime(2026, 11, 7, 12, 0, 0)


def ago(**delta):
    return (NOW - timedelta(**delta)).isoformat()


@pytest.fixture(autouse=True)
def no_cooldown():
    triage.reset_model_cooldown()
    yield
    triage.reset_model_cooldown()


@pytest.fixture
def queue(monkeypatch):
    """Records what was enqueued instead of calling Cloud Tasks."""
    monkeypatch.setenv("TRIAGE_BACKEND", "queue")
    calls = []
    monkeypatch.setattr(jobs, "_enqueue", lambda lane, doc_id: calls.append((lane, doc_id)))
    return calls


@pytest.fixture
def db():
    store = FakeFirestore()
    store.data["messages"] = {DOC: {
        "id": DOC, "reference_id": "UOTEST01", "sender": SENDER, "status": "Open",
        "body": "garbage has not been collected in Gomti Nagar",
        "timestamp": ago(hours=1),
        "triage_status": jobs.STATUS_PENDING,
        jobs.QUEUE_OPEN_FIELD: True, jobs.LANE_FIELD: jobs.LANE_NORMAL,
        jobs.DETAILS_FIELD: jobs.DETAILS_PENDING,
    }}
    return store


def report(db):
    return db.data["messages"][DOC]


# --- choosing a lane ---------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Live wire hanging on the road after the storm",
    "There is an open manhole outside the market",
    "Something dangerous fell near the school gate",  # no known issue, but a danger word
    "सड़क पर बड़ा खतरा है",
])
def test_danger_goes_to_the_urgent_lane(text):
    assert jobs.lane_for({"body": text}) == jobs.LANE_URGENT, text


@pytest.mark.parametrize("text", ["Garbage has not been collected this week", "The park needs new benches"])
def test_routine_reports_stay_in_the_normal_lane(text):
    assert jobs.lane_for({"body": text}) == jobs.LANE_NORMAL, text


def test_every_lane_has_a_deployed_worker():
    for name in jobs.QUEUE_FOR_LANE.values():
        assert hasattr(getattr(main, name), "__firebase_endpoint__"), name
    for name, fn in (("triagetask", main.triagetask), ("triagetaskurgent", main.triagetaskurgent)):
        assert fn.__firebase_endpoint__.taskQueueTrigger is not None, name


def test_the_sweep_waits_longer_than_a_task_retries():
    retry = main.TRIAGE_RETRY
    backoffs = [min(retry.min_backoff_seconds * 2 ** n, retry.max_backoff_seconds)
                for n in range(retry.max_attempts - 1)]
    assert sum(backoffs) < jobs.SWEEP_GRACE_SECONDS


# --- dispatch ----------------------------------------------------------------

def test_dispatch_enqueues_on_the_reports_lane(db, queue):
    used = jobs.dispatch(db, DOC, report={jobs.LANE_FIELD: jobs.LANE_URGENT})

    assert used == jobs.BACKEND_QUEUE
    assert queue == [(jobs.LANE_URGENT, DOC)]


def test_a_failed_enqueue_leaves_the_report_for_the_sweep(db, monkeypatch):
    monkeypatch.setenv("TRIAGE_BACKEND", "queue")

    def broken(lane, doc_id):
        raise RuntimeError("cloud tasks down")
    monkeypatch.setattr(jobs, "_enqueue", broken)
    ran = []

    used = jobs.dispatch(db, DOC, runner=lambda *_: ran.append(1))

    assert used == jobs.LEFT_FOR_SWEEP
    assert ran == [], "a queue outage must not become slow webhooks"
    assert report(db)[jobs.QUEUE_OPEN_FIELD] is True


# --- the worker --------------------------------------------------------------

def test_a_finished_report_ends_its_task_and_closes(db):
    sent = []

    finished = jobs.process_queued_report(db, DOC, client=FakeClient(), notifier=lambda r: sent.append(r) or True)

    assert finished is True
    assert report(db)[jobs.QUEUE_OPEN_FIELD] is False
    assert report(db)["triage_source"].startswith("model:")
    assert len(sent) == 1


def test_a_model_outage_uses_the_rules_and_keeps_the_task_alive(db):
    sent = []

    finished = jobs.process_queued_report(db, DOC, client=FakeClient(error=RuntimeError("429 quota")),
                                          notifier=lambda r: sent.append(r) or True)

    assert finished is False, "a model should still look at it, so the queue must retry"
    r = report(db)
    assert r["triage_status"] == jobs.STATUS_FAILED
    assert r["triage_source"] == "rules"
    assert r["category"] != "Other", "the rules recognised garbage"
    assert r[jobs.QUEUE_OPEN_FIELD] is True
    assert len(sent) == 1, "the citizen should not wait out the outage"


def test_recovery_after_an_outage_upgrades_the_report_without_messaging_again(db):
    sent = []
    notifier = lambda r: sent.append(r) or True
    jobs.process_queued_report(db, DOC, client=FakeClient(error=RuntimeError("500")), notifier=notifier)
    triage.reset_model_cooldown()

    finished = jobs.process_queued_report(db, DOC, client=FakeClient(), notifier=notifier)

    assert finished is True
    assert report(db)["triage_status"] == jobs.STATUS_DONE
    assert report(db)["triage_source"].startswith("model:")
    assert len(sent) == 1


def test_an_undelivered_message_keeps_the_task_alive(db):
    assert jobs.process_queued_report(db, DOC, client=FakeClient(), notifier=lambda r: False) is False
    assert report(db)[jobs.QUEUE_OPEN_FIELD] is True


def test_a_deleted_report_ends_its_task(db):
    assert jobs.process_queued_report(db, "gone", client=FakeClient()) is True


def test_a_text_location_still_reaches_the_map(db):
    """Inline intake pins these from the gazetteer; deferring must not lose that."""
    jobs.process_queued_report(db, DOC, client=FakeClient(error=RuntimeError("500")))

    r = report(db)
    assert r["latitude"] is not None and r["longitude"] is not None


def test_a_gps_pin_is_not_replaced_by_an_estimate(db):
    report(db).update({"latitude": 26.8467, "longitude": 80.9462})

    jobs.process_queued_report(db, DOC, client=FakeClient(error=RuntimeError("500")))

    assert (report(db)["latitude"], report(db)["longitude"]) == (26.8467, 80.9462)


# --- the task handler --------------------------------------------------------

@pytest.fixture
def handler(monkeypatch, db):
    monkeypatch.setattr(main, "firestore_db", db)
    monkeypatch.setattr(main, "send_whatsapp_message", lambda to, body: object())
    monkeypatch.setattr(main, "fetch_twilio_media_part", lambda *a: None)


def call_worker(fn, payload):
    """Through the SDK's real request wrapper, whose status code drives retries."""
    app = Flask(__name__)
    body = json.dumps({"data": payload})
    with app.test_request_context("/", method="POST", data=body, content_type="application/json",
                                  headers={"Authorization": "Bearer fake"}):
        from flask import request
        return fn(request)


def test_what_the_admin_sdk_really_sends_is_accepted_by_the_worker(handler, db, monkeypatch):
    """Found in production: every delivery was a 400, while enqueueing reported
    success. Uses both SDKs' real encoding and validation, with nothing hand-built."""
    from firebase_admin import functions

    bodies = []

    class Queue:
        def enqueue(self, task_data):
            task = functions.TaskQueue._validate_task_options(None, task_data, None)
            bodies.append((base64.b64decode(task.http_request["body"]), task.http_request["headers"]))
    monkeypatch.setattr(functions, "task_queue", lambda name: Queue())
    monkeypatch.setattr(main.LLM, "get_provider", lambda *a, **k: FakeClient())

    jobs._enqueue(jobs.LANE_NORMAL, DOC)

    [(body, headers)] = bodies
    app = Flask(__name__)
    with app.test_request_context("/", method="POST", data=body, headers={**headers, "Authorization": "Bearer fake"}):
        from flask import request
        response = main.triagetask(request)

    assert response.status_code == 200
    assert report(db)["triage_status"] == jobs.STATUS_DONE


def test_a_doubly_wrapped_task_still_finds_its_report(handler, db, monkeypatch):
    monkeypatch.setattr(main.LLM, "get_provider", lambda *a, **k: FakeClient())

    assert call_worker(main.triagetask, {"data": {"doc_id": DOC}}).status_code == 200
    assert report(db)["triage_status"] == jobs.STATUS_DONE


def test_an_unfinished_task_fails_so_the_queue_retries(handler, monkeypatch):
    monkeypatch.setattr(main.LLM, "get_provider", lambda *a, **k: FakeClient(error=RuntimeError("429")))

    response = call_worker(main.triagetask, {"doc_id": DOC})

    assert response.status_code == 500


def test_a_finished_task_succeeds_so_the_queue_stops(handler, db, monkeypatch):
    monkeypatch.setattr(main.LLM, "get_provider", lambda *a, **k: FakeClient())

    response = call_worker(main.triagetaskurgent, {"doc_id": DOC})

    assert response.status_code == 200
    assert report(db)[jobs.QUEUE_OPEN_FIELD] is False
    assert report(db)[jobs.DETAILS_FIELD] == jobs.DETAILS_SENT


def test_a_task_with_no_report_is_not_retried_forever(handler):
    assert call_worker(main.triagetask, {}).status_code == 200


# --- the sweep ---------------------------------------------------------------

def test_the_sweep_leaves_a_recent_report_to_its_task(db, queue):
    report(db)["timestamp"] = ago(minutes=5)

    stats = jobs.sweep_open_reports(db, now=NOW)

    assert queue == []
    assert stats["waiting"] == 1


def test_the_sweep_rescues_a_quiet_report_on_its_own_lane(db, queue):
    report(db)[jobs.LANE_FIELD] = jobs.LANE_URGENT

    stats = jobs.sweep_open_reports(db, now=NOW)

    assert queue == [(jobs.LANE_URGENT, DOC)]
    assert stats["enqueued"] == 1
    assert report(db)[jobs.ENQUEUED_AT_FIELD] == NOW.isoformat()


def test_the_sweep_does_not_requeue_what_it_just_queued(db, queue):
    jobs.sweep_open_reports(db, now=NOW)

    jobs.sweep_open_reports(db, now=NOW + timedelta(minutes=5))
    assert len(queue) == 1

    jobs.sweep_open_reports(db, now=NOW + timedelta(minutes=25))
    assert len(queue) == 2


@pytest.mark.parametrize("old", [
    {"triage_status": "failed", "simulated": True},
    {"triage_status": "failed"},
    {"triage_status": "pending"},
])
def test_the_sweep_ignores_everything_not_filed_through_the_queue(db, queue, old):
    """Deploying the sweep must not start spending model quota on old reports."""
    db.data["messages"]["old"] = {"id": "old", "body": "x", "timestamp": ago(days=1), **old}
    db.data["messages"].pop(DOC)

    assert jobs.sweep_open_reports(db, now=NOW)["scanned"] == 0
    assert queue == []


def test_the_sweep_gives_up_visibly_instead_of_retrying_forever(db, queue):
    report(db)["timestamp"] = ago(days=4)

    stats = jobs.sweep_open_reports(db, now=NOW)

    assert stats["gave_up"] == 1
    assert queue == []
    assert report(db)[jobs.QUEUE_OPEN_FIELD] is False
    assert report(db)["needs_review"] is True


def test_the_sweep_stops_when_the_queue_is_down_and_keeps_the_report(db, monkeypatch):
    def broken(lane, doc_id):
        raise RuntimeError("cloud tasks down")
    monkeypatch.setattr(jobs, "_enqueue", broken)

    stats = jobs.sweep_open_reports(db, now=NOW)

    assert stats["enqueue_failed"] is True
    assert report(db)[jobs.QUEUE_OPEN_FIELD] is True
    assert jobs.ENQUEUED_AT_FIELD not in report(db), "so the next sweep tries it again"


def test_the_sweep_caps_each_run(db, queue, monkeypatch):
    monkeypatch.setattr(jobs, "SWEEP_ENQUEUE_LIMIT", 2)
    for n in range(4):
        db.data["messages"][f"r{n}"] = {**report(db), "id": f"r{n}"}

    stats = jobs.sweep_open_reports(db, now=NOW)

    assert stats["enqueued"] == 2
    assert stats["waiting"] == 3


def test_an_unreadable_timestamp_is_rescued_rather_than_abandoned(db, queue):
    report(db)["timestamp"] = "not a date"

    jobs.sweep_open_reports(db, now=NOW)

    assert queue == [(jobs.LANE_NORMAL, DOC)]
    assert report(db)[jobs.QUEUE_OPEN_FIELD] is True


# --- through the real intake conversation ------------------------------------

@pytest.fixture
def intake(monkeypatch):
    monkeypatch.setattr(main, "gemini_client", None)
    monkeypatch.setattr(main, "_groq_client", None)
    monkeypatch.setattr(main.LLM, "get_provider", lambda *a, **k: None)
    monkeypatch.setattr(main, "send_whatsapp_message", lambda to, body: object())


def file_report(store, text):
    steps = [
        {"From": SENDER, "Body": "new report"},
        {"From": SENDER, "Body": text},
        {"From": SENDER, "Body": "", "Latitude": "26.8534", "Longitude": "80.9981"},
        {"From": SENDER, "Body": "skip"},
    ]
    response = None
    for form in steps:
        response = main._process_whatsapp_sync(form, BackgroundTasks(), store)
    return bytes(response.body).decode("utf-8")


def test_intake_answers_without_triage_and_enqueues(intake, queue):
    store = FakeFirestore()

    receipt = file_report(store, "Sewage is overflowing onto the road in Gomti Nagar.")

    [stored] = store.data["messages"].values()
    assert "We will message you shortly" in receipt
    assert stored["triage_status"] == jobs.STATUS_PENDING
    assert stored[jobs.QUEUE_OPEN_FIELD] is True
    assert stored[jobs.DETAILS_FIELD] == jobs.DETAILS_PENDING
    assert queue == [(jobs.LANE_NORMAL, stored["id"])]


def test_intake_sends_an_emergency_down_the_urgent_lane(intake, queue):
    store = FakeFirestore()

    file_report(store, "A live wire is hanging over the road in Gomti Nagar.")

    [stored] = store.data["messages"].values()
    assert queue == [(jobs.LANE_URGENT, stored["id"])]


def test_the_sandbox_never_touches_the_queue(intake, queue):
    """Workers read the production database, so a task would never find this report."""
    store = sim.NamespacedDB(FakeFirestore())

    receipt = file_report(store, "Sewage is overflowing onto the road in Gomti Nagar.")

    assert queue == []
    assert "Category:" in receipt
    [stored] = store._db.data["sim_messages"].values()
    assert jobs.QUEUE_OPEN_FIELD not in stored


def test_rules_module_is_urgent_handles_empty_text():
    assert triage_rules.is_urgent("") is False



def test_known_text_can_finish_triage_without_a_model(db, monkeypatch):
    monkeypatch.setenv("TRIAGE_RULES_FIRST", "1")

    def unused_provider():
        pytest.fail("Known text must not consume model quota in the fast lane.")

    assert jobs.process_queued_report(db, DOC, client_factory=unused_provider, notifier=lambda report: True)
    assert report(db)["triage_source"] == "rules"
    assert report(db)["triage_status"] == jobs.STATUS_DONE
    assert report(db)[jobs.QUEUE_OPEN_FIELD] is False
