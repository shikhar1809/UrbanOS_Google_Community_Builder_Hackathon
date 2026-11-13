import base64
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx
from fastapi.testclient import TestClient

import main
import sim_api
import triage
import worker
from tests.fakes import FakeFirestore


@pytest.fixture
def sandbox(monkeypatch):
    db = FakeFirestore()
    monkeypatch.setattr(sim_api, "firestore_db", db)
    monkeypatch.setattr(main, "gemini_client", None)
    monkeypatch.setattr(main, "_groq_client", None)
    monkeypatch.setattr(main.LLM, "get_provider", lambda: None)
    monkeypatch.setenv("TRIAGE_BACKEND", "inline")
    triage.reset_model_cooldown()
    with TestClient(sim_api.sim_app) as client:
        yield client, db
    triage.reset_model_cooldown()


def upload(client, content_type, contents):
    response = client.post("/sim/api/media", json={
        "content_type": content_type, "data": base64.b64encode(contents).decode(),
    })
    assert response.status_code == 200
    return {"media_id": response.json()["id"], "media_type": content_type}


def send(client, phone="999000003010", **message):
    response = client.post("/sim/api/message", json={"phone": phone, **message})
    assert response.status_code == 200, response.text
    return "\n".join(reply["text"] for reply in response.json()["replies"])


def test_photo_without_caption_can_file_after_a_valid_location(sandbox, monkeypatch):
    client, db = sandbox
    photo = upload(client, "image/png", b"test anganwadi evidence")
    seen = []

    class VisionModel:
        name = "fixture-vision"
        supports_photo_only = True

        def classify(self, prompt, schema, image=None):
            seen.append((prompt, image.data))
            return schema(category="Construction Development", priority="Medium", sentiment="Unknown",
                          extracted_location="", summary="Unfinished building wall", original_language="Unknown",
                          constituency_zone="North", estimated_budget=0, responsible_body="Needs triage",
                          semantic_tag="unfinished_building", visual_evidence="Unfinished masonry and exposed columns")

    monkeypatch.setattr(main.LLM, "get_provider", lambda: VisionModel())
    send(client, **photo)
    assert not db.data.get("sim_messages")
    receipt = send(client, latitude=26.919486, longitude=80.944970)
    [record] = db.data["sim_messages"].values()
    assert record["reference_id"] in receipt
    assert record["intake_formats"] == ["Photo", "GPS pin"]
    assert record["body"] == "" and record["original_language"] is None
    assert record["triage_source"] == "model:fixture-vision"
    assert record["visual_evidence"] == "Unfinished masonry and exposed columns"
    assert "without a description" in seen[0][0]
    assert seen[0][1] == b"test anganwadi evidence"


def test_photo_only_stays_in_review_without_vision_and_rejects_outside_lucknow(sandbox):
    client, db = sandbox
    photo = upload(client, "image/png", b"test photo")
    send(client, **photo)
    send(client, latitude=28.61, longitude=77.21)
    assert not db.data.get("sim_messages")
    send(client, phone="999000003011", **photo)
    receipt = send(client, phone="999000003011", latitude=26.919486, longitude=80.944970)
    [record] = db.data["sim_messages"].values()
    assert "saved for review" in receipt
    assert record["triage_status"] == "failed"
    assert record["responsible_body"] == "Needs triage"
    assert record["priority"] is None
    assert record["media_url"] and record["photo_only"]


def test_voice_first_preserves_awadhi_transcript_through_shared_intake(sandbox, monkeypatch):
    client, db = sandbox
    audio = upload(client, "audio/ogg", b"stub recording bytes")
    transcript = "हमरे मोहल्ले मा पानी भर गवा है।"
    observed = {}

    def transcribe(**kwargs):
        observed["prompt"] = kwargs["contents"][1]
        return SimpleNamespace(text=transcript)

    def upload_file(file):
        observed["audio"] = Path(file).read_bytes()
        return SimpleNamespace(name="local-audio")

    monkeypatch.setattr(worker, "gemini_client", SimpleNamespace(
        files=SimpleNamespace(upload=upload_file, delete=lambda **_: None),
        models=SimpleNamespace(generate_content=transcribe),
    ))
    assert transcript in send(client, **audio)
    send(client, latitude=26.919486, longitude=80.944970)
    send(client, body="skip")
    [record] = db.data["sim_messages"].values()
    assert record["body"] == transcript
    assert record["intake_formats"] == ["Voice", "GPS pin"]
    assert observed["audio"] == b"stub recording bytes"
    assert "Awadhi" in observed["prompt"]


def test_photo_only_will_not_use_a_text_only_provider():
    class TextModel:
        def classify(self, *_args, **_kwargs):
            pytest.fail("A text-only provider cannot classify a photo-only report")

    result = triage.run_triage(TextModel(), "", image_part=object(), rules_fallback=True, photo_only=True)
    assert result["ok"] is False


def test_media_download_keeps_twilio_credentials_on_the_original_host(monkeypatch):
    seen = []

    def handle(request):
        seen.append((request.url.host, "authorization" in request.headers))
        if request.url.host == "api.twilio.com":
            return httpx.Response(307, headers={"location": "https://media.twiliocdn.com/example"})
        return httpx.Response(200, content=b"photo")

    client = httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(main.httpx, "Client", lambda **_: client)
    monkeypatch.setattr(main, "TWILIO_ACCOUNT_SID", "local-sid")
    monkeypatch.setattr(main, "TWILIO_AUTH_TOKEN", "local-token")
    with pytest.raises(ValueError, match="Twilio API"):
        main.download_twilio_media("https://example.invalid/private", 1024)
    assert main.download_twilio_media("https://api.twilio.com/Media/asset", 1024) == b"photo"
    assert seen == [("api.twilio.com", True), ("media.twiliocdn.com", False)]
