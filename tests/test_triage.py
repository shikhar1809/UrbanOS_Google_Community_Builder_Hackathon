"""Triage turns a complaint into the fields the ranking depends on, so its
failure behaviour matters as much as its success behaviour: a model outage must
still leave a filed, recoverable report rather than losing a citizen's message.
"""

import pytest

import triage


class FakeParsed:
    def __init__(self, **kw):
        defaults = dict(
            category="Infrastructure Upgrade",
            priority="High",
            feasibility="Moderate",
            extracted_location="Hazratganj",
            summary="Sewage overflow near market",
            original_language="Hindi",
            constituency_zone="Central",
            estimated_budget=500000,
            responsible_body="Jal Sansthan (Water Board)",
            ward_no="Ward 14",
            ward_officer="Rajeev Singh",
            semantic_tag="sewage_overflow",
            visual_evidence="standing sewage across the road",
        )
        defaults.update(kw)
        for k, v in defaults.items():
            setattr(self, k, v)


class FakeClient:
    """Records the call so the prompt and image wiring can be asserted."""

    def __init__(self, parsed=None, error=None):
        self.calls = []
        self._parsed = parsed or FakeParsed()
        self._error = error
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self._error:
            raise self._error
        return type("R", (), {"parsed": self._parsed})()


def test_structured_fields_are_returned():
    result = triage.run_triage(FakeClient(), "sewage overflowing", "Hazratganj", "text")

    assert result["ok"] is True
    assert result["category"] == "Infrastructure Upgrade"
    assert result["priority"] == "High"
    assert result["semantic_tag"] == "sewage_overflow"
    assert result["responsible_body"] == "Jal Sansthan (Water Board)"


def test_a_model_failure_still_returns_a_filable_report():
    result = triage.run_triage(FakeClient(error=RuntimeError("429 quota exhausted")),
                               "sewage overflowing", "Hazratganj", "text")

    assert result["ok"] is False
    assert result["category"] is None, "no category is honest; a wrong one corrupts ranking"
    assert result["responsible_body"] == "Municipal Corporation", "a routable default"
    assert result["ward_no"] == "Ward 1"


def test_no_client_configured_returns_defaults():
    result = triage.run_triage(None, "sewage overflowing")

    assert result["ok"] is False
    assert result["category"] is None


def test_empty_description_is_not_sent_to_the_model():
    client = FakeClient()

    result = triage.run_triage(client, "")

    assert client.calls == []
    assert result["ok"] is False


def test_the_photo_is_passed_to_the_model_as_evidence():
    client = FakeClient()
    sentinel = object()

    triage.run_triage(client, "broken drain", image_part=sentinel)

    assert sentinel in client.calls[0]["contents"]


def test_a_schema_is_always_requested():
    client = FakeClient()

    triage.run_triage(client, "broken drain")

    config = client.calls[0]["config"]
    assert config["response_mime_type"] == "application/json"
    assert config["response_schema"] is triage.TriageResult


def test_text_location_falls_back_to_what_the_citizen_typed():
    client = FakeClient(parsed=FakeParsed(extracted_location=""))

    result = triage.run_triage(client, "broken drain", "Near Chowk police station", "text")

    assert result["extracted_location"] == "Near Chowk police station"


def test_a_gps_pin_overrides_the_models_ward_guess():
    """A dropped pin is harder evidence than anything inferred from text."""
    result = triage.run_triage(FakeClient(), "broken drain", lat=26.8467, lng=80.9462)

    assert result["ward_no"] != "Ward 14", "the model's guess should have been replaced"
    assert result["ward_no"].startswith("Ward ")


def test_the_same_place_always_maps_to_the_same_ward():
    """Ward demand counts are only meaningful if the mapping is stable."""
    first = triage.ward_from_coordinates(26.8467, 80.9462)
    second = triage.ward_from_coordinates(26.8467, 80.9462)

    assert first == second
    assert first is not None


def test_ward_mapping_stays_inside_the_citys_ward_range():
    for lat, lng in [(26.8467, 80.9462), (26.8534, 80.9981), (26.80, 80.90), (26.90, 81.00)]:
        ward, officer = triage.ward_from_coordinates(lat, lng)
        number = int(ward.split()[1])
        assert 1 <= number <= triage.WARD_COUNT
        assert officer


def test_missing_coordinates_map_to_no_ward():
    assert triage.ward_from_coordinates(None, None) is None
    assert triage.ward_from_coordinates(26.8, None) is None


@pytest.mark.parametrize("number,expected", [
    (1, "Ramesh Yadav"), (20, "Ramesh Yadav"),
    (21, "Priya Sharma"), (40, "Priya Sharma"),
    (41, "Mohd. Farhan Siddiqui"), (60, "Mohd. Farhan Siddiqui"),
    (61, "Sunita Verma"), (80, "Sunita Verma"),
    (81, "Ajay Kumar Singh"), (110, "Ajay Kumar Singh"),
])
def test_officer_bands(number, expected):
    assert triage.officer_for_ward(number) == expected


def test_prompt_carries_the_complaint_and_location():
    prompt = triage.build_prompt("sewage overflowing", "Hazratganj")

    assert "sewage overflowing" in prompt
    assert "Hazratganj" in prompt


def test_prompt_is_explicit_when_no_location_was_given():
    assert "Not provided" in triage.build_prompt("sewage overflowing", "")
