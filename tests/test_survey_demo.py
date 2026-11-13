"""The survey demo loop: launch on the dashboard, answer in the sandbox, see the
answer on the dashboard. And a broadcast must never message a real citizen
whose number has not been approved.
"""

import pytest
from fastapi.testclient import TestClient

import main
import sim
import sim_api
import triage
from tests.fakes import FakeFirestore

REAL_CITIZENS = ["whatsapp:+919811111111", "whatsapp:+919822222222"]
SANDBOX_PHONE = "919900012345"
SURVEY = {"id": "s1", "question": "Should the park in Aliganj get new lights?", "options": "Yes, No",
          "is_active": True}


class Twilio:
    """Records every real WhatsApp message instead of sending it."""
    sent: list = []

    def __init__(self, *args, **kwargs):
        self.messages = self

    def create(self, from_, body, to):
        Twilio.sent.append(to)


@pytest.fixture
def db(monkeypatch):
    store = FakeFirestore()
    store.data["messages"] = {
        f"r{n}": {"id": f"r{n}", "reference_id": f"UO{n}", "sender": phone, "body": "Broken streetlight",
                  "timestamp": "2026-11-01T10:00:00", "category": "Electricity", "priority": "Medium",
                  "status": "Open", "constituency_zone": "North", "ward_no": "Ward 7"}
        for n, phone in enumerate(REAL_CITIZENS)
    }
    store.data["surveys"] = {"s1": dict(SURVEY)}
    Twilio.sent = []
    monkeypatch.setattr(main, "Client", Twilio)
    for name in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_NUMBER"):
        monkeypatch.setattr(main, name, "set")
    monkeypatch.setattr(main, "gemini_client", None)
    monkeypatch.setattr(main.LLM, "get_provider", lambda *a, **k: None)
    monkeypatch.setattr(sim_api, "firestore_db", store)
    main.app.dependency_overrides[main.get_db] = lambda: store
    main.AN.invalidate_snapshot()
    triage.reset_model_cooldown()
    yield store
    main.app.dependency_overrides.clear()
    main.AN.invalidate_snapshot()


@pytest.fixture
def dashboard(db):
    return TestClient(main.app)


@pytest.fixture
def sandbox(db):
    return TestClient(sim_api.sim_app)


def approve(db, *phones):
    db.data.setdefault("config", {})["whatsapp_allowlist"] = {"phones": list(phones)}


def test_with_no_approved_numbers_no_real_citizen_is_messaged(dashboard, db):
    result = dashboard.post("/surveys/s1/broadcast", json={"dry_run": False}).json()

    assert Twilio.sent == [], "the seeded data holds real-looking numbers"
    assert result["broadcast_count"] == 0
    assert result["targeted"] == 2
    assert result["sandbox_delivered"] is True


def test_only_approved_numbers_get_real_whatsapp(dashboard, db):
    approve(db, "+91 98111 11111")

    result = dashboard.post("/surveys/s1/broadcast", json={"dry_run": False}).json()

    assert Twilio.sent == ["whatsapp:+919811111111"]
    assert result["broadcast_count"] == 1


def test_the_dry_run_says_how_many_real_messages_would_go(dashboard, db):
    approve(db, "919822222222")

    dry = dashboard.post("/surveys/s1/broadcast", json={"dry_run": True}).json()

    assert dry["would_send_to"] == 2, "the dashboard still shows the matching audience"
    assert dry["whatsapp_recipients"] == 1
    assert Twilio.sent == [] and "sim_inbox" not in db.data, "a dry run sends nothing anywhere"


def test_a_broadcast_reaches_the_sandbox_and_the_reply_counts_on_the_dashboard(dashboard, sandbox, db):
    assert sandbox.get("/sim/api/inbox", params={"phone": SANDBOX_PHONE, "since": 0}).json()["messages"] == []

    dashboard.post("/surveys/s1/broadcast", json={"dry_run": False})
    inbox = sandbox.get("/sim/api/inbox", params={"phone": SANDBOX_PHONE, "since": 0}).json()

    [message] = inbox["messages"]
    assert SURVEY["question"] in message["text"]
    assert "1. Yes" in message["text"]

    replies = sandbox.post("/sim/api/message", json={"phone": SANDBOX_PHONE, "body": "1"}).json()["replies"]
    assert any("Thank you" in r["text"] for r in replies)

    [answer] = db.data["survey_responses"].values()
    assert answer["selected_option"] == "Yes"
    assert answer["simulated"] is True, "sandbox answers must be distinguishable"

    [listed] = dashboard.get("/surveys").json()
    assert listed["results"] == {"Yes": 1}
    assert listed["sandbox_response_count"] == 1


def test_the_inbox_only_returns_what_arrived_since_the_last_check(dashboard, sandbox, db):
    dashboard.post("/surveys/s1/broadcast", json={"dry_run": False})
    first = sandbox.get("/sim/api/inbox", params={"phone": SANDBOX_PHONE, "since": 0}).json()

    again = sandbox.get("/sim/api/inbox", params={"phone": SANDBOX_PHONE, "since": first["latest"]}).json()

    assert len(first["messages"]) == 1
    assert again["messages"] == []


def test_a_new_chat_starts_from_the_server_clock_not_its_own(dashboard, sandbox, db, monkeypatch):
    """Found live: this laptop runs a few seconds ahead of Google's servers, so a
    chat that used its own clock skipped a survey sent just after it opened."""
    monkeypatch.setattr(sim_api.time, "time", lambda: 1000.0)
    start = sandbox.get("/sim/api/inbox", params={"phone": SANDBOX_PHONE}).json()
    assert start == {"messages": [], "latest": 1000.0}

    db.data.setdefault("sim_inbox", {})["b1"] = {"kind": "survey", "survey_id": "s1",
                                                 "text": "Survey", "sent_epoch": 1000.5}
    after = sandbox.get("/sim/api/inbox", params={"phone": SANDBOX_PHONE, "since": start["latest"]}).json()

    assert [m["text"] for m in after["messages"]] == ["Survey"]


def test_a_real_citizens_answer_is_not_tagged(db):
    record = sim.tag_response({"survey_id": "s1", "sender": REAL_CITIZENS[0], "selected_option": "No"})

    assert "simulated" not in record


def test_sandbox_answers_do_not_inflate_the_real_response_rate(dashboard, sandbox, db):
    approve(db, REAL_CITIZENS[0])
    dashboard.post("/surveys/s1/broadcast", json={"dry_run": False})
    sandbox.get("/sim/api/inbox", params={"phone": SANDBOX_PHONE, "since": 0})
    sandbox.post("/sim/api/message", json={"phone": SANDBOX_PHONE, "body": "2"})

    [listed] = dashboard.get("/surveys").json()

    assert listed["response_count"] == 1
    assert listed["response_rate"] == 0.0, "one real recipient, no real answers yet"


def test_the_sandbox_shares_only_survey_collections_with_the_dashboard():
    class Recorder:
        def __init__(self):
            self.names = []

        def collection(self, name):
            self.names.append(name)
            return name

    raw = Recorder()
    ns = sim.NamespacedDB(raw)
    for name in ("surveys", "survey_responses", "messages", "sessions", "inbox"):
        ns.collection(name)

    assert raw.names == ["surveys", "survey_responses", "sim_messages", "sim_sessions", "sim_inbox"]


# --- choosing the audience on the map ------------------------------------------

def test_a_map_circle_targets_only_citizens_who_reported_inside_it(dashboard, db):
    db.data["messages"]["r0"].update({"latitude": 26.8467, "longitude": 80.9462})  # Hazratganj
    db.data["messages"]["r1"].update({"latitude": 26.8534, "longitude": 80.9981})  # Gomti Nagar, about 5 km east

    preview = dashboard.post("/surveys/audience/preview", json={
        "areas": [{"lat": 26.8467, "lng": 80.9462, "radius_km": 1}],
    }).json()

    assert preview["eligible_recipient_count"] == 1
    assert preview["label"] == "1 map area"


def test_reports_without_a_location_never_match_a_map_area(dashboard, db):
    preview = dashboard.post("/surveys/audience/preview", json={
        "areas": [{"lat": 26.85, "lng": 80.95, "radius_km": 15}],
    }).json()

    assert preview["eligible_recipient_count"] == 0


def test_the_preview_says_how_many_real_numbers_are_approved(dashboard, db):
    approve(db, REAL_CITIZENS[1])

    assert dashboard.post("/surveys/audience/preview", json={}).json()["whatsapp_recipients"] == 1


def test_an_absurd_circle_is_refused(dashboard):
    response = dashboard.post("/surveys/audience/preview", json={
        "areas": [{"lat": 26.85, "lng": 80.95, "radius_km": 500}],
    })

    assert response.status_code == 422
