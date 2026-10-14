"""Admin access control.

The dashboard exposes every citizen report, the ranking that drives sanctions,
and endpoints that spend money by sending WhatsApp broadcasts. All of it had
been reachable without any credential.
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
from tests.fakes import FakeFirestore

# Routes that must never serve an anonymous caller.
PROTECTED = [
    ("get", "/messages"),
    ("get", "/surveys"),
    ("get", "/projects/ranked"),
    ("get", "/api/feed"),
    ("get", "/api/feed/stats"),
    ("get", "/api/analytics"),
    ("post", "/api/chat"),
    ("post", "/projects/sanction"),
    ("post", "/surveys"),
    ("post", "/surveys/abc/broadcast"),
    ("post", "/surveys/abc/activate"),
    ("post", "/surveys/abc/stop"),
    ("delete", "/surveys/abc"),
]

PUBLIC = [("get", "/health"), ("get", "/favicon.svg"), ("get", "/sw.js")]


class FakeToken:
    def __init__(self, email):
        self.email = email

    def __call__(self, token):
        if token == "bad-token":
            raise ValueError("token expired")
        return {"uid": "u1", "email": self.email}


@pytest.fixture
def client():
    main.app.dependency_overrides[main.get_db] = lambda: FakeFirestore()
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


@pytest.mark.parametrize("method,path", PROTECTED)
def test_anonymous_callers_are_refused(client, method, path, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "mp@example.com")

    response = getattr(client, method)(path)

    assert response.status_code in (401, 403), f"{method.upper()} {path} served an anonymous caller"


@pytest.mark.parametrize("method,path", PUBLIC)
def test_public_routes_stay_public(client, method, path):
    assert getattr(client, method)(path).status_code == 200


def test_allowlisted_admin_is_accepted(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "mp@example.com, aide@example.com")
    monkeypatch.setattr(main.auth, "verify_id_token", FakeToken("MP@example.com"))

    claims = main.verify_admin(_bearer("good-token"))

    assert claims["email"] == "MP@example.com", "the check must not be case sensitive"


def test_authenticated_but_unlisted_user_is_refused(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "mp@example.com")
    monkeypatch.setattr(main.auth, "verify_id_token", FakeToken("stranger@gmail.com"))

    with pytest.raises(HTTPException) as caught:
        main.verify_admin(_bearer("good-token"))

    assert caught.value.status_code == 403


def test_invalid_token_is_refused(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "mp@example.com")
    monkeypatch.setattr(main.auth, "verify_id_token", FakeToken("mp@example.com"))

    with pytest.raises(HTTPException) as caught:
        main.verify_admin(_bearer("bad-token"))

    assert caught.value.status_code == 401


def test_missing_allowlist_denies_everyone(monkeypatch):
    """An unset ADMIN_EMAILS used to mean 'any Google account is an admin'."""
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    monkeypatch.setattr(main.auth, "verify_id_token", FakeToken("anyone@gmail.com"))

    with pytest.raises(HTTPException) as caught:
        main.verify_admin(_bearer("good-token"))

    assert caught.value.status_code == 503
    assert "ADMIN_EMAILS" in caught.value.detail


def test_allowlist_is_read_per_request(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "first@example.com")
    assert main.admin_emails() == ["first@example.com"]

    monkeypatch.setenv("ADMIN_EMAILS", "second@example.com")
    assert main.admin_emails() == ["second@example.com"], "no redeploy should be needed"


def _bearer(token):
    from fastapi.security import HTTPAuthorizationCredentials
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
