"""Deterministic triage, for when no language model can answer.

Keyword rules over English, romanised Hinglish, Hindi and Urdu. Much cruder than
a model, and marked as such on every report it touches (`triage_source: rules`,
`triage_status: failed`), so `/admin/retriage` can replace the result once a
model is reachable again.

Why this exists at all: a report with no category drops out of department
routing and clustering, so a model outage used to mean the system stopped
processing anything while still accepting input. The rules route to the same
departments the triage prompt documents.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

LESA = "LESA / UPPCL"
JAL = "Jal Sansthan (Water Board)"
NIGAM = "Nagar Nigam"
PWD = "Public Works Department (PWD)"
LDA = "Lucknow Development Authority (LDA)"
TRAFFIC = "Traffic Police"
HEALTH = "CMO / Health Department"


@dataclass(frozen=True)
class Issue:
    tag: str
    label: str
    category: str
    department: str
    priority: str
    budget: int
    feasibility: str
    keywords: tuple[str, ...]


# Order matters: the first match wins, so hazards come before the broader issue
# that shares their vocabulary ("sparking transformer" before "no electricity").
ISSUES: tuple[Issue, ...] = (
    Issue("electrical_hazard", "Live electrical hazard", "Public Utility Works", LESA, "Critical", 150_000, "Moderate",
          ("hanging wire", "live wire", "loose wire", "naked wire", "wire is hanging", "wires hanging",
           "transformer", "sparking", "spark", "electric shock", "tar latak", "taar latak", "khula taar", "khula tar",
           "तार लटक", "खुला तार", "ट्रांसफार्मर", "करंट", "चिंगारी", "ٹرانسفارمر", "ننگی تار", "لٹکتی تار")),
    Issue("manhole_repair", "Open manhole", "Maintenance & Repair", JAL, "Critical", 60_000, "High",
          ("manhole", "man hole", "drain cover", "dhakkan", "मैनहोल", "ढक्कन", "مین ہول")),
    Issue("sewage_overflow", "Sewage overflow", "Public Utility Works", JAL, "High", 400_000, "Moderate",
          ("sewage", "sewer", "gutter", "naala", "nala overflow", "ganda nala", "सीवर", "नाला", "नाली", "गटर", "گٹر", "سیوریج")),
    Issue("drainage_improvement", "Waterlogging and drainage", "Infrastructure Upgrade", JAL, "High", 1_200_000, "Complex",
          ("waterlog", "water logging", "water-logging", "flooded", "flooding", "knee deep", "drain is blocked",
           "blocked drain", "jalbharav", "jal bharav", "paani bhar", "pani bhar", "barish ka pani",
           "जलभराव", "जल भराव", "पानी भर", "پانی بھر", "جل بھراؤ")),
    Issue("water_quality", "Contaminated drinking water", "Public Utility Works", JAL, "High", 300_000, "Moderate",
          ("dirty water", "dirty drinking", "contaminated", "smelly water", "muddy water", "ganda pani", "ganda paani",
           "गंदा पानी", "बदबूदार पानी", "گندا پانی")),
    Issue("water_supply", "No water supply", "Public Utility Works", JAL, "High", 500_000, "Moderate",
          ("no water", "water supply", "water tanker", "tanker", "pani nahi", "paani nahi", "पानी नहीं", "पानी की सप्लाई",
           "پانی نہیں")),
    Issue("water_pipeline_repair", "Leaking water pipeline", "Maintenance & Repair", JAL, "Medium", 200_000, "High",
          ("pipe burst", "burst pipe", "broken pipe", "pipeline", "leakage", "water leak", "पाइप", "पाइपलाइन", "پائپ")),
    Issue("power_supply", "Power outage", "Public Utility Works", LESA, "High", 350_000, "Moderate",
          ("power cut", "power outage", "no electricity", "no power", "bijli nahi", "bijli gayab", "light nahi aa",
           "बिजली नहीं", "बिजली कटौती", "बिजली गुल", "بجلی نہیں")),
    Issue("street_lighting", "Street lights not working", "Public Utility Works", LESA, "Medium", 120_000, "High",
          ("street light", "streetlight", "street lamp", "lights are off", "lane is dark", "andhera", "batti band",
           "स्ट्रीट लाइट", "अंधेरा", "اسٹریٹ لائٹ", "اندھیرا")),
    Issue("traffic_signal", "Traffic signal failure", "Infrastructure Upgrade", TRAFFIC, "Medium", 250_000, "Moderate",
          ("traffic signal", "traffic light", "signal not working", "signal kharab", "ट्रैफिक सिग्नल", "ٹریفک سگنل")),
    Issue("road_repair", "Road repair", "Maintenance & Repair", NIGAM, "Medium", 800_000, "Moderate",
          ("pothole", "potholes", "road is broken", "broken road", "damaged road", "road has caved", "gaddha", "gadde",
           "sadak tooti", "sadak kharab", "गड्ढ", "सड़क टूट", "सड़क खराब", "ٹوٹی سڑک", "گڑھا", "گڑھے", "سڑک")),
    Issue("garbage_collection", "Garbage not collected", "Environmental Project", NIGAM, "Medium", 100_000, "High",
          ("garbage", "trash", "waste pile", "dustbin", "overflowing bin", "kachra", "kooda", "kuda",
           "कचरा", "कूड़ा", "कूडा", "کوڑا", "کچرا")),
    Issue("stray_animal_control", "Stray animal menace", "Other", NIGAM, "Medium", 80_000, "High",
          ("stray dog", "stray cattle", "stray animal", "dog bite", "awara kutte", "आवारा", "आवारा कुत्त", "آوارہ")),
    Issue("hospital_upgrade", "Health facility upgrade", "Construction Development", HEALTH, "High", 8_000_000, "Complex",
          ("clinic", "health centre", "health center", "hospital", "phc", "dispensary", "aspatal", "अस्पताल", "ہسپتال")),
    Issue("school_repair", "School building repair", "Construction Development", LDA, "High", 3_500_000, "Complex",
          ("school", "classroom", "anganwadi", "स्कूल", "आंगनवाड़ी", "اسکول")),
    Issue("illegal_construction", "Encroachment or illegal building", "Land Development", LDA, "Medium", 500_000, "Complex",
          ("encroach", "illegal construction", "unauthorised construction", "atikraman", "अतिक्रमण", "تجاوزات")),
    Issue("park_development", "Park maintenance", "Environmental Project", NIGAM, "Low", 400_000, "High",
          ("park", "playground", "swing", "bench", "पार्क", "پارک")),
)

_ARTERIAL = ("highway", "flyover", "underpass", "bridge", "ring road", "shaheed path", "kanpur road",
             "sitapur road", "faizabad road", "raebareli road", "sultanpur road", "hardoi road")

_ESCALATORS = ("children", "school gate", "accident", "someone will die", "danger", "dangerous", "emergency",
               "injured", "hospitalised", "khatra", "bachche", "bachon", "खतरा", "बच्चे", "हादसा", "خطرہ", "بچے")

_PRIORITY_LADDER = ("Low", "Medium", "High", "Critical")

_ROMAN_HINDI = re.compile(r"\b(hai|hain|nahi|nahin|mein|kar|raha|rahi|bahut|kuch|aur|kya|kab|hamare|hamari)\b", re.I)


@dataclass(frozen=True)
class Locality:
    name: str
    lat: float
    lng: float
    zone: str


# Compass zones follow the groupings in main.DEMO_DEMOGRAPHICS, because the
# ranking's deprivation signal is keyed on them.
GAZETTEER: tuple[Locality, ...] = (
    Locality("Hazratganj", 26.8467, 80.9462, "Central"),
    Locality("Aminabad", 26.8466, 80.9226, "Central"),
    Locality("Chowk", 26.8680, 80.9110, "Central"),
    Locality("Charbagh", 26.8320, 80.9220, "Central"),
    Locality("Qaiserbagh", 26.8560, 80.9320, "Central"),
    Locality("Lalbagh", 26.8520, 80.9400, "Central"),
    Locality("Gomti Nagar", 26.8534, 80.9981, "East"),
    Locality("Indira Nagar", 26.8840, 80.9990, "East"),
    Locality("Vibhuti Khand", 26.8590, 81.0060, "East"),
    Locality("Munshipulia", 26.8870, 81.0120, "East"),
    Locality("Alambagh", 26.8160, 80.9000, "South"),
    Locality("Ashiyana", 26.7900, 80.9130, "South"),
    Locality("Telibagh", 26.7740, 80.9360, "South"),
    Locality("Krishna Nagar", 26.7960, 80.8960, "South"),
    Locality("Aliganj", 26.8930, 80.9460, "North"),
    Locality("Mahanagar", 26.8720, 80.9500, "North"),
    Locality("Jankipuram", 26.9200, 80.9440, "North"),
    Locality("Vikas Nagar", 26.8960, 80.9610, "North"),
    Locality("Rajajipuram", 26.8450, 80.8850, "West"),
    Locality("Aishbagh", 26.8410, 80.9030, "West"),
    Locality("Dubagga", 26.8640, 80.8630, "West"),
    Locality("Amausi", 26.7640, 80.8850, "West"),
)


def _match_issue(text: str) -> Optional[Issue]:
    return next((issue for issue in ISSUES if any(k in text for k in issue.keywords)), None)


def _department(issue: Issue, text: str) -> str:
    # The prompt's own rule: an inner-lane pothole is Nagar Nigam, an arterial is PWD.
    if issue.tag == "road_repair" and any(k in text for k in _ARTERIAL):
        return PWD
    return issue.department


def _priority(issue: Issue, text: str) -> str:
    level = _PRIORITY_LADDER.index(issue.priority)
    if any(k in text for k in _ESCALATORS):
        level = min(level + 1, len(_PRIORITY_LADDER) - 1)
    return _PRIORITY_LADDER[level]


def _language(text: str) -> str:
    if any("؀" <= c <= "ۿ" for c in text):
        return "Urdu"
    if any("ऀ" <= c <= "ॿ" for c in text):
        return "Hindi"
    return "Hindi (romanised)" if _ROMAN_HINDI.search(text) else "English"


def find_locality(text: str) -> Optional[Locality]:
    lowered = (text or "").lower()
    return next((loc for loc in GAZETTEER if loc.name.lower() in lowered), None)


def nearest_locality(lat: float, lng: float) -> Locality:
    # Equirectangular distance is plenty at city scale.
    cos_lat = math.cos(math.radians(lat))
    return min(GAZETTEER, key=lambda loc: (loc.lat - lat) ** 2 + ((loc.lng - lng) * cos_lat) ** 2)


def classify(description: str, location_raw: str = "", lat=None, lng=None) -> dict:
    """Same field names as a model result, so callers need no special case."""
    text = (description or "").lower()
    issue = _match_issue(text)

    locality = find_locality(f"{location_raw} {description}")
    if locality is None and lat is not None and lng is not None:
        locality = nearest_locality(lat, lng)
    area = locality.name if locality else None

    result = {
        "category": issue.category if issue else "Other",
        "priority": _priority(issue, text) if issue else "Low",
        "feasibility": issue.feasibility if issue else "Unknown",
        "summary": f"{issue.label} in {area}" if issue and area else (issue.label if issue else "Unclassified civic report"),
        "original_language": _language(description or ""),
        "constituency_zone": locality.zone if locality else "Central",
        "estimated_budget": issue.budget if issue else 100_000,
        "semantic_tag": issue.tag if issue else "general_civic",
        "visual_evidence": "",
        "responsible_body": _department(issue, text) if issue else NIGAM,
    }
    if locality and (lat is None or lng is None):
        result["estimated_latitude"] = locality.lat
        result["estimated_longitude"] = locality.lng
    return result
