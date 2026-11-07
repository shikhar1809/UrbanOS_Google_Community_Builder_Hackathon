"""A deferred report is answered twice: an instant receipt, then the details once
triage has decided them. The citizen must get the second message exactly once,
and nobody else may get one at all.
"""

import pytest
from fastapi import BackgroundTasks

import jobs
import main
import triage
from tests.fakes import FakeFirestore
from tests.test_jobs import FakeClient

DOC = "report-1"
SENDER = "whatsapp:+919812345678"
REPORT_TEXT = "Sewage is overflowing onto the road in Gomti Nagar."
ASSIGNMENT_WORDS = ("Category:", "Priority:", "Officer:", "has been notified")


class Notifier:
    def __init__(self, results=(True,)):
        self.sent = []
        self._results = list(results)

    def __call__(self, report):
        self.sent.append(report)
        result = self._results.pop(0) if self._results else True
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def db():
    store = FakeFirestore()
    store.data["messages"] = {DOC: {
        "id": DOC, "reference_id": "UOTEST01", "body": "sewage overflowing near the market",
        "sender": SENDER, "triage_status": jobs.STATUS_PENDING, "status": "Open",
        **jobs.details_fields(SENDER, "English"),
    }}
    return store


def report(db):
    return db.data["messages"][DOC]


# --- the details message ----------------------------------------------------

def test_the_details_carry_what_triage_decided(db):
    notifier = Notifier()

    assert jobs.process_triage(db, DOC, client=FakeClient(), notifier=notifier) is True

    [sent] = notifier.sent
    assert sent["ward_officer"] == "Ramesh Yadav"
    assert sent["category"] == "Infrastructure Upgrade"
    assert report(db)[jobs.DETAILS_FIELD] == jobs.DETAILS_SENT


def test_a_repeated_job_does_not_message_twice(db):
    notifier = Notifier()

    jobs.process_triage(db, DOC, client=FakeClient(), notifier=notifier)
    jobs.process_triage(db, DOC, client=FakeClient(), notifier=notifier)

    assert len(notifier.sent) == 1


def test_a_competing_job_holding_the_claim_does_not_message(db):
    assert main.IDEM.claim(db, f"triage-details-{DOC}").granted
    notifier = Notifier()

    jobs.process_triage(db, DOC, client=FakeClient(), notifier=notifier)

    assert notifier.sent == []
    assert report(db)[jobs.DETAILS_FIELD] == jobs.DETAILS_PENDING


def test_failed_triage_sends_nothing_and_a_later_success_does(db):
    notifier = Notifier()

    jobs.process_triage(db, DOC, client=FakeClient(error=RuntimeError("429")), notifier=notifier)
    assert notifier.sent == [], "nothing has been decided yet"

    jobs.process_triage(db, DOC, client=FakeClient(), notifier=notifier)
    assert len(notifier.sent) == 1


@pytest.mark.parametrize("failure", [False, RuntimeError("twilio down")])
def test_an_undelivered_message_is_retried_by_the_backlog_run(db, failure):
    notifier = Notifier([failure, True])
    client = FakeClient()

    assert jobs.process_triage(db, DOC, client=client, notifier=notifier) is True
    assert report(db)[jobs.DETAILS_FIELD] == jobs.DETAILS_PENDING

    result = jobs.retriage_pending(db, limit=10, client=client, notifier=notifier)

    assert result["found"] == 1
    assert len(notifier.sent) == 2
    assert client.calls == 1, "redelivery must not re-bill the model"
    assert report(db)[jobs.DETAILS_FIELD] == jobs.DETAILS_SENT


def test_draining_an_old_backlog_messages_nobody(db):
    """These citizens already had a full receipt when they filed."""
    db.data["messages"][DOC].pop(jobs.DETAILS_FIELD)
    db.data["messages"][DOC]["triage_status"] = jobs.STATUS_FAILED
    notifier = Notifier()

    result = jobs.retriage_pending(db, limit=10, client=FakeClient(), notifier=notifier)

    assert result["triaged"] == 1
    assert notifier.sent == []


def test_a_report_is_listed_once_though_it_matches_twice(db):
    assert jobs.pending_reports(db, limit=10) == [DOC]


def test_a_simulated_citizen_is_never_flagged():
    assert jobs.details_fields("whatsapp:+999123456789", "English") == {}


def test_the_message_is_built_from_the_triaged_fields(monkeypatch):
    outbox = []
    monkeypatch.setattr(main, "send_whatsapp_message", lambda to, body: outbox.append((to, body)) or object())

    delivered = main._notify_triage_details({
        "sender": SENDER, "reference_id": "UOTEST01", "category": "Sanitation", "priority": "Critical",
        "ward_no": "Ward 7", "responsible_body": "Jal Sansthan", "ward_officer": "Ramesh Yadav",
        "citizen_language": "English",
    })

    assert delivered is True
    [(to, body)] = outbox
    assert to == SENDER
    assert "\n\n" in body and "\\n" not in body
    for expected in ("#UOTEST01", "Sanitation", "Critical", "Ward 7", "Jal Sansthan", "Ramesh Yadav"):
        assert expected in body


def test_a_failed_send_is_reported_as_undelivered(monkeypatch):
    monkeypatch.setattr(main, "send_whatsapp_message", lambda to, body: None)

    assert main._notify_triage_details({"sender": SENDER, "reference_id": "UOTEST01"}) is False


# --- through the real intake conversation ------------------------------------

def file_report(db, tasks):
    """The shortest real conversation that files a report. Returns the last reply."""
    steps = [
        {"From": SENDER, "Body": "new report"},
        {"From": SENDER, "Body": REPORT_TEXT},
        {"From": SENDER, "Body": "", "Latitude": "26.8534", "Longitude": "80.9981"},
        {"From": SENDER, "Body": "skip"},
    ]
    response = None
    for form in steps:
        response = main._process_whatsapp_sync(form, tasks, db)
    return bytes(response.body).decode("utf-8")


@pytest.fixture
def intake(monkeypatch):
    outbox = []
    monkeypatch.setattr(main, "gemini_client", None)
    monkeypatch.setattr(main, "_groq_client", None)
    monkeypatch.setattr(main.LLM, "get_provider", lambda *a, **k: None)
    monkeypatch.setattr(main, "send_whatsapp_message", lambda to, body: outbox.append((to, body)) or object())
    triage.reset_model_cooldown()
    yield outbox
    triage.reset_model_cooldown()


def test_deferred_intake_gives_an_instant_receipt_then_the_details(intake, monkeypatch):
    monkeypatch.setenv("TRIAGE_BACKEND", "background")
    store, tasks = FakeFirestore(), BackgroundTasks()

    receipt = file_report(store, tasks)

    [stored] = store.data["messages"].values()
    assert f"#{stored['reference_id']}" in receipt
    assert "We will message you shortly" in receipt
    for word in ASSIGNMENT_WORDS:
        assert word not in receipt, "the receipt must not present an undecided assignment"
    assert intake == [], "the details wait for triage"
    assert stored[jobs.DETAILS_FIELD] == jobs.DETAILS_PENDING

    monkeypatch.setattr(main.LLM, "get_provider", lambda *a, **k: FakeClient())
    [task] = tasks.tasks
    task.func(*task.args, **task.kwargs)

    [(to, details)] = intake
    assert to == SENDER
    assert f"#{stored['reference_id']}" in details
    [triaged] = store.data["messages"].values()
    assert triaged["ward_officer"] in details, "the officer on record, which a GPS pin decides"
    assert triaged[jobs.DETAILS_FIELD] == jobs.DETAILS_SENT


def test_inline_intake_still_sends_one_full_receipt(intake, monkeypatch):
    monkeypatch.delenv("TRIAGE_BACKEND", raising=False)
    store = FakeFirestore()

    receipt = file_report(store, BackgroundTasks())

    [stored] = store.data["messages"].values()
    assert "Category:" in receipt and "Officer:" in receipt
    assert jobs.DETAILS_FIELD not in stored
    assert intake == []
