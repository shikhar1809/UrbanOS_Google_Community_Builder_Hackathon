"""Webhook-level behaviour: signature enforcement and duplicate delivery.

These drive the real endpoint, so they cover the wiring between the route, the
signature check and the idempotency claim — not just the helpers in isolation.
"""

import pytest
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

import idempotency
import main
from tests.fakes import FakeFirestore

REPLY = "<Response><Message>Logged</Message></Response>"


class StubValidator:
    """Stands in for twilio.request_validator.RequestValidator."""

    def __init__(self, accept: bool):
        self.accept = accept

    def validate(self, url, post_vars, signature):
        return self.accept


@pytest.fixture
def db():
    return FakeFirestore()


@pytest.fixture
def calls(monkeypatch):
    """Count pipeline runs, so a duplicate that reaches the pipeline is visible."""
    seen = []

    def fake_pipeline(form_data, background_tasks, _db):
        seen.append(dict(form_data))
        return HTMLResponse(content=REPLY, media_type="application/xml")

    monkeypatch.setattr(main, "_process_whatsapp_sync", fake_pipeline)
    return seen


@pytest.fixture
def client(db):
    main.app.dependency_overrides[main.get_db] = lambda: db
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def post(client, **fields):
    form = {"From": "whatsapp:+919812345678", "Body": "hi"}
    form.update(fields)
    return client.post("/webhook/whatsapp", data=form)


def test_request_is_processed_when_signature_checking_is_not_configured(client, calls, monkeypatch):
    monkeypatch.setattr(main, "twilio_validator", None)

    response = post(client, MessageSid="SM-unsigned")

    assert response.status_code == 200
    assert len(calls) == 1


def test_forged_request_is_rejected(client, calls, monkeypatch):
    monkeypatch.setattr(main, "twilio_validator", StubValidator(accept=False))
    monkeypatch.setattr(main, "TWILIO_VALIDATE_SIGNATURE", True)

    response = post(client, MessageSid="SM-forged")

    assert response.status_code == 403
    assert calls == [], "a forged request must never reach the pipeline"


def test_valid_signature_is_accepted(client, calls, monkeypatch):
    monkeypatch.setattr(main, "twilio_validator", StubValidator(accept=True))
    monkeypatch.setattr(main, "TWILIO_VALIDATE_SIGNATURE", True)

    response = post(client, MessageSid="SM-signed")

    assert response.status_code == 200
    assert len(calls) == 1


def test_signature_checking_can_be_disabled_for_debugging(client, calls, monkeypatch):
    monkeypatch.setattr(main, "twilio_validator", StubValidator(accept=False))
    monkeypatch.setattr(main, "TWILIO_VALIDATE_SIGNATURE", False)

    response = post(client, MessageSid="SM-disabled")

    assert response.status_code == 200
    assert len(calls) == 1


def test_redelivery_does_not_file_a_second_report(client, calls, db, monkeypatch):
    monkeypatch.setattr(main, "twilio_validator", None)

    first = post(client, MessageSid="SM-retry")
    second = post(client, MessageSid="SM-retry")

    assert first.status_code == second.status_code == 200
    assert len(calls) == 1, "Twilio's retry must not run the pipeline twice"
    assert second.text == REPLY, "the retry should replay the original reply"


def test_different_messages_are_both_processed(client, calls, monkeypatch):
    monkeypatch.setattr(main, "twilio_validator", None)

    post(client, MessageSid="SM-a", Body="first")
    post(client, MessageSid="SM-b", Body="second")

    assert len(calls) == 2


def test_a_failed_attempt_is_retried_rather_than_swallowed(client, db, monkeypatch):
    monkeypatch.setattr(main, "twilio_validator", None)
    attempts = []

    def flaky(form_data, background_tasks, _db):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("triage blew up")
        return HTMLResponse(content=REPLY, media_type="application/xml")

    monkeypatch.setattr(main, "_process_whatsapp_sync", flaky)

    with pytest.raises(RuntimeError):
        post(client, MessageSid="SM-flaky")
    retry = post(client, MessageSid="SM-flaky")

    assert len(attempts) == 2, "the claim must be released so Twilio's retry is processed"
    assert retry.status_code == 200
    assert retry.text == REPLY


def test_message_without_an_id_is_still_processed(client, calls, monkeypatch):
    monkeypatch.setattr(main, "twilio_validator", None)

    response = post(client)

    assert response.status_code == 200
    assert len(calls) == 1
    assert idempotency.COLLECTION not in db_collections(client)


def db_collections(client):
    override = main.app.dependency_overrides[main.get_db]
    return override().data
