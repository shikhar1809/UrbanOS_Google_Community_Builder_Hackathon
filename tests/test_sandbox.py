import base64
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import analytics
import main
import planning_review
import sim
import sim_api
import worker
from sim_api import sim_app


class FakeDB:
    def __init__(self):
        self.names = []

    def collection(self, name):
        self.names.append(name)
        return name


def test_namespaced_db_prefixes_every_collection():
    fake = FakeDB()
    ns = sim.NamespacedDB(fake)

    assert ns.collection("messages") == "sim_messages"
    assert ns.collection("sessions") == "sim_sessions"
    assert fake.names == ["sim_messages", "sim_sessions"]


def test_outbound_messages_are_captured_inside_sandbox_session_only():
    outbox = []
    assert sim.outbound_sink() is None

    with sim.session(FakeDB(), outbox):
        worker.send_whatsapp_message("whatsapp:+919900000003", "hello")

    assert outbox == ["hello"]
    assert sim.outbound_sink() is None


def test_twiml_bodies_extracts_each_message():
    xml = b"<Response><Message>one</Message><Message>two &amp; three</Message></Response>"
    assert sim_api._twiml_bodies(xml) == ["one", "two & three"]
    assert sim_api._twiml_bodies(b"<Response></Response>") == []


def test_the_chat_needs_no_code():
    client = TestClient(sim_app)

    assert client.get("/sim/api/status").status_code == 200
    # 422: stopped by input validation, not by a gate.
    assert client.post("/sim/api/message", json={"phone": "bad"}).status_code == 422
    assert client.post("/sim/api/media", json={"content_type": "text/html", "data": "AAAA"}).status_code == 422
    assert client.post("/sim/api/reset", json={"phone": "bad"}).status_code == 422


def test_the_chat_writes_only_to_sandbox_collections(monkeypatch):
    """What makes leaving it open acceptable."""
    from tests.fakes import FakeFirestore
    db = FakeFirestore()
    monkeypatch.setattr(sim_api, "firestore_db", db)
    client = TestClient(sim_app)

    assert client.post("/sim/api/message", json={"phone": "919900000003", "body": "hi"}).status_code == 200

    assert db.data, "the message should have been processed"
    assert all(name.startswith("sim_") for name in db.data)


def test_bad_input_is_rejected_before_touching_storage(monkeypatch):
    client = TestClient(sim_app)

    bad_phone = client.post("/sim/api/message", json={"phone": "not-a-number", "body": "hi"})
    bad_media = client.post(
        "/sim/api/message",
        json={"phone": "919900000003", "media_id": "a" * 32, "media_type": "video/mp4"},
    )
    bad_upload = client.post("/sim/api/media", json={"content_type": "image/png", "data": "!!!not base64"})
    wrong_type = client.post("/sim/api/media", json={"content_type": "text/html", "data": "AAAA"})

    assert bad_phone.status_code == 422
    assert bad_media.status_code == 422
    assert bad_upload.status_code == 422
    assert wrong_type.status_code == 422


def test_sandbox_app_exposes_only_sim_routes():
    paths = {route.path for route in sim_app.routes}

    assert paths, "sandbox app has no routes"
    assert all(path.startswith("/sim/") for path in paths)


def test_text_and_hinglish_voice_with_photo_reach_the_same_gps_recommendation(monkeypatch):
    from tests.fakes import FakeFirestore

    db = FakeFirestore()
    monkeypatch.setattr(sim_api, "firestore_db", db)
    monkeypatch.setattr(main, "gemini_client", None)
    monkeypatch.setattr(main, "_groq_client", None)
    monkeypatch.setattr(main.LLM, "get_provider", lambda: None)
    observed_audio = []

    def upload(file):
        observed_audio.append(Path(file).read_bytes())
        return SimpleNamespace(name="fixture-voice")

    monkeypatch.setattr(worker, "gemini_client", SimpleNamespace(
        files=SimpleNamespace(upload=upload, delete=lambda **_: None),
        models=SimpleNamespace(generate_content=lambda **_: SimpleNamespace(
            text="Jankipuram ki road par sewage overflow ho raha hai.")),
    ))
    client = TestClient(sim_app)

    def asset(mime, data):
        response = client.post("/sim/api/media", json={
            "content_type": mime, "data": base64.b64encode(data).decode(),
        })
        assert response.status_code == 200
        return response.json()["id"]

    def send(phone, **fields):
        response = client.post("/sim/api/message", json={"phone": phone, **fields})
        assert response.status_code == 200

    audio = asset("audio/ogg", b"mocked Ogg fixture")
    photo = asset("image/png", b"mocked image fixture")
    plain, multimedia = "999000001001", "999000001002"
    for phone in (plain, multimedia):
        send(phone, body="new report")
        if phone == plain:
            send(phone, body="Sewage overflow is blocking the road in Jankipuram.")
        else:
            send(phone, media_id=audio, media_type="audio/ogg")
        send(phone, latitude=26.919486, longitude=80.944970)
        if phone == multimedia:
            send(phone, media_id=photo, media_type="image/png")
        send(phone, body="skip")

    assert observed_audio == [b"mocked Ogg fixture"]
    reports = [analytics.normalize_record(key, data) for key, data in db.data["sim_messages"].items()]
    assert {report["semantic_tag"] for report in reports} == {"sewage_overflow"}
    assert {tuple(report["intake_formats"]) for report in reports} == {
        ("Text", "GPS pin"), ("Voice", "Photo", "GPS pin"),
    }
    [project], report_count = planning_review.recommendations(reports, "demo")
    assert report_count == project["unique_reporters"] == 2
