"""Gemini first, the fine-tuned open model as its fallback, keyword rules last.

The chain exists so a Gemini outage, such as exhausted credits, does not drop
triage straight to the keyword rules while a working model is available.
"""

import pytest

import llm
import triage


class Parsed:
    category = "Public Utility Works"; priority = "Critical"; feasibility = "Moderate"
    extracted_location = "Chinhat"; summary = "Live electrical hazard"; original_language = "Urdu"
    constituency_zone = "East"; estimated_budget = 150000; responsible_body = "LESA / UPPCL"
    ward_no = "Ward 7"; ward_officer = "Ramesh Yadav"; semantic_tag = "electrical_hazard"
    visual_evidence = ""


class Fake(llm.Provider):
    def __init__(self, name, error=None, text_only=False):
        self.name = name
        self.error = error
        self.text_only = text_only
        self.calls = []

    def classify(self, prompt, schema, image=None):
        self.calls.append(image)
        if self.error:
            raise self.error
        return Parsed()

    def narrate(self, system, history, user, max_tokens=2048, temperature=0.2):
        self.calls.append(user)
        if self.error:
            raise self.error
        return f"{self.name} answer"


GEMINI_OUT_OF_CREDIT = llm.LLMUnavailable("gemini triage failed: 402 RESOURCE_EXHAUSTED. Your prepayment credits are depleted")


@pytest.fixture(autouse=True)
def clean():
    llm.reset()
    triage.reset_model_cooldown()
    yield
    llm.reset()
    triage.reset_model_cooldown()


def test_the_first_provider_answers_when_it_can():
    gemini, finetune = Fake("gemini"), Fake("llama_cpp")
    chain = llm.ChainProvider([gemini, finetune])

    assert chain.narrate("s", [], "q") == "gemini answer"
    assert finetune.calls == []
    assert chain.name == "gemini"


def test_the_fine_tune_takes_over_when_gemini_fails():
    gemini, finetune = Fake("gemini", error=GEMINI_OUT_OF_CREDIT), Fake("llama_cpp")
    chain = llm.ChainProvider([gemini, finetune])

    result = chain.classify("prompt", triage.TriageResult)

    assert result.priority == "Critical"
    assert chain.name == "llama_cpp", "triage must record who actually answered"


def test_an_out_of_credit_provider_is_skipped_for_a_while():
    gemini, finetune = Fake("gemini", error=GEMINI_OUT_OF_CREDIT), Fake("llama_cpp")
    chain = llm.ChainProvider([gemini, finetune])

    chain.classify("one", triage.TriageResult)
    chain.classify("two", triage.TriageResult)

    assert len(gemini.calls) == 1, "every report would otherwise wait on a call that cannot succeed"
    assert len(finetune.calls) == 2


def test_an_ordinary_failure_is_retried_next_time():
    gemini, finetune = Fake("gemini", error=RuntimeError("connection reset")), Fake("llama_cpp")
    chain = llm.ChainProvider([gemini, finetune])

    chain.classify("one", triage.TriageResult)
    chain.classify("two", triage.TriageResult)

    assert len(gemini.calls) == 2


def test_the_text_only_fine_tune_is_asked_without_the_photo():
    gemini = Fake("gemini", error=GEMINI_OUT_OF_CREDIT)
    finetune = Fake("llama_cpp", text_only=True)
    photo = llm.ImageRef(b"jpeg-bytes")

    llm.ChainProvider([gemini, finetune]).classify("prompt", triage.TriageResult, image=photo)

    assert gemini.calls == [photo]
    assert finetune.calls == [None]


def test_when_every_model_fails_triage_still_files_with_the_rules():
    chain = llm.ChainProvider([Fake("gemini", error=GEMINI_OUT_OF_CREDIT),
                               Fake("llama_cpp", error=llm.LLMUnavailable("space asleep"))])

    result = triage.run_triage(chain, description="Open manhole in Chinhat", location_raw="Chinhat",
                               loc_source="text", rules_fallback=True)

    assert result["triage_source"] == "rules"
    assert result["category"] == "Maintenance & Repair"


def test_triage_records_the_fine_tune_when_it_decided():
    chain = llm.ChainProvider([Fake("gemini", error=GEMINI_OUT_OF_CREDIT), Fake("llama_cpp")])

    result = triage.run_triage(chain, description="Wires hanging in Chinhat", location_raw="Chinhat",
                               loc_source="text", rules_fallback=True)

    assert result["ok"] is True
    assert result["triage_source"] == "model:llama_cpp"


def test_a_listed_order_builds_a_chain_of_whatever_is_configured(monkeypatch):
    built = {"gemini": Fake("gemini"), "llama_cpp": Fake("llama_cpp")}
    monkeypatch.setattr(llm, "_build", lambda kind: built.get(kind))
    monkeypatch.setenv("LLM_PROVIDER", "gemini, llama_cpp")

    provider = llm.get_provider(refresh=True)

    assert isinstance(provider, llm.ChainProvider)
    assert [p.name for p in provider.providers] == ["gemini", "llama_cpp"]


def test_a_listed_order_with_one_configured_provider_is_just_that_provider(monkeypatch):
    """Before the Space exists, `gemini,llama_cpp` behaves exactly like `gemini`."""
    gemini = Fake("gemini")
    monkeypatch.setattr(llm, "_build", lambda kind: gemini if kind == "gemini" else None)
    monkeypatch.setenv("LLM_PROVIDER", "gemini,llama_cpp")

    assert llm.get_provider(refresh=True) is gemini


def test_a_listed_order_with_nothing_configured_serves_computed_answers(monkeypatch):
    monkeypatch.setattr(llm, "_build", lambda kind: None)
    monkeypatch.setenv("LLM_PROVIDER", "gemini,llama_cpp")

    assert llm.get_provider(refresh=True) is None
