"""The golden set.

Where the labels come from matters more than how many there are. These are not
my opinions about civic policy:

* Department routing is asserted straight from the rules the triage prompt
  already states ("Potholes in gali/mohalla -> Nagar Nigam. On highway -> PWD.
  Water/sewage -> Jal Sansthan. Electricity -> LESA. Building -> LDA."). A
  model that breaks its own documented routing is wrong by the system's own
  definition, whatever anyone thinks of the rule.
* Geofence labels are geography: Kanpur is not Lucknow.
* Tag-consistency cases assert only that two phrasings of one real issue get
  the same tag, never what that tag should be. The grouping is the product
  requirement; the wording is the model's business.

Language coverage is deliberate. Script detection handles Devanagari and the
rest, but the most common real input is romanised Hinglish ("pani nahi aa raha
hai"), which scores as English and is the case most likely to regress unnoticed.

This is a seed, not a finished benchmark. The real set should come from
production messages an aide has corrected; `evals/README.md` says how to grow
it. Treat these as the floor that must never break.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TriageCase:
    id: str
    description: str
    location: str = ""
    language: str = "English"
    # Only fields with a defensible ground truth are asserted. None means
    # "not scored", because inventing a label is worse than scoring nothing.
    responsible_body: Optional[str] = None
    priority: Optional[str] = None
    notes: str = ""


@dataclass
class GateCase:
    id: str
    text: str
    expected: str          # "accept" or "reject"
    notes: str = ""


@dataclass
class TagGroup:
    """Different wordings of one real-world issue. They must share a tag."""
    issue: str
    descriptions: list = field(default_factory=list)


# --- Department routing, asserted from the prompt's own stated rules ---------

ROUTING = [
    TriageCase("route-water-1", "Sewage is overflowing onto the street outside our house",
               "Hazratganj", responsible_body="Jal Sansthan",
               notes="prompt: water/sewage -> Jal Sansthan"),
    TriageCase("route-water-2", "No water supply in our area for the last four days",
               "Aliganj", responsible_body="Jal Sansthan"),
    TriageCase("route-power-1", "Street lights have not worked on our lane for two weeks",
               "Gomti Nagar", responsible_body="LESA",
               notes="prompt: electricity -> LESA"),
    TriageCase("route-power-2", "Transformer is sparking dangerously near the school gate",
               "Indira Nagar", responsible_body="LESA", priority="Critical",
               notes="live electrical danger near a school should not be Low"),
    TriageCase("route-building-1", "An illegal building is being constructed without approval",
               "Chowk", responsible_body="LDA", notes="prompt: building -> LDA"),
    TriageCase("route-road-1", "Large potholes in our gali after the rain",
               "Rajajipuram", responsible_body="Nagar Nigam",
               notes="prompt: potholes in gali/mohalla -> Nagar Nigam"),
]

# --- Multilingual and code-mixed --------------------------------------------

LANGUAGES = [
    TriageCase("lang-hindi", "हमारे मोहल्ले में पिछले एक हफ्ते से पानी नहीं आ रहा है",
               "Aminabad", language="Hindi", responsible_body="Jal Sansthan"),
    TriageCase("lang-hinglish-1", "Bhai humare area me pani nahi aa raha hai 4 din se",
               "Alambagh", language="English",
               responsible_body="Jal Sansthan",
               notes="romanised Hinglish scores as English; most common real input"),
    TriageCase("lang-hinglish-2", "Gali me street light kharab hai, raat me bahut andhera rehta hai",
               "Mahanagar", language="English", responsible_body="LESA"),
    TriageCase("lang-urdu", "ہمارے علاقے میں سیوریج کا پانی بھرا ہوا ہے",
               "Chowk", language="Urdu", responsible_body="Jal Sansthan"),
    TriageCase("lang-bengali", "আমাদের এলাকায় রাস্তার আলো জ্বলছে না",
               "Gomti Nagar", language="Bengali", responsible_body="LESA"),
]

# --- Priority ordering ------------------------------------------------------
# Only cases where the ordering is not a matter of taste.

PRIORITY = [
    TriageCase("prio-critical-1", "A live electric wire has fallen on the footpath where children walk",
               "Aliganj", priority="Critical"),
    TriageCase("prio-critical-2", "The boundary wall of the primary school has collapsed",
               "Chinhat", priority="Critical"),
    TriageCase("prio-low-1", "It would be nice to have a few more benches in the park",
               "Hazratganj", priority="Low",
               notes="a comfort request must not outrank a safety hazard"),
]

TRIAGE_CASES = ROUTING + LANGUAGES + PRIORITY

# --- Tag consistency: the grouping is asserted, never the wording ------------

TAG_GROUPS = [
    TagGroup("street_lighting", [
        "MG Road is completely dark at night",
        "Need streetlights on the main road",
        "Gali me street light kharab hai",
        "The lamp posts near our colony have been off for a month",
    ]),
    TagGroup("water_supply", [
        "No water supply for four days",
        "Humare area me pani nahi aa raha",
        "Taps have been dry all week in our mohalla",
    ]),
    TagGroup("road_repair", [
        "Huge potholes on our street",
        "The road is broken and full of craters after the rain",
        "Sadak tooti hui hai, chalna mushkil hai",
    ]),
]

# --- Geofence: geography, not judgement -------------------------------------

GEOFENCE_CASES = [
    GateCase("geo-in-1", "Hazratganj, Lucknow", "accept"),
    GateCase("geo-in-2", "Gomti Nagar", "accept"),
    GateCase("geo-in-3", "Indira Nagar near the market", "accept"),
    GateCase("geo-in-4", "Kanpur Road, Lucknow", "accept",
             notes="a real Lucknow road named after another city; rejecting this silences a citizen"),
    GateCase("geo-out-1", "Sector 18, Noida", "reject"),
    GateCase("geo-out-2", "Near Charminar, Hyderabad", "reject"),
    GateCase("geo-out-3", "Bandra West, Mumbai", "reject"),
    GateCase("geo-unknown-1", "behind the big peepal tree", "accept",
             notes="unrecognised is not out-of-area; it must defer, not reject"),
]

# Coordinates: (id, lat, lng, expected)
GEOFENCE_COORDS = [
    ("coord-hazratganj", 26.8467, 80.9462, "accept"),
    ("coord-gomtinagar", 26.8534, 80.9981, "accept"),
    ("coord-outskirts", 26.9800, 81.1200, "accept", ),
    ("coord-kanpur", 26.4499, 80.3319, "reject"),
    ("coord-delhi", 28.6139, 77.2090, "reject"),
    ("coord-mumbai", 19.0760, 72.8777, "reject"),
]
