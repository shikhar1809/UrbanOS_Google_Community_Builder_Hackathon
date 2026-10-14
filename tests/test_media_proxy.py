"""The media proxy forwards the Twilio account SID and auth token upstream, so
the host it talks to has to be checked before the request is made. Previously
any caller could name a host and collect the credentials.
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main

ATTACKER_URLS = [
    "https://evil.example.com/collect",
    "https://api.twilio.com.evil.example.com/x",      # suffix smuggling
    "https://evil.example.com/?x=api.twilio.com",     # value in the query
    "https://user:pw@evil.example.com/x",             # userinfo trick
    "https://169.254.169.254/latest/meta-data/",      # cloud metadata
    "https://localhost/admin",
    "https://127.0.0.1:8000/health",
]


@pytest.mark.parametrize("url", ATTACKER_URLS)
def test_credentials_are_never_offered_to_other_hosts(url):
    assert main.media_url_is_credentialed(url) is False


def test_the_twilio_media_api_is_allowed():
    url = "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages/MM1/Media/ME1"
    assert main.media_url_is_credentialed(url) is True


@pytest.mark.parametrize("scheme_url", [
    "http://api.twilio.com/x",       # plaintext would expose the credentials
    "file:///etc/passwd",
    "gopher://api.twilio.com/x",
    "",
])
def test_non_https_urls_are_refused(scheme_url):
    with pytest.raises(HTTPException) as caught:
        main.media_url_is_credentialed(scheme_url)
    assert caught.value.status_code == 400


@pytest.mark.parametrize("url", [
    "https://media.twiliocdn.com/AC1/ME1",
    "https://s3-external-1.amazonaws.com/com.twilio.prod.twilio-api/ME1",
])
def test_twilio_cdn_redirects_are_followed(url):
    assert main.media_redirect_is_allowed(url) is True


@pytest.mark.parametrize("url", [
    "https://evil.example.com/x",
    "https://twiliocdn.com.evil.example.com/x",
    "https://169.254.169.254/latest/meta-data/",
])
def test_redirects_off_the_cdn_are_refused(url):
    assert main.media_redirect_is_allowed(url) is False


def test_endpoint_rejects_a_foreign_host_before_making_a_request(monkeypatch):
    monkeypatch.setattr(main, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(main, "TWILIO_AUTH_TOKEN", "token-test")

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("an outbound request was made to a disallowed host")

    monkeypatch.setattr(main.httpx, "AsyncClient", explode)
    client = TestClient(main.app)

    response = client.get("/media-proxy", params={"url": "https://evil.example.com/collect"})

    assert response.status_code == 400
