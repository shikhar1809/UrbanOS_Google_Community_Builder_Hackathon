from fastapi.testclient import TestClient

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


def test_status_is_open_when_no_access_code_configured(monkeypatch):
    monkeypatch.delenv("SIM_ACCESS_CODE", raising=False)
    client = TestClient(sim_app)

    response = client.get("/sim/api/status")

    assert response.status_code == 200
    assert response.json()["access_code_required"] is False


def test_access_code_gates_api_routes(monkeypatch):
    monkeypatch.setenv("SIM_ACCESS_CODE", "let-me-in")
    client = TestClient(sim_app)

    assert client.get("/sim/api/status").status_code == 401
    assert client.get("/sim/api/status", headers={"X-Sim-Code": "wrong"}).status_code == 401
    assert client.get("/sim/api/status", headers={"X-Sim-Code": "let-me-in"}).status_code == 200


def test_bad_input_is_rejected_before_touching_storage(monkeypatch):
    monkeypatch.delenv("SIM_ACCESS_CODE", raising=False)
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
