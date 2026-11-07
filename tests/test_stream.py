"""The live stream writes to the real reports collection, so these pin both that
it really goes through the production intake pipeline and that it can never
reach a real person or stay behind after a demo.
"""

import random

import pytest
from fastapi.testclient import TestClient

import guardrails
import main
import sim
import sim_api
import sim_stream
import triage
import triage_rules
from tests.fakes import FakeFirestore


@pytest.fixture(autouse=True)
def no_models(monkeypatch):
    """No model is reachable, which is the state production is in today."""
    monkeypatch.setattr(main, "gemini_client", None)
    monkeypatch.setattr(main, "_groq_client", None)
    monkeypatch.setattr(main.LLM, "get_provider", lambda *a, **k: None)
    monkeypatch.setattr(main.JOBS, "backend", lambda: main.JOBS.BACKEND_INLINE)
    triage.reset_model_cooldown()
    yield
    triage.reset_model_cooldown()


@pytest.fixture
def db():
    return FakeFirestore()


def event(**overrides):
    base = {
        "kind": "citizen", "name": "Sunita", "phone": "+999123456789",
        "text": "Sewage is overflowing onto the road in Gomti Nagar.", "language": "English",
        "place": "Gomti Nagar", "lat": 26.8534, "lng": 80.9981, "location_label": "GPS pin, Gomti Nagar",
    }
    base.update(overrides)
    return base


def run_event(db, **overrides):
    return sim_stream.process_event(event(**overrides), main._process_whatsapp_sync, db, "run1")


def reports(db):
    return list(db.data.get("messages", {}).values())


# --- the pipeline ------------------------------------------------------------

def test_a_citizen_is_filed_by_the_production_pipeline(db):
    [result] = run_event(db)

    assert result["outcome"] == "filed"
    assert result["reference_id"].startswith("UO"), "the reference the real pipeline issues"
    assert result["tag"] == "sewage_overflow"
    assert "Jal Sansthan" in result["department"]
    assert result["ward"].startswith("Ward ")
    assert result["reply"], "the citizen was answered"

    [stored] = reports(db)
    assert stored["reference_id"] == result["reference_id"]
    assert stored["constituency_zone"] == "East"


def test_a_filed_report_carries_its_provenance(db):
    run_event(db)

    [stored] = reports(db)
    assert stored["simulated"] is True
    assert stored["stream_run"] == "run1"
    assert stored["triage_source"] == "rules"
    assert stored["triage_status"] == "failed", "stays eligible for a real model to re-triage"


def test_conversation_state_stays_out_of_real_collections(db):
    run_event(db)

    assert "sessions" not in db.data
    assert "rate_limits" not in db.data
    assert db.data["stream_rate_limits"], "spam guard ran, in its own namespace"
    assert not db.data.get("stream_sessions"), "a finished citizen leaves no open session"


def test_a_real_report_is_not_tagged(db):
    """tag_report must be inert outside the stream."""
    report = {"body": "x"}
    sim.tag_report(report)
    assert report == {"body": "x"}


# --- the guardrails ----------------------------------------------------------

def test_a_pin_in_another_city_is_refused(db):
    [result] = run_event(db, kind="out_of_area_pin", lat=26.4499, lng=80.3319, place="Kanpur")

    assert result["outcome"] == "rejected"
    assert result["reason"] == "out_of_area"
    assert reports(db) == []


def test_text_naming_another_city_is_refused(db):
    e = event(kind="out_of_area_text", location_text="Kanpur", place="Kanpur", location_label="Kanpur")
    e.pop("lat")
    e.pop("lng")

    [result] = sim_stream.process_event(e, main._process_whatsapp_sync, db, "run1")

    assert result["outcome"] == "rejected"
    assert reports(db) == []


def test_off_topic_is_refused_with_no_model_available(db):
    [result] = run_event(db, kind="off_topic", text="What is the cricket score today?")

    assert result["outcome"] == "rejected"
    assert result["reason"] == "off_topic"
    assert reports(db) == []


def test_a_repeat_from_the_same_citizen_is_refused(db):
    first, second = run_event(db, kind="duplicate")

    assert first["outcome"] == "filed"
    assert second["outcome"] == "rejected"
    assert second["reason"] == "duplicate"
    assert len(reports(db)) == 1


def test_one_failing_citizen_does_not_raise(db):
    def explode(*a, **k):
        raise RuntimeError("boom")

    [result] = sim_stream.process_event(event(), explode, db, "run1")

    assert result["outcome"] == "error"


# --- nobody real can be contacted --------------------------------------------

def test_generated_senders_are_all_on_the_reserved_prefix():
    rng = random.Random(7)
    for key in sim_stream.SCENARIOS:
        for _ in range(100):
            phone = sim_stream.generate_event(key, rng)["phone"]
            assert phone.startswith("+999") and len(phone) == 13


def test_simulated_senders_are_recognised():
    assert sim.is_simulated_sender("whatsapp:+999123456789")
    assert not sim.is_simulated_sender("whatsapp:+919800000000")
    assert not sim.is_simulated_sender(None)


def test_sanction_notifications_skip_simulated_citizens(monkeypatch):
    sent = []

    class Messages:
        def create(self, **kw):
            sent.append(kw["to"])

    class FakeTwilio:
        def __init__(self, *a):
            self.messages = Messages()

    monkeypatch.setattr(main, "Client", FakeTwilio)
    monkeypatch.setattr(main, "TWILIO_ACCOUNT_SID", "x")
    monkeypatch.setattr(main, "TWILIO_AUTH_TOKEN", "x")
    monkeypatch.setattr(main, "TWILIO_WHATSAPP_NUMBER", "x")

    count = main.notify_sanctioned(["whatsapp:+999123456789", "whatsapp:+919800000000"], "t", "p")

    assert sent == ["whatsapp:+919800000000"]
    assert count == 1


def test_the_stream_does_not_bind_twilio():
    """If it cannot hold the credentials it cannot send, whatever else goes wrong."""
    import inspect
    source = inspect.getsource(main)
    decorator = source.split("def sandbox(req")[0].rsplit("@https_fn.on_request(", 1)[1]
    assert "TWILIO" not in decorator


# --- generation --------------------------------------------------------------

def test_a_surge_concentrates_on_its_zone_and_issues():
    rng = random.Random(1)
    events = [sim_stream.generate_event("monsoon", rng) for _ in range(300)]
    east = {loc.name for loc in triage_rules.GAZETTEER if loc.zone == "East"}
    citizens = [e for e in events if e["kind"] == "citizen"]

    assert sum(e["place"] in east for e in citizens) / len(citizens) > 0.7


def test_adversarial_traffic_is_mostly_noise_for_the_guardrails():
    rng = random.Random(1)
    events = [sim_stream.generate_event("adversarial", rng) for _ in range(500)]

    assert 0.3 < sum(e["kind"] != "citizen" for e in events) / 500 < 0.5


def test_every_complaint_template_is_understood_by_the_rules():
    """A template the rules cannot read would land as 'general_civic' and make the
    clustering look worse than it is."""
    for tag, templates in sim_stream.COMPLAINTS.items():
        for _lang, template in templates:
            result = triage_rules.classify(template.format(place="Hazratganj"))
            assert result["semantic_tag"] == tag, template


# --- run lifecycle -----------------------------------------------------------

def start(db, **kw):
    params = {"scenario": "steady", "rate_per_min": 30, "max_events": 300}
    params.update(kw)
    return sim_stream.start(db, **params)


def tick(db, run_id, batch=1):
    return sim_stream.tick(db, main._process_whatsapp_sync, run_id, batch, random.Random(3))


def test_a_tick_files_reports_and_updates_the_run(db):
    run = start(db)

    out = tick(db, run["run_id"], batch=3)

    assert len(out["results"]) >= 3
    state = sim_stream.read_state(db)
    assert state["events"] == len(out["results"])
    assert state["filed"] + state["rejected"] == state["events"]
    assert state["live"] is True
    assert state["avg_pipeline_ms"] is not None
    assert state["recent"], "the console reads recent events from the run document"


def test_clusters_accumulate_across_ticks(db):
    run = start(db, scenario="monsoon")
    for _ in range(4):
        sim_stream._control(db).update({"last_tick_at": 0})
        tick(db, run["run_id"], batch=5)

    clusters = sim_stream.read_state(db)["clusters"]
    assert clusters and clusters[0]["count"] >= 2


def test_batch_size_is_capped_on_the_server(db):
    run = start(db)

    out = sim_stream.tick(db, main._process_whatsapp_sync, run["run_id"], 100, random.Random(9))

    assert len([r for r in out["results"]]) <= sim_stream.MAX_BATCH * 2, "a duplicate may add one result each"


def test_rate_is_clamped_on_the_server(db):
    assert start(db, rate_per_min=100000)["rate_per_min"] == sim_stream.MAX_RATE


def test_a_second_stream_cannot_start_over_a_live_one(db):
    start(db)
    with pytest.raises(sim_stream.StreamError) as e:
        start(db)
    assert e.value.status == 409


def test_a_stream_whose_tab_closed_goes_stale_and_can_be_replaced(db):
    start(db)
    sim_stream._control(db).update({"last_tick_at": 0})

    assert sim_stream.read_state(db)["live"] is False
    assert start(db)["live"] is True


def test_a_stopped_run_refuses_ticks(db):
    run = start(db)
    sim_stream.stop(db)
    with pytest.raises(sim_stream.StreamError):
        tick(db, run["run_id"])


def test_a_stale_run_id_refuses_ticks(db):
    start(db)
    with pytest.raises(sim_stream.StreamError):
        tick(db, "not-this-run")


def test_ticking_too_fast_is_refused(db):
    run = start(db)
    tick(db, run["run_id"])
    with pytest.raises(sim_stream.StreamError) as e:
        tick(db, run["run_id"])
    assert e.value.status == 429


def test_the_run_ends_itself_at_its_budget(db):
    run = start(db, max_events=2)

    out = tick(db, run["run_id"], batch=5)

    assert out["state"]["status"] == "completed"
    with pytest.raises(sim_stream.StreamError):
        tick(db, run["run_id"])


# --- clean up ----------------------------------------------------------------

def test_purge_removes_only_simulated_reports(db):
    db.collection("messages").document("real").set({"reference_id": "UOREAL", "simulated": False})
    db.collection("messages").document("legacy").set({"reference_id": "REF-1"})
    run_event(db)
    assert len(reports(db)) == 3

    out = sim_stream.purge(db)

    assert out["removed_reports"] == 1
    assert {r["reference_id"] for r in reports(db)} == {"UOREAL", "REF-1"}
    assert not db.data.get("stream_rate_limits")
    assert sim_stream.read_state(db)["status"] == "idle"


def test_purge_refuses_while_a_run_is_live(db):
    start(db)
    with pytest.raises(sim_stream.StreamError):
        sim_stream.purge(db)


# --- http --------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch, db):
    monkeypatch.setattr(sim_api, "firestore_db", db)
    monkeypatch.setenv("SIM_ACCESS_CODE", "let-me-in")
    return TestClient(sim_api.sim_app)


AUTH = {"X-Sim-Code": "let-me-in"}


def test_stream_controls_require_the_code(client):
    for path in ("start", "stop", "purge"):
        assert client.post(f"/sim/api/stream/{path}", json={}).status_code == 401
    assert client.post("/sim/api/stream/tick", json={"run_id": "x"}).status_code == 401


def test_stream_controls_fail_closed_with_no_code_configured(client, monkeypatch):
    """The chat opens up when no code is set. These must not, because they write
    to the real collection."""
    monkeypatch.delenv("SIM_ACCESS_CODE")
    assert client.post("/sim/api/stream/start", json={}).status_code == 503


def test_watching_is_open_and_exposes_no_real_number(client, db):
    run = client.post("/sim/api/stream/start", json={"scenario": "steady"}, headers=AUTH).json()
    client.post("/sim/api/stream/tick", json={"run_id": run["run_id"], "batch": 2}, headers=AUTH)

    response = client.get("/sim/api/stream/state")

    assert response.status_code == 200
    body = response.json()
    assert body["events"] >= 2
    assert {s["key"] for s in body["scenarios"]} == set(sim_stream.SCENARIOS)
    for r in body["recent"]:
        assert r["phone"].startswith("+999 ••••")


def test_an_unknown_scenario_is_rejected(client):
    assert client.post("/sim/api/stream/start", json={"scenario": "nope"}, headers=AUTH).status_code == 422


def test_full_http_lifecycle(client):
    run = client.post("/sim/api/stream/start", json={"scenario": "monsoon", "rate_per_min": 60}, headers=AUTH).json()
    assert run["live"] is True

    ticked = client.post("/sim/api/stream/tick", json={"run_id": run["run_id"], "batch": 2}, headers=AUTH)
    assert ticked.status_code == 200

    assert client.post("/sim/api/stream/stop", headers=AUTH).json()["status"] == "stopped"
    assert client.post("/sim/api/stream/purge", headers=AUTH).status_code == 200
    assert client.get("/sim/api/stream/state").json()["events"] == 0


# --- data files --------------------------------------------------------------

def run_with_noise(client):
    run = client.post("/sim/api/stream/start", json={"scenario": "adversarial"}, headers=AUTH).json()
    for _ in range(4):
        sim_stream._control(sim_api.firestore_db).update({"last_tick_at": 0})
        client.post("/sim/api/stream/tick", json={"run_id": run["run_id"], "batch": 5}, headers=AUTH)
    return run["run_id"]


def test_a_file_holds_every_result_including_refusals(client):
    """More than the run document's short tail, and refused messages too, which
    never reach the reports collection."""
    run_id = run_with_noise(client)

    state = client.get("/sim/api/stream/state").json()
    rows = client.get(f"/sim/api/stream/files/{run_id}").json()["rows"]

    assert len(rows) == state["events"]
    assert sum(r["outcome"] == "filed" for r in rows) == state["filed"]
    assert sum(r["outcome"] == "rejected" for r in rows) == state["rejected"] > 0


def test_runs_are_listed_newest_first_with_totals(client):
    first = run_with_noise(client)
    client.post("/sim/api/stream/stop", headers=AUTH)
    second = run_with_noise(client)

    files = client.get("/sim/api/stream/files").json()["files"]

    assert [f["run_id"] for f in files] == [second, first]
    assert files[1]["status"] == "stopped"
    assert files[0]["events"] > 0


def test_csv_download(client):
    run_id = run_with_noise(client)

    response = client.get(f"/sim/api/stream/files/{run_id}?format=csv")

    assert response.headers["content-type"].startswith("text/csv")
    assert f"urbanos-stream-{run_id}.csv" in response.headers["content-disposition"]
    lines = response.text.strip().splitlines()
    assert lines[0].startswith("at,outcome,kind")
    assert len(lines) > 1


def test_jsonl_download_is_one_object_per_line(client):
    import json
    run_id = run_with_noise(client)

    response = client.get(f"/sim/api/stream/files/{run_id}?format=jsonl")

    rows = [json.loads(line) for line in response.text.strip().splitlines()]
    assert rows and all("outcome" in r for r in rows)


def test_files_hold_no_real_or_unmasked_number(client):
    run_id = run_with_noise(client)

    text = client.get(f"/sim/api/stream/files/{run_id}?format=jsonl").text

    assert "whatsapp:" not in text
    for row in client.get(f"/sim/api/stream/files/{run_id}").json()["rows"]:
        assert row["phone"].startswith("+999 ••••")


def test_an_unknown_or_malformed_run_is_refused(client):
    assert client.get("/sim/api/stream/files/0123456789").status_code == 404
    assert client.get("/sim/api/stream/files/not-a-run").status_code == 422


def test_purge_removes_the_files_too(client):
    run_with_noise(client)
    client.post("/sim/api/stream/stop", headers=AUTH)

    client.post("/sim/api/stream/purge", headers=AUTH)

    assert client.get("/sim/api/stream/files").json()["files"] == []


# --- triage fallback ---------------------------------------------------------

@pytest.mark.parametrize("text,tag,dept", [
    ("Transformer is sparking dangerously near the school gate", "electrical_hazard", "LESA"),
    ("Street lights have not worked on our lane for two weeks", "street_lighting", "LESA"),
    ("No water supply in our area for the last four days", "water_supply", "Jal Sansthan"),
    ("pani nahi aa raha hai teen din se", "water_supply", "Jal Sansthan"),
    ("सीवर का पानी सड़क पर बह रहा है", "sewage_overflow", "Jal Sansthan"),
    ("Pothole on the inner lane of our mohalla", "road_repair", "Nagar Nigam"),
    ("Big pothole on the Shaheed Path flyover", "road_repair", "PWD"),
    ("Illegal construction next door", "illegal_construction", "LDA"),
    ("Traffic signal is dead at the crossing", "traffic_signal", "Traffic Police"),
])
def test_rules_follow_the_prompts_documented_routing(text, tag, dept):
    result = triage_rules.classify(text)
    assert result["semantic_tag"] == tag
    assert dept in result["responsible_body"]


def test_rules_rank_a_hazard_above_a_comfort_request():
    wire = triage_rules.classify("Live wire hanging where children walk")
    bench = triage_rules.classify("Please add more benches to the park")
    assert wire["priority"] == "Critical"
    assert bench["priority"] == "Low"


def test_rules_admit_when_they_do_not_understand():
    result = triage_rules.classify("Something is wrong in my area")
    assert result["semantic_tag"] == "general_civic"
    assert result["category"] == "Other"


def test_rules_place_a_text_location_on_the_map():
    result = triage_rules.classify("Garbage everywhere", "near the market, Aliganj")
    assert result["constituency_zone"] == "North"
    assert result["estimated_latitude"] == pytest.approx(26.893, abs=0.01)


def test_a_gps_pin_is_not_overridden_by_the_gazetteer():
    result = triage_rules.classify("Garbage everywhere", "", lat=26.85, lng=81.0)
    assert result["constituency_zone"] == "East"
    assert "estimated_latitude" not in result


class Refuses:
    name = "gemini"

    def __init__(self, message):
        self.calls = 0
        self.message = message

    def classify(self, *a, **k):
        self.calls += 1
        raise RuntimeError(self.message)


def test_a_billing_refusal_falls_back_and_backs_off():
    client = Refuses("402 RESOURCE_EXHAUSTED prepayment credits are depleted")

    first = triage.run_triage(client, "Garbage in Aliganj", rules_fallback=True)
    second = triage.run_triage(client, "Garbage in Aliganj", rules_fallback=True)

    assert first["ok"] is False
    assert first["triage_source"] == "rules"
    assert first["semantic_tag"] == "garbage_collection"
    assert second["triage_source"] == "rules"
    assert client.calls == 1, "the second report must not wait on a model known to be refusing"


def test_an_ordinary_model_error_does_not_trigger_the_backoff():
    client = Refuses("connection reset")

    triage.run_triage(client, "Garbage in Aliganj", rules_fallback=True)
    triage.run_triage(client, "Garbage in Aliganj", rules_fallback=True)

    assert client.calls == 2


def test_a_model_answer_is_recorded_as_such():
    class Parsed:
        category = "Other"
        priority = "Low"
        feasibility = "Unknown"
        extracted_location = ""
        summary = "s"
        original_language = "English"
        constituency_zone = "Central"
        estimated_budget = 1
        responsible_body = "x"
        ward_no = "Ward 2"
        ward_officer = "y"
        semantic_tag = "t"
        visual_evidence = ""

    class Answers:
        name = "groq"

        def classify(self, *a, **k):
            return Parsed()

    assert triage.run_triage(Answers(), "x", rules_fallback=True)["triage_source"] == "model:groq"


def test_off_topic_phrases_do_not_catch_civic_complaints():
    assert guardrails.check_topic_text("Road near the cricket stadium is broken")["verdict"] == "accept"
    assert guardrails.check_topic_text("Water leak in the movie hall lane")["verdict"] == "accept"
    assert guardrails.check_topic_text("What is the cricket score?")["verdict"] == "reject_topic"
