"""Structured civic triage.

Turns a citizen's free-text complaint into the fields the dashboard ranks on:
category, priority, department, ward, zone, budget and a semantic tag that
groups the same real-world issue regardless of phrasing.

Extracted from the intake handler so it can run somewhere other than inside the
webhook request. Triage is several seconds of model time; holding a WhatsApp
webhook open for it is what causes the provider timeouts, and therefore the
retries, that intake has to deduplicate.

Fails SOFT. A model outage returns the defaults below rather than raising, so a
citizen's report is still filed and can be triaged later. An untriaged report is
recoverable; a lost one is not.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

import triage_rules

logger = logging.getLogger(__name__)

TRIAGE_MODEL = "gemini-2.5-flash"

# After a quota or billing refusal the next call will fail the same way, and each
# attempt adds a second of latency to a report the rules can file at once.
MODEL_COOLDOWN_SECONDS = 120
_QUOTA_MARKERS = ("402", "429", "resource_exhausted", "quota", "depleted", "rate limit")
_model_cool_until = 0.0


def model_cooling() -> bool:
    return time.time() < _model_cool_until


def note_model_failure(error: Exception) -> None:
    """Back off from the model when it has refused on quota or billing grounds."""
    global _model_cool_until
    text = str(error).lower()
    if any(marker in text for marker in _QUOTA_MARKERS):
        _model_cool_until = time.time() + MODEL_COOLDOWN_SECONDS
        logger.warning("[TRIAGE] model refused on quota; using rules for %ss", MODEL_COOLDOWN_SECONDS)


def reset_model_cooldown() -> None:
    global _model_cool_until
    _model_cool_until = 0.0

# 110 municipal wards, each with a duty officer band.
# A ward the model actually decided, as opposed to "Unknown" or prose.
_WARD_RE = re.compile(r"Ward \d{1,3}")

WARD_COUNT = 110
H3_RESOLUTION = 8

DEFAULTS: dict[str, Any] = {
    "category": None,
    "priority": None,
    "feasibility": None,
    "extracted_location": None,
    "summary": None,
    "original_language": None,
    "constituency_zone": None,
    "estimated_budget": None,
    "semantic_tag": None,
    "visual_evidence": "",
    "responsible_body": "Municipal Corporation",
    "ward_no": "Ward 1",
    "ward_officer": "Duty Officer",
}


class TriageResult(BaseModel):
    category: Literal["Construction Development", "Land Development", "Infrastructure Upgrade", "Public Utility Works", "Environmental Project", "Maintenance & Repair", "Other"] = Field(description="Must be one of the strictly provided categories.")
    priority: Literal["Low", "Medium", "High", "Critical"] = Field(description="Priority severity level.")
    feasibility: Literal["High", "Moderate", "Complex", "Unknown"] = Field(alias="sentiment", description="Maps to the old sentiment DB column.")
    extracted_location: str = Field(description="Street name, landmark, or area extracted from text. Empty string if none.")
    summary: str = Field(description="Short 4-5 word summary title of the project proposal.")
    original_language: str = Field(description="The language the user originally submitted their request in (e.g., Hindi, English, Spanish).")
    constituency_zone: Literal["North", "South", "East", "West", "Central"] = Field(description="Infer this constituency zone from the location context. Default to Central if unknown.")
    estimated_budget: int = Field(description="An integer representing the estimated cost in INR based on the project scale (e.g., 500000 for small, 50000000 for large capital works).")
    responsible_body: str = Field(default="Municipal Corporation", description="The city department responsible for handling this issue (e.g. 'Public Works Department (PWD)', 'Jal Sansthan (Water Board)', 'Municipal Corporation').")
    ward_no: str = Field(default="Ward 1", description="Assign a demo municipal ward number (e.g. 'Ward 14') appropriate for the location.")
    ward_officer: str = Field(default="Rajeev Singh", description="Assign a demo Indian name for the responsible ward officer.")
    semantic_tag: str = Field(
        description=(
            "A normalized snake_case topic tag (2-3 words) that semantically groups this proposal "
            "with similar ones regardless of phrasing. Be CONSISTENT — the same real-world issue "
            "must always produce the same tag. Examples: 'road_repair', 'school_construction', "
            "'water_supply', 'street_lighting', 'drainage_improvement', 'park_development', "
            "'hospital_upgrade', 'bridge_construction', 'sanitation_works', 'electricity_supply'. "
            "Two proposals about 'MG Road is dark' and 'need streetlights on main road' must share 'street_lighting'."
        )
    )
    visual_evidence: str = Field(default="", description="If a photo was provided, describe the visible civic evidence in 6-12 words. Empty string if no photo or not relevant.")
    estimated_latitude: float | None = Field(default=None, description="If location text is provided, guess the approximate latitude for a map marker in Lucknow, India. Otherwise null.")
    estimated_longitude: float | None = Field(default=None, description="If location text is provided, guess the approximate longitude for a map marker in Lucknow, India. Otherwise null.")


def build_prompt(description: str, location_raw: str) -> str:
    return (
        "You are an AI triage system for Lucknow Municipal civic grievances via WhatsApp.\n"
        "Analyze this citizen complaint and extract all structured fields.\n"
        "Context: Lucknow has Nagar Nigam, Jal Sansthan (Water Board), PWD, LDA, LESA, Traffic Police.\n"
        "Potholes in gali/mohalla → Nagar Nigam. On highway → PWD.\n"
        "Water/sewage → Jal Sansthan. Electricity → LESA. Building → LDA.\n"
        "If an image is attached, use it as civic evidence to support the report.\n\n"
        f"Complaint Text: {description}\n"
        f"Submitted Location: {location_raw or 'Not provided'}"
    )


def officer_for_ward(ward_number: int) -> str:
    if ward_number <= 20:
        return "Ramesh Yadav"
    if ward_number <= 40:
        return "Priya Sharma"
    if ward_number <= 60:
        return "Mohd. Farhan Siddiqui"
    if ward_number <= 80:
        return "Sunita Verma"
    return "Ajay Kumar Singh"


def ward_from_coordinates(lat, lng) -> Optional[tuple[str, str]]:
    """Map a GPS pin to a ward via its H3 cell.

    A dropped pin is harder evidence than anything the model infers from text,
    so this overrides the model's ward guess. Hashing the H3 cell makes the
    mapping stable: the same place always lands in the same ward, which is what
    makes ward-level demand counts meaningful.
    """
    if lat is None or lng is None:
        return None
    try:
        import h3
        cell = h3.latlng_to_cell(lat, lng, H3_RESOLUTION)
        digest = int(hashlib.md5(cell.encode()).hexdigest(), 16)
        number = (digest % WARD_COUNT) + 1
        return f"Ward {number}", officer_for_ward(number)
    except Exception as e:
        logger.error(f"[H3 ERROR] Could not map coordinates to a ward: {e}")
        return None


def _classify(client, prompt: str, image_part, model: str) -> TriageResult:
    """Run the schema-constrained call, whichever backend `client` is.

    `client` is normally an llm.Provider. A raw google-genai client is still
    accepted because evals and the worker hand one around directly, and a report
    classified by either route is indistinguishable afterwards.
    """
    if hasattr(client, "classify"):
        image = image_part if image_part.__class__.__name__ == "ImageRef" else None
        if image_part is not None and image is None:
            # A vendor-specific part cannot be handed to another provider; the
            # description alone still triages, so drop the photo rather than fail.
            logger.warning("[TRIAGE] ignoring evidence photo of type %s", type(image_part).__name__)
        return client.classify(prompt, TriageResult, image=image)

    contents = [prompt]
    if image_part:
        contents.append(image_part)
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config={"response_mime_type": "application/json", "response_schema": TriageResult},
    )
    return response.parsed


def run_triage(client, description: str, location_raw: str = "", loc_source: str = "",
               image_part=None, lat=None, lng=None, model: str = TRIAGE_MODEL,
               rules_fallback: bool = False, photo_only: bool = False) -> dict:
    """Classify one report. Never raises: on failure the defaults are returned
    so the report is still filed and can be triaged again later.

    With `rules_fallback`, a report no model could classify gets keyword-rule
    fields instead of blanks. `ok` stays False so it remains eligible for
    /admin/retriage, and `triage_source` records which one decided.

    `model` applies only to the legacy google-genai path; an llm.Provider picks
    its own model from configuration.
    """
    result = dict(DEFAULTS)
    result["extracted_location"] = location_raw if loc_source == "text" else None
    result["triage_source"] = None

    # A GPS pin determines the ward whether or not the model answers.
    pinned = ward_from_coordinates(lat, lng)

    usable_photo = photo_only and image_part is not None and getattr(client, "supports_photo_only", False)
    if client and (description or usable_photo) and not (rules_fallback and model_cooling()):
        try:
            prompt = build_prompt(description, location_raw)
            if photo_only:
                prompt += ("\nThe citizen submitted a photo without a description. Describe the visible issue "
                           "using the photo and supplied location. Avoid inferring facts hidden from view.")
            parsed = _classify(client, prompt, image_part, model)
            result.update({
                "category": parsed.category,
                "priority": parsed.priority,
                "feasibility": parsed.feasibility,
                "original_language": parsed.original_language,
                "responsible_body": parsed.responsible_body,
                "summary": parsed.summary,
                "constituency_zone": parsed.constituency_zone,
                "estimated_budget": parsed.estimated_budget,
                "semantic_tag": parsed.semantic_tag,
                "visual_evidence": parsed.visual_evidence or "",
                "estimated_latitude": getattr(parsed, "estimated_latitude", None),
                "estimated_longitude": getattr(parsed, "estimated_longitude", None),
            })
            # The model is asked for a ward, but a model that does not know says
            # so ("Unknown", ""), and a non-answer on screen is worse than the
            # placeholder. A GPS pin overrides either one just below.
            if _WARD_RE.fullmatch((parsed.ward_no or "").strip()):
                result["ward_no"] = parsed.ward_no.strip()
                officer = (parsed.ward_officer or "").strip()
                if officer and officer.lower() != "unknown":
                    result["ward_officer"] = officer
            if loc_source == "text":
                result["extracted_location"] = parsed.extracted_location or location_raw
            result["ok"] = True
            result["triage_source"] = "model:" + getattr(client, "name", "gemini")
        except Exception as e:
            logger.error(f"[AI ERROR] Triage failed: {e}", exc_info=True)
            note_model_failure(e)
            result["ok"] = False
    else:
        result["ok"] = False

    if rules_fallback and not result["ok"] and description:
        result.update(triage_rules.classify(description, location_raw, lat, lng))
        result["triage_source"] = "rules"
        # A text location has no pin, but the gazetteer knows roughly where it is,
        # which is enough to place it on the map and in a ward.
        if not pinned:
            pinned = ward_from_coordinates(result.get("estimated_latitude"), result.get("estimated_longitude"))

    if pinned:
        result["ward_no"], result["ward_officer"] = pinned
        logger.info(f"[H3 MAPPING] {lat},{lng} -> {pinned[0]} ({pinned[1]})")

    return result
