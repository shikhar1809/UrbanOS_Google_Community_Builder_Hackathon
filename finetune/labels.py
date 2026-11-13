"""What the stored labels mean, in terms of the schema triage must emit.

This module exists because the export and the schema do not agree. The
`messages` collection holds two populations:

* ~1007 seeded demo reports written against an older label vocabulary --
  `category` values like "Electricity" and "Waste Management", a
  `constituency_zone` holding an AREA NAME ("Gomti Nagar") rather than a
  compass zone, a `sentiment` holding emotional tone ("Frustrated") where the
  schema wants engineering feasibility, and a free-text `semantic_tag`
  ("streetlights not working").
* ~38 reports genuinely triaged by Gemini against the current schema.

Training on the raw export would therefore teach the model to emit values that
are not in its own enums. Every mapping needed to prevent that lives here, in
one reviewable place, rather than being buried in the dataset builder.

Two mappings are mechanical and two are judgement, and it is worth being clear
about which is which:

* MECHANICAL. `CANONICAL_TAG` collapses the observed tags onto canonical
  snake_case ones. The seeded tags are already clean clusters; only their
  wording is inconsistent. "pothole" and "broken road" are one issue, and the
  eval's own tag group says so.
* MECHANICAL. `canonical_department` folds surface variants ("LESA / UPPCL",
  "Jal Sansthan (Water Board)") onto one name each. Routing in the seed data is
  already perfectly consistent per issue type -- all 163 electricity reports
  say LESA -- so there is nothing to decide, only spelling to settle.
* JUDGEMENT. `TAG_CATEGORY` and `AREA_ZONE`. The legacy category vocabulary has
  no faithful mapping onto the schema's seven project-shaped categories, and an
  area name is not a compass zone. Note that the eval scores `responsible_body`
  and `semantic_tag` and does NOT score category or zone, so these mappings
  carry the least weight of the four -- but a dataset still cannot contain a
  value outside the enum, so a choice must be made.
* DISCARDED. The legacy `sentiment` is not a feasibility estimate under another
  name; it is a different measurement. Rather than launder tone into
  feasibility, feasibility is derived from project scale via
  `feasibility_from_budget`, which is at least the thing the field claims to
  describe.
"""

from __future__ import annotations

import re
from typing import Optional

# --- Departments -------------------------------------------------------------
# The eval matches on a distinctive token (`"LESA" in actual`), so a short
# canonical name satisfies it and gives the fine-tune one surface form per
# department instead of three.

DEPARTMENTS = ("Nagar Nigam", "Jal Sansthan", "LESA", "PWD", "LDA", "Traffic Police")

_DEPARTMENT_PATTERNS = (
    ("LESA", ("lesa", "uppcl", "electricity department", "power corporation")),
    ("Jal Sansthan", ("jal sansthan", "jal nigam", "water board", "water works")),
    ("LDA", ("lda", "development authority")),
    ("PWD", ("pwd", "public works")),
    ("Traffic Police", ("traffic police",)),
    ("Nagar Nigam", ("nagar nigam", "municipal corporation", "municipal")),
)


def canonical_department(value: Optional[str]) -> Optional[str]:
    """One name per department. "LESA / UPPCL" and "LESA" are the same body."""
    text = (value or "").strip().lower()
    if not text:
        return None
    for canonical, needles in _DEPARTMENT_PATTERNS:
        if any(needle in text for needle in needles):
            return canonical
    return None


# --- Semantic tags -----------------------------------------------------------
# The product requirement is that one real-world issue yields one tag; the
# wording is not asserted anywhere. So the canonical form only has to be
# snake_case, descriptive, and stable.

CANONICAL_TAG = {
    # roads and footpaths
    "pothole": "road_repair",
    "broken road": "road_repair",
    "road_repair": "road_repair",
    "footpath damage": "footpath_repair",
    "bridge repair": "bridge_repair",
    # water and drainage
    "broken pipe": "water_pipeline_repair",
    "water_leakage": "water_pipeline_repair",
    "no water supply": "water_supply",
    "water_supply": "water_supply",
    "dirty drinking water": "water_quality",
    "water logging": "drainage_improvement",
    "drainage_improvement": "drainage_improvement",
    "open manhole": "manhole_repair",
    "sewage_overflow": "sewage_overflow",
    "sanitation_works": "sanitation_works",
    # electricity
    "streetlights not working": "street_lighting",
    "street_lighting": "street_lighting",
    "frequent power cuts": "power_supply",
    "transformer spark": "electrical_hazard",
    "loose hanging wires": "electrical_hazard",
    # waste and animals
    "garbage dump": "garbage_collection",
    "overflowing bin": "garbage_collection",
    "irregular pickup": "garbage_collection",
    "dead animal": "carcass_removal",
    "stray dog menace": "stray_animal_control",
    # green space and parks
    "overgrown weeds": "vegetation_clearance",
    "park maintenance needed": "park_maintenance",
    "broken swings": "park_equipment_repair",
    "park_development": "park_development",
    "sports_facility_construction": "sports_facility_construction",
    # transport and enforcement
    "traffic signal broken": "traffic_signal_repair",
    "bus stop shed missing": "bus_stop_construction",
    "unauthorized parking": "unauthorized_parking",
    "illegal auto stand": "illegal_encroachment",
    "no police patrolling": "police_patrolling",
    # social infrastructure
    "school_construction": "school_construction",
    "hospital_construction": "hospital_construction",
}

# Tags that name a CATEGORY rather than a topic. They are the fragmentation
# this work removes, so a row carrying one is dropped from training instead of
# being taught. All are single stray records.
DEGENERATE_TAGS = {
    "infrastructure_upgrade",
    "public_utility_works",
    "maintenance_and_repair",
    "general_inquiry",
    "unclear_request",
}

_SNAKE_RE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")


def canonical_tag(value: Optional[str]) -> Optional[str]:
    """Canonical snake_case tag, or None if the tag is absent or degenerate."""
    text = (value or "").strip().lower()
    if not text:
        return None
    if text in DEGENERATE_TAGS:
        return None
    if text in CANONICAL_TAG:
        return CANONICAL_TAG[text]
    # An unseen tag that is already well-formed snake_case is kept; anything
    # else is prose this module has not been taught to collapse, and guessing
    # would reintroduce the fragmentation.
    return text if _SNAKE_RE.match(text) else None


def is_canonical_tag(value: Optional[str]) -> bool:
    return bool(value) and value in CANONICAL_TAGS


CANONICAL_TAGS = frozenset(CANONICAL_TAG.values())


# --- Categories --------------------------------------------------------------
# Judgement. The schema's seven categories describe what KIND OF WORKS a report
# implies, which is the question an MP's office is actually deciding, so each
# canonical tag is mapped by the work it would fund: a new structure is
# Construction Development, restoring an existing asset is Maintenance &
# Repair, a utility network is Public Utility Works, and so on. Mapping from
# the tag rather than from the legacy category is more precise, because the tag
# names the issue while "Infrastructure" does not.

CATEGORIES = ("Construction Development", "Land Development", "Infrastructure Upgrade",
              "Public Utility Works", "Environmental Project", "Maintenance & Repair", "Other")

TAG_CATEGORY = {
    "road_repair": "Maintenance & Repair",
    "footpath_repair": "Maintenance & Repair",
    "bridge_repair": "Infrastructure Upgrade",
    "water_pipeline_repair": "Public Utility Works",
    "water_supply": "Public Utility Works",
    "water_quality": "Public Utility Works",
    "drainage_improvement": "Infrastructure Upgrade",
    "manhole_repair": "Maintenance & Repair",
    "sewage_overflow": "Public Utility Works",
    "sanitation_works": "Public Utility Works",
    "street_lighting": "Public Utility Works",
    "power_supply": "Public Utility Works",
    "electrical_hazard": "Public Utility Works",
    "garbage_collection": "Maintenance & Repair",
    "carcass_removal": "Maintenance & Repair",
    "stray_animal_control": "Other",
    "vegetation_clearance": "Environmental Project",
    "park_maintenance": "Environmental Project",
    "park_equipment_repair": "Maintenance & Repair",
    "park_development": "Environmental Project",
    "sports_facility_construction": "Construction Development",
    "traffic_signal_repair": "Maintenance & Repair",
    "bus_stop_construction": "Construction Development",
    "unauthorized_parking": "Other",
    "illegal_encroachment": "Land Development",
    "police_patrolling": "Other",
    "school_construction": "Construction Development",
    "hospital_construction": "Construction Development",
}

# Fallback when a row's tag is unknown but its legacy category is not.
LEGACY_CATEGORY = {
    "electricity": "Public Utility Works",
    "water & sanitation": "Public Utility Works",
    "infrastructure": "Infrastructure Upgrade",
    "transport": "Infrastructure Upgrade",
    "public safety": "Other",
    "parks & rec": "Environmental Project",
    "waste management": "Maintenance & Repair",
}


def canonical_category(tag: Optional[str], legacy: Optional[str]) -> Optional[str]:
    if tag and tag in TAG_CATEGORY:
        return TAG_CATEGORY[tag]
    text = (legacy or "").strip()
    if text in CATEGORIES:
        return text
    return LEGACY_CATEGORY.get(text.lower())


# --- Departments per issue type ----------------------------------------------
# The routing rules the triage prompt already states, expressed per canonical
# tag. Used to REPAIR a row whose stored department contradicts its own issue
# type, and to label augmented examples. Not a new policy: it is the prompt's
# own rule set ("Water/sewage -> Jal Sansthan. Electricity -> LESA. Building ->
# LDA.") written where a dataset builder can apply it.

TAG_DEPARTMENT = {
    "street_lighting": "LESA",
    "power_supply": "LESA",
    "electrical_hazard": "LESA",
    "water_supply": "Jal Sansthan",
    "water_quality": "Jal Sansthan",
    "water_pipeline_repair": "Jal Sansthan",
    "sewage_overflow": "Jal Sansthan",
    "drainage_improvement": "Jal Sansthan",
    "illegal_encroachment": "LDA",
}


def department_for(tag: Optional[str], stored: Optional[str]) -> Optional[str]:
    """The department this report belongs to.

    An issue type with a documented owner takes it, because the prompt's rules
    are the system's own definition of correct routing. Everything else keeps
    whatever was stored, which in the seed data is consistent per issue type.
    """
    if tag and tag in TAG_DEPARTMENT:
        return TAG_DEPARTMENT[tag]
    return canonical_department(stored)


# --- Zones -------------------------------------------------------------------
# Judgement, but geographic rather than editorial: each seeded area name is
# placed in the compass zone of Lucknow it actually sits in.

ZONES = ("North", "South", "East", "West", "Central")

AREA_ZONE = {
    "hazratganj": "Central",
    "aminabad": "Central",
    "chowk": "Central",
    "central lucknow": "Central",
    "qaiserbagh": "Central",
    "gomti nagar": "East",
    "indira nagar": "North",
    "mahanagar": "North",
    "aliganj": "North",
    "chinhat": "East",
    "rajajipuram": "West",
    "alambagh": "South",
    "ashiyana": "South",
    "sarojini nagar": "South",
    "jankipuram": "North",
}


def canonical_zone(value: Optional[str]) -> Optional[str]:
    text = (value or "").strip()
    if text in ZONES:
        return text
    return AREA_ZONE.get(text.lower())


# --- Feasibility -------------------------------------------------------------
# The stored `sentiment` measures tone, not feasibility, so it is discarded.
# Scale is a defensible proxy for how complex a work is to deliver: a lamp
# replacement is not a bridge.

FEASIBILITIES = ("High", "Moderate", "Complex", "Unknown")


def feasibility_from_budget(budget: Optional[int]) -> str:
    if budget is None:
        return "Unknown"
    if budget < 1_000_000:
        return "High"
    if budget <= 20_000_000:
        return "Moderate"
    return "Complex"


def canonical_feasibility(stored: Optional[str], budget: Optional[int]) -> str:
    text = (stored or "").strip()
    if text in FEASIBILITIES:
        return text
    return feasibility_from_budget(budget)


# --- Language ----------------------------------------------------------------

LANGUAGE_CODES = {"en": "English", "hi": "Hindi", "ur": "Urdu", "bn": "Bengali",
                  "ta": "Tamil", "te": "Telugu", "mr": "Marathi", "pa": "Punjabi"}


def canonical_language(value: Optional[str]) -> str:
    text = (value or "").strip()
    if not text:
        return "English"
    return LANGUAGE_CODES.get(text.lower(), text.title() if text.islower() else text)


# --- Priority ----------------------------------------------------------------

PRIORITIES = ("Low", "Medium", "High", "Critical")


def canonical_priority(value: Optional[str]) -> Optional[str]:
    text = (value or "").strip().title()
    return text if text in PRIORITIES else None


def as_int(value) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
