"""The provider layer decides which model answers, and what happens when none
will. Both matter: a wrong provider silently changes how every report is
classified, and an unhandled provider failure turns a cosmetic outage into a
500 on an answer the server had already computed.
"""

import json

import httpx
import pytest
from pydantic import BaseModel

import llm
import triage


class Shape(BaseModel):
    """Stand-in for TriageResult; the real schema is exercised in test_triage."""

    category: str
    priority: str


@pytest.fixture(autouse=True)
def _clean_provider_cache(monkeypatch):
    """The resolver caches, so every test starts without a decision carried in
    from the last one."""
    for var in ("LLM_PROVIDER", "GEMINI_API_KEY", "GROQ_API_KEY", "OLLAMA_HOST"):
        monkeypatch.delenv(var, raising=False)
    llm.reset()
    yield
    llm.reset()


# --- reply cleaning ---------------------------------------------------------
# Small local models are the reason this layer exists, and they do two things
# hosted models do not: narrate their reasoning, and wrap JSON in prose.

def test_thinking_is_stripped_from_narration():
    assert llm._strip_thinking("<think>weighing it up</think>**Ward 14.**") == "**Ward 14.**"


def test_thinking_block_spanning_lines_is_stripped():
    assert llm._strip_thinking("<think>\nline\nline\n</think>\nAnswer") == "Answer"


def test_json_is_found_inside_a_markdown_fence():
    raw = 'Here you go:\n```json\n{"category": "Other", "priority": "Low"}\n```'
    assert llm._extract_json(raw)["category"] == "Other"


def test_json_is_found_after_a_thinking_block():
    raw = '<think>deciding</think>{"category": "Other", "priority": "High"}'
    assert llm._extract_json(raw)["priority"] == "High"


def test_reply_without_json_is_unavailable_not_a_crash():
    with pytest.raises(llm.LLMUnavailable):
        llm._extract_json("I could not classify that.")


def test_malformed_json_is_unavailable_not_a_crash():
    with pytest.raises(llm.LLMUnavailable):
        llm._extract_json('{"category": "Other", ')


# --- ollama -----------------------------------------------------------------

def _stub_ollama(monkeypatch, content, *, status=200, capture=None):
    """Replace httpx.Client with one that answers /api/chat from memory."""

    class StubResponse:
        status_code = status
        text = content if isinstance(content, str) else json.dumps(content)

        def raise_for_status(self):
            if status >= 400:
                raise httpx.HTTPStatusError("boom", request=None, response=self)

        def json(self):
            return {"message": {"content": self.text}}

    class StubClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None):
            if capture is not None:
                capture.append((url, json))
            return StubResponse()

        def get(self, url):
            return StubResponse()

    monkeypatch.setattr(llm.httpx, "Client", StubClient)


def test_ollama_narration_returns_the_model_text(monkeypatch):
    _stub_ollama(monkeypatch, "**Ward 14 is worst.**")
    provider = llm.OllamaProvider()
    assert provider.narrate("sys", [], "which ward?") == "**Ward 14 is worst.**"


def test_ollama_narration_sends_system_history_and_query_in_order(monkeypatch):
    calls = []
    _stub_ollama(monkeypatch, "ok", capture=calls)
    provider = llm.OllamaProvider()
    provider.narrate("SYS", [llm.Turn("user", "first"), llm.Turn("assistant", "reply")], "second")

    roles = [m["role"] for m in calls[0][1]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert calls[0][1]["messages"][0]["content"] == "SYS"
    assert calls[0][1]["messages"][-1]["content"] == "second"


def test_ollama_empty_narration_is_unavailable(monkeypatch):
    _stub_ollama(monkeypatch, "   ")
    with pytest.raises(llm.LLMUnavailable):
        llm.OllamaProvider().narrate("sys", [], "q")


def test_ollama_unreachable_is_unavailable_not_a_crash(monkeypatch):
    class Dead:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **kw):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(llm.httpx, "Client", Dead)
    with pytest.raises(llm.LLMUnavailable):
        llm.OllamaProvider().narrate("sys", [], "q")


def test_ollama_classify_constrains_decoding_to_the_schema(monkeypatch):
    calls = []
    _stub_ollama(monkeypatch, '{"category": "Other", "priority": "Low"}', capture=calls)
    result = llm.OllamaProvider().classify("prompt", Shape)

    assert isinstance(result, Shape) and result.category == "Other"
    sent = calls[0][1]
    # The schema goes in `format`, so invalid JSON is impossible rather than
    # merely discouraged, and the reply must not be truncated mid-object.
    assert sent["format"]["properties"].keys() >= {"category", "priority"}
    assert sent["options"].get("num_predict") is None
    assert sent["options"]["temperature"] == 0


def test_ollama_classify_attaches_an_evidence_photo(monkeypatch):
    calls = []
    _stub_ollama(monkeypatch, '{"category": "Other", "priority": "Low"}', capture=calls)
    llm.OllamaProvider().classify("prompt", Shape, image=llm.ImageRef(b"\x89PNG", "image/png"))
    assert calls[0][1]["messages"][0]["images"] == ["iVBORw=="]


# --- groq -------------------------------------------------------------------

def test_groq_cannot_classify():
    """Groq narrates. Routing a citizen's complaint through it would be a silent
    downgrade, so it refuses rather than guessing."""
    with pytest.raises(llm.LLMUnavailable):
        llm.GroqProvider(client=object()).classify("prompt", Shape)


# --- resolution -------------------------------------------------------------

def test_explicit_provider_is_honoured(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setattr(llm.OllamaProvider, "available", lambda self: True)
    assert llm.get_provider().name == "ollama"


def test_explicit_provider_does_not_silently_fall_back(monkeypatch):
    """Asking for ollama and getting billed gemini quota instead would be a
    surprise worth more than the answer."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("GEMINI_API_KEY", "key")
    monkeypatch.setattr(llm.OllamaProvider, "available", lambda self: False)
    assert llm.get_provider() is None


def test_auto_prefers_a_reachable_local_model_over_paid_quota(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "key")
    monkeypatch.setattr(llm.OllamaProvider, "available", lambda self: True)
    assert llm.get_provider().name == "ollama"


def test_auto_returns_none_when_nothing_is_configured(monkeypatch):
    monkeypatch.setattr(llm.OllamaProvider, "available", lambda self: False)
    assert llm.get_provider() is None


def test_provider_is_cached_until_reset(monkeypatch):
    probes = []
    monkeypatch.setattr(llm.OllamaProvider, "available",
                        lambda self: (probes.append(1), True)[1])
    llm.get_provider()
    llm.get_provider()
    assert len(probes) == 1, "resolving twice should not re-probe the host"


# --- the seam with triage ---------------------------------------------------

def test_triage_uses_a_provider_when_given_one(monkeypatch):
    _stub_ollama(monkeypatch, json.dumps({
        "category": "Public Utility Works", "priority": "High", "sentiment": "Moderate",
        "extracted_location": "MG Road", "summary": "Dead street light",
        "original_language": "English", "constituency_zone": "Central",
        "estimated_budget": 200000, "responsible_body": "Municipal Corporation",
        "ward_no": "Ward 9", "ward_officer": "A Sharma",
        "semantic_tag": "street_lighting", "visual_evidence": "",
    }))
    result = triage.run_triage(llm.OllamaProvider(), "street light dead", "MG Road", "text")
    assert result["ok"] is True
    assert result["semantic_tag"] == "street_lighting"


def test_triage_falls_back_to_defaults_when_the_provider_fails(monkeypatch):
    """An untriaged report is recoverable; a lost one is not."""
    _stub_ollama(monkeypatch, "no json here")
    result = triage.run_triage(llm.OllamaProvider(), "street light dead", "MG Road", "text")
    assert result["ok"] is False
    assert result["category"] is None
    assert result["ward_officer"] == "Duty Officer"


def test_triage_drops_a_vendor_part_rather_than_failing(monkeypatch):
    """A google-genai Part cannot be handed to Ollama. The description alone is
    still worth classifying, so the photo is dropped and the report triaged."""
    calls = []
    _stub_ollama(monkeypatch, json.dumps({
        "category": "Other", "priority": "Low", "sentiment": "Unknown",
        "extracted_location": "", "summary": "s", "original_language": "English",
        "constituency_zone": "Central", "estimated_budget": 0,
        "responsible_body": "Municipal Corporation", "ward_no": "Ward 1",
        "ward_officer": "Duty Officer", "semantic_tag": "other", "visual_evidence": "",
    }), capture=calls)

    class VendorPart:
        pass

    result = triage.run_triage(llm.OllamaProvider(), "desc", "", "text", image_part=VendorPart())
    assert result["ok"] is True
    assert "images" not in calls[0][1]["messages"][0]
