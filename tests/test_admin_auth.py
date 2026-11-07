"""Open access. Sign-in was removed at the owner's request, so these pin the two
things that decision relies on: nobody is asked to sign in, and no response
hands a stranger a citizen's phone number.
"""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from tests.fakes import FakeFirestore

PHONE = "whatsapp:+919812345678"
OTHER_PHONE = "whatsapp:+919876543210"
DIGITS = ("919812345678", "919876543210", "9812345678", "9876543210")

READ_ROUTES = [
    "/messages",
    "/api/feed",
    "/api/feed/stats",
    "/api/analytics",
    "/projects/ranked",
    "/surveys",
    "/surveys/s1/report",
]


class Db(FakeFirestore):
    pass


@pytest.fixture
def db():
    store = Db()
    store.data["messages"] = {
        f"r{n}": {
            "id": f"r{n}", "reference_id": f"UO{n}", "sender": phone, "body": "Garbage is piling up",
            "timestamp": "2026-11-01T10:00:00", "category": "Environmental Project", "priority": "High",
            "status": "Open", "constituency_zone": "East", "semantic_tag": "garbage_collection",
            "ward_no": "Ward 7", "ward_officer": "Ramesh Yadav", "responsible_body": "Nagar Nigam",
            "estimated_budget": 100000, "latitude": 26.85, "longitude": 80.99,
        }
        for n, phone in enumerate((PHONE, OTHER_PHONE))
    }
    store.data["surveys"] = {"s1": {
        "id": "s1", "question": "Is it better?", "options": "Yes,No", "is_active": False,
        "sent_to": [PHONE, OTHER_PHONE], "sent_at": "2026-11-02T10:00:00",
    }}
    store.data["survey_responses"] = {"a1": {"survey_id": "s1", "sender": PHONE, "selected_option": "Yes"}}
    return store


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(main, "gemini_client", None)
    monkeypatch.setattr(main.LLM, "get_provider", lambda *a, **k: None)
    main.AN.invalidate_snapshot()
    main._RANKED_CACHE.update({"data": None, "at": 0.0, "version": None})
    main.app.dependency_overrides[main.get_db] = lambda: db
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()
    main.AN.invalidate_snapshot()
    main._RANKED_CACHE.update({"data": None, "at": 0.0, "version": None})


@pytest.mark.parametrize("path", READ_ROUTES)
def test_reads_need_no_sign_in_and_reveal_no_phone_numbers(client, path):
    response = client.get(path)

    assert response.status_code == 200, path
    body = json.dumps(response.json())
    for digits in DIGITS:
        assert digits not in body, f"{path} exposed a citizen's number"


def test_audience_preview_masks_numbers(client):
    response = client.post("/surveys/audience/preview", json={})

    assert response.status_code == 200
    body = json.dumps(response.json(), ensure_ascii=False)
    for digits in DIGITS:
        assert digits not in body


def test_chat_needs_no_sign_in_and_reveals_no_phone_numbers(client):
    response = client.post("/api/chat", json={"query": "which ward is worst?"})

    assert response.status_code == 200
    body = json.dumps(response.json())
    for digits in DIGITS:
        assert digits not in body


def test_a_sanction_is_accepted_without_a_sender_list_and_still_finds_citizens(client, monkeypatch):
    """The page can no longer send one, since it never receives the numbers."""
    notified = []
    monkeypatch.setattr(main, "notify_sanctioned", lambda senders, title, pid: notified.extend(senders))

    response = client.post("/projects/sanction", json={
        "project_id": "garbage_collection|East", "title": "Garbage", "zone": "East",
        "category": "Environmental Project",
    })

    assert response.status_code == 200
    assert response.json()["citizens_notified"] == 2
    assert sorted(notified) == sorted([PHONE, OTHER_PHONE]), "numbers come from the database, not the caller"


def test_the_page_has_no_login_screen():
    page = Path("public/index.html").read_text(encoding="utf-8")

    for needle in ("pin-gate", "signInWithGoogle", "firebase-auth-compat", "firebase.auth(", "getIdToken"):
        assert needle not in page, needle
    assert not re.search(r"p\.senders|senders\.length", page), "the page must not expect phone numbers"


@pytest.mark.parametrize("path", ["/health", "/favicon.svg", "/sw.js"])
def test_static_routes_stay_public(client, path):
    assert client.get(path).status_code == 200
