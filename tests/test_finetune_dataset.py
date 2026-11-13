"""The training set is the fine-tune's specification, so its defects become the
model's defects. These tests guard the three ways that can go wrong quietly.

**A label outside the enum.** The stored vocabulary is not the schema's
vocabulary, so every mapped label is checked against the enum it has to satisfy.
A dataset teaching "Electricity" as a category would produce a model whose
output pydantic rejects.

**The eval set leaking into training.** Every score in `evals/` becomes
meaningless if the model was trained on the cases. The phrasings were written
separately for exactly this reason, and this is where that claim is enforced
rather than asserted in a docstring.

**The label leaking into its own prompt.** The seeded reports embed their tag in
their body, which is why they are re-rendered at all. If a re-rendered body ever
contains its own answer again, the fine-tune goes back to learning a string
match and the eval cases -- all phrased differently -- go back to failing.
"""

import json

import pytest

import llm
import triage
from evals import dataset as EVAL_DS
from finetune import build_dataset as BD
from finetune import labels as L
from finetune import phrasings as P


# --- the label mapping ------------------------------------------------------

def test_department_variants_collapse_to_one_name():
    for variant in ("LESA / UPPCL", "LESA", "lesa/uppcl", "UPPCL"):
        assert L.canonical_department(variant) == "LESA"
    for variant in ("Jal Sansthan", "Jal Sansthan (Water Board)", "Water Board"):
        assert L.canonical_department(variant) == "Jal Sansthan"
    assert L.canonical_department("Municipal Corporation") == "Nagar Nigam"
    assert L.canonical_department("") is None


def test_canonical_department_names_satisfy_the_eval_match():
    """The eval asserts `expected.lower() in actual.lower()`, so a canonical
    name has to contain the token the eval looks for."""
    expected = {c.responsible_body for c in EVAL_DS.TRIAGE_CASES if c.responsible_body}
    for name in expected:
        assert any(name.lower() in dept.lower() for dept in L.DEPARTMENTS), name


def test_equivalent_tags_collapse_together():
    assert L.canonical_tag("pothole") == L.canonical_tag("broken road") == "road_repair"
    assert L.canonical_tag("garbage dump") == L.canonical_tag("overflowing bin")
    assert L.canonical_tag("streetlights not working") == "street_lighting"


def test_category_shaped_tags_are_dropped_not_taught():
    for tag in L.DEGENERATE_TAGS:
        assert L.canonical_tag(tag) is None


def test_every_canonical_tag_has_a_category_and_is_snake_case():
    for tag in L.CANONICAL_TAGS:
        assert tag in L.TAG_CATEGORY, tag
        assert L.TAG_CATEGORY[tag] in L.CATEGORIES
        assert tag == tag.lower() and " " not in tag, tag


def test_documented_routing_rules_are_applied_over_a_stored_contradiction():
    """The prompt says electricity goes to LESA. A stored 'Nagar Nigam' on a
    streetlight report is the defect being corrected, not evidence."""
    assert L.department_for("street_lighting", "Nagar Nigam") == "LESA"
    assert L.department_for("sewage_overflow", "Nagar Nigam") == "Jal Sansthan"
    # An issue type with no documented owner keeps what was stored.
    assert L.department_for("park_maintenance", "Nagar Nigam") == "Nagar Nigam"


def test_area_names_map_to_compass_zones():
    assert L.canonical_zone("Gomti Nagar") == "East"
    assert L.canonical_zone("Alambagh") == "South"
    assert L.canonical_zone("Central") == "Central"
    assert L.canonical_zone("Kanpur") is None


def test_tone_is_not_laundered_into_feasibility():
    """'Frustrated' is not a feasibility estimate, so it must not survive into
    the field; scale decides instead."""
    assert L.canonical_feasibility("Frustrated", 500_000) == "High"
    assert L.canonical_feasibility("Urgent", 50_000_000) == "Complex"
    # A value that genuinely is a feasibility is kept.
    assert L.canonical_feasibility("Moderate", 500_000) == "Moderate"


def test_language_codes_become_names():
    assert L.canonical_language("hi") == "Hindi"
    assert L.canonical_language("en") == "English"
    assert L.canonical_language("") == "English"


# --- the built examples -----------------------------------------------------

@pytest.fixture(scope="module")
def built():
    """Built from a small synthetic export, so the tests do not depend on a
    live database or on a file someone has to remember to regenerate."""
    rows = [
        {"id": "a", "body": "Please look into the streetlights not working situation in Alambagh.",
         "summary": "Streetlights not working in Alambagh", "category": "Electricity",
         "priority": "High", "sentiment": "Frustrated", "semantic_tag": "streetlights not working",
         "responsible_body": "LESA / UPPCL", "constituency_zone": "Alambagh",
         "extracted_location": "Alambagh", "estimated_budget": "120000",
         "ward_no": "Ward 22", "ward_officer": "Nobody", "original_language": "en",
         "latitude": "26.8", "longitude": "80.9"},
        {"id": "b", "body": "There is a severe issue of pothole in Ashiyana. Please fix it soon.",
         "summary": "Pothole in Ashiyana", "category": "Infrastructure",
         "priority": "Medium", "sentiment": "Neutral", "semantic_tag": "pothole",
         "responsible_body": "Nagar Nigam", "constituency_zone": "Ashiyana",
         "extracted_location": "Ashiyana", "estimated_budget": "4800000",
         "ward_no": "Ward 80", "ward_officer": "Sunita Verma", "original_language": "en",
         "latitude": "26.76", "longitude": "80.91"},
        # Untriaged: no labels to learn from, so it must be dropped.
        {"id": "c", "body": "Kuch to kijiye", "category": None, "priority": None,
         "semantic_tag": None, "responsible_body": None, "ward_no": None},
    ]
    return rows


def _build_from(tmp_path, rows):
    path = tmp_path / "raw.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return BD.build(str(path))


def test_untriaged_rows_are_dropped(tmp_path, built):
    examples, stats = _build_from(tmp_path, built)
    assert stats["dropped_unmappable"] == 1


def test_every_answer_validates_against_the_real_schema(tmp_path, built):
    """The strongest available check: the answer the model is being taught must
    be something `TriageResult` accepts, field for field."""
    examples, _ = _build_from(tmp_path, built)
    assert examples
    for example in examples:
        payload = json.loads(example["messages"][1]["content"])
        triage.TriageResult.model_validate(payload)


def test_answer_fields_match_the_decoding_schema_order(tmp_path, built):
    """Constrained decoding asks for properties in schema order; a training
    target in a different order teaches the model to fight the grammar."""
    order = list(llm.schema_for_decoding(triage.TriageResult)["properties"].keys())
    examples, _ = _build_from(tmp_path, built)
    for example in examples:
        assert list(json.loads(example["messages"][1]["content"]).keys()) == order


def test_prompt_is_the_production_prompt(tmp_path, built):
    examples, _ = _build_from(tmp_path, built)
    prompt = examples[0]["messages"][0]["content"]
    assert prompt.startswith("You are an AI triage system for Lucknow")
    assert "Complaint Text:" in prompt


def test_templated_bodies_are_rerendered_without_their_label(tmp_path, built):
    """The seeded body contains its own tag. After re-rendering it must not."""
    examples, stats = _build_from(tmp_path, built)
    assert stats["rerendered"] >= 2
    for example in examples:
        prompt = example["messages"][0]["content"].lower()
        tag = json.loads(example["messages"][1]["content"])["semantic_tag"]
        assert tag.replace("_", " ") not in prompt
        assert tag not in prompt


def test_ward_officer_follows_the_ward_band(tmp_path, built):
    """A stored officer that contradicts the ward band ('Nobody' on Ward 22) is
    recomputed, so the pair is never taught inconsistently."""
    examples, _ = _build_from(tmp_path, built)
    for example in examples:
        payload = json.loads(example["messages"][1]["content"])
        number = int(payload["ward_no"].split()[1])
        assert payload["ward_officer"] == triage.officer_for_ward(number)


def test_electricity_is_oversampled(tmp_path, built):
    examples, stats = _build_from(tmp_path, built)
    assert stats["electricity_oversampled"] >= 1
    departments = [e["_department"] for e in examples]
    assert departments.count("LESA") > 1


def test_critical_and_low_bands_are_both_present(tmp_path, built):
    """Critical is the band the base model never emits; Low is the contrast that
    stops teaching Critical from shifting everything upward."""
    examples, stats = _build_from(tmp_path, built)
    assert stats["critical_added"] > 0 and stats["low_added"] > 0
    priorities = {e["_priority"] for e in examples}
    assert {"Critical", "Low"} <= priorities


def test_critical_examples_route_electricity_to_lesa(tmp_path, built):
    examples, _ = _build_from(tmp_path, built)
    hazards = [e for e in examples
               if e["_tag"] == "electrical_hazard" and e["_priority"] == "Critical"]
    assert hazards
    assert all(e["_department"] == "LESA" for e in hazards)


# --- the eval set must stay unseen ------------------------------------------

def test_no_eval_case_text_appears_in_the_training_set(tmp_path, built):
    """A fine-tune trained on the eval cases would score well and mean nothing."""
    examples, _ = _build_from(tmp_path, built)
    prompts = "\n".join(e["messages"][0]["content"] for e in examples)
    for case in EVAL_DS.TRIAGE_CASES:
        assert case.description not in prompts, case.id
    for group in EVAL_DS.TAG_GROUPS:
        for description in group.descriptions:
            assert description not in prompts, description


def test_phrasings_do_not_reproduce_eval_case_text():
    """Checked against the bank itself, not only the built set, so a phrasing
    added later cannot slip in behind a row that happens to be dropped."""
    eval_texts = {c.description.strip().lower() for c in EVAL_DS.TRIAGE_CASES}
    eval_texts |= {d.strip().lower() for g in EVAL_DS.TAG_GROUPS for d in g.descriptions}
    for tag, bank in P.PHRASINGS.items():
        for text, _language in bank:
            rendered = text.format(area="Hazratganj").strip().lower()
            assert rendered not in eval_texts, f"{tag}: {text}"


def test_every_phrasing_tag_is_canonical():
    """A phrasing filed under a tag the label layer does not produce would be
    dead weight, and a sign the two files have drifted apart."""
    for tag in P.PHRASINGS:
        assert tag in L.CANONICAL_TAGS, tag
    for _text, tag, department, priority in P.CRITICAL_CASES + P.LOW_CASES:
        assert tag in L.CANONICAL_TAGS, tag
        assert department in L.DEPARTMENTS
        assert priority in L.PRIORITIES


def test_eval_critical_issue_types_have_phrasings():
    """The two gaps this fine-tune exists to close need training coverage: the
    electricity tags that the routing metric misses, and the tag groups the
    consistency metric scores."""
    for tag in ("street_lighting", "power_supply", "electrical_hazard",
                "water_supply", "road_repair"):
        assert len(P.PHRASINGS.get(tag, [])) >= 4, tag


def test_phrasings_cover_the_languages_the_evals_test():
    """Hinglish scores as English, so it is counted by inspecting the bank for
    non-Latin scripts: Hindi, Urdu and Bengali must all appear."""
    languages = {language for bank in P.PHRASINGS.values() for _text, language in bank}
    assert {"Hindi", "Urdu", "Bengali"} <= languages
