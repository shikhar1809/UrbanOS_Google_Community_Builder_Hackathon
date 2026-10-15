"""The report query narrates figures analytics.py has already computed, and the
prompt forbids it from calculating anything. That makes a model outage cosmetic:
the server is holding the real answer either way. So the endpoint must serve
that answer rather than an error, and must never let narration invent a number
the computation did not produce.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import analytics
import llm
import main
from tests.fakes import FakeFirestore

BASE = datetime(2026, 1, 1, 10, 0, 0)


class StubProvider(llm.Provider):
    """Stands in for whichever backend is configured, and records the prompt so
    what the model was told can be asserted."""

    name = "stub"

    def __init__(self, reply="**Ward 14 leads.**", error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def narrate(self, system, history, user, max_tokens=2048, temperature=0.2):
        self.calls.append({"system": system, "history": list(history), "user": user})
        if self.error:
            raise self.error
        return self.reply


@pytest.fixture(autouse=True)
def fresh_caches():
    analytics.invalidate_snapshot()
    main._BRIEFING_CACHE.clear()
    llm.reset()
    yield
    analytics.invalidate_snapshot()
    main._BRIEFING_CACHE.clear()
    llm.reset()


@pytest.fixture
def db():
    store = FakeFirestore()
    for i in range(6):
        store.data.setdefault("messages", {})[f"m{i:05d}"] = {
            "reference_id": f"UO{i:05d}",
            "timestamp": (BASE + timedelta(minutes=i)).isoformat(),
            "sender": "whatsapp:+910000000000",
            "body": "Garbage not collected near the park gate",
            "summary": "Garbage not collected",
            "constituency_zone": "North",
            "semantic_tag": "sanitation_works",
            # The planner maps "garbage" in the query to this category, so the
            # fixture has to use it for _ask() to match any records at all.
            "category": "Waste Management",
            "priority": "High",
            "sentiment": "Moderate",
            "status": "Pending",
            "ward_no": "Ward 14",
            "responsible_body": "Nagar Nigam",
        }
    return store


@pytest.fixture
def client(db, monkeypatch):
    main.app.dependency_overrides[main.verify_admin] = lambda: {"email": "mp@example.com"}
    main.app.dependency_overrides[main.get_db] = lambda: db
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def _ask(client, query="which ward is worst for garbage?"):
    return client.post("/api/chat", json={"query": query, "history": []})


def test_narration_is_returned_when_a_provider_answers(client, monkeypatch):
    stub = StubProvider()
    monkeypatch.setattr(llm, "get_provider", lambda *a, **kw: stub)
    response = _ask(client)
    assert response.status_code == 200
    assert response.json()["response"] == "**Ward 14 leads.**"


def test_the_model_is_given_the_computed_facts(client, monkeypatch):
    """The prompt has to carry the answer, because the model is explicitly told
    not to work anything out for itself."""
    stub = StubProvider()
    monkeypatch.setattr(llm, "get_provider", lambda *a, **kw: stub)
    _ask(client)
    assert "COMPUTED FACTS" in stub.calls[0]["system"]


def test_history_is_forwarded_as_alternating_turns(client, monkeypatch):
    """The API speaks Gemini's vocabulary ("model"); a provider is handed the
    chat-completions one ("assistant"), so the roles have to be translated."""
    stub = StubProvider()
    monkeypatch.setattr(llm, "get_provider", lambda *a, **kw: stub)
    response = client.post("/api/chat", json={
        "query": "and the month before?",
        "history": [{"role": "user", "content": "worst ward?"},
                    {"role": "model", "content": "Ward 14."}],
    })
    assert response.status_code == 200
    assert [t.role for t in stub.calls[0]["history"]] == ["user", "assistant"]
    assert stub.calls[0]["user"] == "and the month before?"


def test_a_narration_failure_serves_the_computed_answer_not_a_500(client, monkeypatch):
    """This path used to raise HTTPException(500), discarding an answer the
    server had already finished computing."""
    stub = StubProvider(error=llm.LLMUnavailable("ollama unreachable"))
    monkeypatch.setattr(llm, "get_provider", lambda *a, **kw: stub)
    response = _ask(client)
    assert response.status_code == 200
    assert "narration is unavailable" in response.json()["response"]


def test_no_provider_configured_still_answers(client, monkeypatch):
    monkeypatch.setattr(llm, "get_provider", lambda *a, **kw: None)
    response = _ask(client)
    assert response.status_code == 200
    assert "narration is unavailable" in response.json()["response"]


def test_an_empty_result_is_reported_without_asking_a_model(client, monkeypatch):
    """The failure this prevents: asked to narrate a scope with no matches,
    llama3.1:8b answered from the surrounding briefing every time, naming a
    ranked ward as the finding. The borrowed figures were real, so the reply
    read like a genuine answer to a question that in fact matched nothing.
    """
    stub = StubProvider(reply="**Ward 14 leads.**")
    monkeypatch.setattr(llm, "get_provider", lambda *a, **kw: stub)
    monkeypatch.setattr(main.AN, "compute_answer", lambda *a, **kw: {
        "facts": "SCOPE: 0 complaints out of 6 in window.\nRESULT: No records match this scope.",
        "chart": None, "map_ids": [], "headline": "No matching records", "no_match": True,
    })
    body = _ask(client).json()

    assert stub.calls == [], "a model must not be asked to describe an empty result"
    assert "No matching records" in body["response"]
    assert "Ward 14" not in body["response"]


def test_a_non_empty_result_still_goes_to_the_model(client, monkeypatch):
    """The short-circuit above must not swallow ordinary answers."""
    stub = StubProvider()
    monkeypatch.setattr(llm, "get_provider", lambda *a, **kw: stub)
    _ask(client)
    assert len(stub.calls) == 1


def test_chart_and_map_survive_a_narration_failure(client, monkeypatch):
    """The chart and the map highlight come from the computation, so they must
    not disappear just because the prose did."""
    stub = StubProvider(error=RuntimeError("boom"))
    monkeypatch.setattr(llm, "get_provider", lambda *a, **kw: stub)
    body = _ask(client).json()
    assert "chart" in body and "map_filter" in body
    assert body["range"]["grand_total"] == 6
