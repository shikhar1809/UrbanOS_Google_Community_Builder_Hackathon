"""
UrbanOS Intake Guardrails
=========================

Three independent gates on the WhatsApp intake pipeline. Each one is designed to
fail OPEN on internal error: a broken guardrail must never stop a citizen from
reporting a real civic problem.

1. GEOFENCE      - the report must concern a location inside Lucknow.
2. MEDIA GATE    - attached images must be plausible civic evidence, not selfies,
                   memes, screenshots or random photos.
3. SPAM GUARD    - per-sender rate limits, duplicate suppression and a strike
                   system for repeat abuse.

Ordering matters: the cheap deterministic checks (coordinates, known city names,
rate counters) run before anything that costs a model call.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. GEOFENCE
# ---------------------------------------------------------------------------
# Lucknow Nagar Nigam covers roughly a 25 km radius around Hazratganj. The
# district (including Bakshi Ka Talab, Malihabad, Mohanlalganj) reaches further,
# so the accept radius is generous and the "warn" band beyond it is explicit.

LUCKNOW_CENTER = (26.8467, 80.9462)
LUCKNOW_RADIUS_KM = 35.0          # accept
LUCKNOW_SOFT_RADIUS_KM = 55.0     # accept with a flag; beyond this, reject

# Cities that are definitely NOT Lucknow. If a citizen names one of these, the
# report belongs to another municipal body.
OTHER_CITIES = {
    "kanpur", "delhi", "new delhi", "noida", "ghaziabad", "gurgaon", "gurugram",
    "mumbai", "bombay", "pune", "bangalore", "bengaluru", "chennai", "madras",
    "hyderabad", "kolkata", "calcutta", "ahmedabad", "surat", "jaipur", "bhopal",
    "indore", "nagpur", "patna", "ranchi", "varanasi", "banaras", "kashi",
    "allahabad", "prayagraj", "agra", "meerut", "aligarh", "bareilly", "gorakhpur",
    "moradabad", "saharanpur", "jhansi", "mathura", "faizabad", "ayodhya",
    "sultanpur", "raebareli", "rae bareli", "hardoi", "sitapur", "unnao",
    "barabanki", "lakhimpur", "amethi", "pratapgarh", "jaunpur", "azamgarh",
    "chandigarh", "lucknow airport road kanpur",
}

# Known Lucknow localities. A positive match short-circuits the model call.
LUCKNOW_LOCALITIES = {
    "lucknow", "lko", "hazratganj", "gomti nagar", "gomtinagar", "indira nagar",
    "indiranagar", "aliganj", "aminabad", "chowk", "alambagh", "rajajipuram",
    "mahanagar", "ashiyana", "jankipuram", "vikas nagar", "vikasnagar",
    "nirala nagar", "chinhat", "faizabad road", "kanpur road", "sitapur road",
    "hardoi road", "raebareli road", "sultanpur road", "amausi", "krishna nagar",
    "telibagh", "sarojini nagar", "bakshi ka talab", "bkt", "malihabad",
    "mohanlalganj", "gosainganj", "itaunja", "kakori", "dubagga",
    "thakurganj", "daliganj", "nishatganj", "khurram nagar", "kursi road",
    "vrindavan yojana", "shaheed path", "charbagh", "naka hindola", "husainganj",
    "lalbagh", "qaiserbagh", "aishbagh", "balaganj", "chowpatty", "madiaon",
    "vibhuti khand", "vinay khand", "vishal khand", "viram khand", "viraj khand",
    "patrakarpuram", "polytechnic", "munshipulia", "bhootnath", "lekhraj",
    "cantonment", "dilkusha", "sadar", "banthra", "nadarganj", "talkatora",
    "rakabganj", "yahiyaganj", "saadatganj", "hasanganj", "narhi", "jopling road",
    "butler colony", "gokhale marg", "park road", "vidhan sabha", "lohia path",
    "janpath", "riverside", "ekana", "sushant golf city", "eldeco", "omaxe",
    "anora kala", "utrathia", "nilmatha", "transport nagar", "scooter india",
    "ghaila", "kalyanpur", "triveni nagar",
}

# Deliberately NOT in the set above: "sector", "colony", "ward", "nagar", "road".
# Every Indian city has those, so treating them as evidence of Lucknow let
# "garbage in Kanpur sector 5" through the geofence. Generic text now returns
# "unknown" and defers to the classifier, which is the safe direction.


def haversine_km(a: tuple, b: tuple) -> float:
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def check_coordinates(lat, lng) -> dict:
    """Hard geofence on a GPS pin. Coordinates cannot be argued with, so this is
    the strongest signal available and runs before any text heuristics."""
    if lat is None or lng is None:
        return {"verdict": "unknown", "reason": "no coordinates supplied"}
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return {"verdict": "unknown", "reason": "unparseable coordinates"}
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return {"verdict": "reject", "reason": "coordinates out of valid range", "distance_km": None}

    d = haversine_km((lat, lng), LUCKNOW_CENTER)
    if d <= LUCKNOW_RADIUS_KM:
        return {"verdict": "accept", "reason": f"{d:.1f} km from Lucknow centre", "distance_km": round(d, 1)}
    if d <= LUCKNOW_SOFT_RADIUS_KM:
        return {"verdict": "accept_flagged",
                "reason": f"{d:.1f} km from Lucknow centre - outer district",
                "distance_km": round(d, 1)}
    return {"verdict": "reject",
            "reason": f"{d:.0f} km from Lucknow - outside municipal jurisdiction",
            "distance_km": round(d, 1)}


def check_location_text(text: str) -> dict:
    """Cheap text geofence. Only rejects when another city is named explicitly and
    no Lucknow locality is mentioned; everything else defers to the model, because
    the long tail of colony and gali names is not enumerable."""
    if not text or not text.strip():
        return {"verdict": "unknown", "reason": "no location text"}
    t = " " + re.sub(r"[^a-z0-9\s]", " ", text.lower()) + " "

    hit_local = next((loc for loc in LUCKNOW_LOCALITIES if f" {loc} " in t), None)
    hit_other = next((c for c in OTHER_CITIES if f" {c} " in t), None)

    if hit_local and not hit_other:
        return {"verdict": "accept", "reason": f"recognised Lucknow locality '{hit_local}'"}
    if hit_other and not hit_local:
        return {"verdict": "reject", "reason": f"names another city: '{hit_other.title()}'", "city": hit_other.title()}
    if hit_other and hit_local:
        # e.g. "Kanpur Road, Lucknow" - a real Lucknow road named after another city.
        return {"verdict": "accept", "reason": f"'{hit_other}' appears but so does Lucknow locality '{hit_local}'"}
    return {"verdict": "unknown", "reason": "no recognised place name"}


def build_location_check_prompt(description: str, location_text: str) -> str:
    """Merged jurisdiction + geography classifier.

    Folded into one call rather than two: the pipeline already pays for a
    topical check, and asking both questions at once keeps intake latency flat.
    """
    return (
        "You screen incoming citizen complaints for UrbanOS, the grievance system of "
        "LUCKNOW, Uttar Pradesh, India. Answer with a single line in the exact format:\n"
        "VERDICT|REASON\n"
        "where VERDICT is one of ACCEPT, REJECT_TOPIC, REJECT_LOCATION.\n\n"
        "ACCEPT - a real urban civic issue (road, water, sewage, electricity, garbage, "
        "streetlight, drainage, construction, park, transport, stray animals, public health) "
        "AND the location is in Lucknow or not specified at all.\n"
        "REJECT_TOPIC - not a municipal matter (personal dispute, national politics, "
        "astrology, entertainment, spam, abuse, a private commercial issue).\n"
        "REJECT_LOCATION - a civic issue, but clearly located in a city other than Lucknow.\n\n"
        "If the location is vague, missing, or is any Lucknow locality, treat it as Lucknow.\n"
        "REASON must be under 15 words.\n\n"
        f"Complaint: {description}\n"
        f"Stated location: {location_text or 'not provided'}"
    )


def parse_location_check(raw: str) -> dict:
    """Parse the classifier line, defaulting to accept on anything unexpected."""
    if not raw:
        return {"verdict": "accept", "reason": "classifier returned nothing"}
    line = raw.strip().splitlines()[0]
    parts = line.split("|", 1)
    token = parts[0].strip().upper()
    reason = parts[1].strip() if len(parts) > 1 else ""
    if token.startswith("REJECT_LOCATION"):
        return {"verdict": "reject_location", "reason": reason or "outside Lucknow"}
    if token.startswith("REJECT_TOPIC"):
        return {"verdict": "reject_topic", "reason": reason or "not a municipal matter"}
    return {"verdict": "accept", "reason": reason}


# ---------------------------------------------------------------------------
# 2. MEDIA GATE
# ---------------------------------------------------------------------------

IMAGE_CLASSIFIER_PROMPT = (
    "You are screening a photo attached to a municipal complaint in Lucknow, India.\n"
    "Classify what the photo shows. Answer on ONE line, exactly:\n"
    "LABEL|CONFIDENCE|REASON\n\n"
    "LABEL must be one of:\n"
    "  CIVIC        - infrastructure or public space: road, pothole, garbage, drain, sewage, "
    "streetlight, wires, transformer, water leak, park, building work, stray animals, "
    "flooding, traffic, damaged public property, or any outdoor street scene relevant to a complaint.\n"
    "  PERSON       - a selfie or portrait where a person is the subject and no civic issue is visible.\n"
    "  SCREENSHOT   - a screenshot, chat capture, or photo of a phone or computer screen.\n"
    "  DOCUMENT     - a paper document, bill, form, ID card or letter.\n"
    "  IRRELEVANT   - memes, food, pets, entertainment, adult content, advertising, or anything "
    "with no possible connection to a civic complaint.\n"
    "  UNCLEAR      - too dark, blurred or ambiguous to judge.\n\n"
    "CONFIDENCE is HIGH, MEDIUM or LOW. REASON is under 12 words.\n"
    "A photo showing ANY street, public area or infrastructure is CIVIC even if a person appears in it."
)

# DOCUMENT is allowed through: citizens legitimately attach bills for utility and
# tax complaints. UNCLEAR is allowed through because a dark photo of a broken
# streetlight at night is exactly the evidence the system exists to collect.
_ACCEPTED_IMAGE_LABELS = {"CIVIC", "DOCUMENT", "UNCLEAR"}
_REJECT_MESSAGES = {
    "PERSON": "that looks like a personal photo rather than the civic issue",
    "SCREENSHOT": "that looks like a screenshot rather than a photo of the issue",
    "IRRELEVANT": "that photo does not appear to show a civic issue",
}


def parse_image_verdict(raw: str) -> dict:
    """Parse the vision classifier. Fails open: an unreadable answer accepts."""
    if not raw:
        return {"accept": True, "label": "UNCLEAR", "confidence": "LOW", "reason": "no classifier output"}
    parts = [p.strip() for p in raw.strip().splitlines()[0].split("|")]
    label = (parts[0] if parts else "UNCLEAR").upper()
    label = next((l for l in ("CIVIC", "PERSON", "SCREENSHOT", "DOCUMENT", "IRRELEVANT", "UNCLEAR")
                  if label.startswith(l)), "UNCLEAR")
    confidence = (parts[1].upper() if len(parts) > 1 else "LOW")
    reason = parts[2] if len(parts) > 2 else ""

    # Only act on a rejection the model is actually sure about. A LOW-confidence
    # "PERSON" is far more likely to be a street photo with a bystander in it
    # than an attempt to abuse the system.
    accept = label in _ACCEPTED_IMAGE_LABELS or confidence == "LOW"
    return {
        "accept": accept,
        "label": label,
        "confidence": confidence,
        "reason": reason,
        "message": _REJECT_MESSAGES.get(label, "that photo does not appear to show a civic issue"),
    }


# ---------------------------------------------------------------------------
# 3. SPAM GUARD
# ---------------------------------------------------------------------------

LIMITS = {
    "burst_messages": 15,      # inbound messages ...
    "burst_window_s": 60,      # ... per this many seconds
    "reports_per_hour": 6,
    "reports_per_day": 20,
    "duplicate_window_h": 24,
    "strikes_to_block": 5,
    "block_minutes": 60,
}


def normalize_text(text: str) -> str:
    """Collapse a complaint to a comparable form so trivially reworded resubmissions
    of the same grievance still register as duplicates."""
    t = (text or "").lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def text_fingerprint(text: str) -> str:
    return hashlib.sha1(normalize_text(text).encode("utf-8")).hexdigest()[:16]


class SpamGuard:
    """Firestore-backed rate limiting and duplicate suppression.

    State lives in a `rate_limits` document per sender so limits survive process
    restarts and hold across instances, which an in-memory counter cannot do on
    a serverless runtime. Every method fails open.
    """

    COLLECTION = "rate_limits"

    def __init__(self, db):
        self.db = db

    def _doc(self, sender: str):
        return self.db.collection(self.COLLECTION).document(hashlib.sha1(sender.encode()).hexdigest()[:24])

    def _load(self, sender: str) -> dict:
        try:
            snap = self._doc(sender).get()
            return snap.to_dict() or {} if snap.exists else {}
        except Exception as e:
            logger.warning(f"[SPAMGUARD] load failed for {sender}: {e}")
            return {}

    def _save(self, sender: str, state: dict) -> None:
        try:
            state["sender"] = sender
            state["updated_at"] = datetime.now().isoformat()
            self._doc(sender).set(state)
        except Exception as e:
            logger.warning(f"[SPAMGUARD] save failed for {sender}: {e}")

    def check_message(self, sender: str) -> dict:
        """Called on every inbound message, before any work is done."""
        now = time.time()
        state = self._load(sender)

        blocked_until = state.get("blocked_until", 0)
        if blocked_until and now < blocked_until:
            mins = max(1, int((blocked_until - now) / 60))
            return {"allow": False, "reason": "blocked", "retry_minutes": mins,
                    "message": f"Too many messages. Please try again in about {mins} minute(s)."}

        stamps = [t for t in state.get("msg_times", []) if now - t < LIMITS["burst_window_s"]]
        stamps.append(now)
        state["msg_times"] = stamps[-60:]

        if len(stamps) > LIMITS["burst_messages"]:
            state["strikes"] = state.get("strikes", 0) + 1
            if state["strikes"] >= LIMITS["strikes_to_block"]:
                state["blocked_until"] = now + LIMITS["block_minutes"] * 60
                state["strikes"] = 0
                self._save(sender, state)
                return {"allow": False, "reason": "flood_blocked", "retry_minutes": LIMITS["block_minutes"],
                        "message": (f"You have sent a lot of messages very quickly, so this number is paused "
                                    f"for {LIMITS['block_minutes']} minutes. Genuine reports are always welcome "
                                    f"after that.")}
            self._save(sender, state)
            return {"allow": False, "reason": "rate_limited",
                    "message": "You are sending messages very quickly. Please wait a moment and try again."}

        self._save(sender, state)
        return {"allow": True, "reason": "ok"}

    def check_submission(self, sender: str, description: str) -> dict:
        """Called just before a report is written. Enforces submission quotas and
        rejects a repeat of something this sender already filed."""
        now = time.time()
        state = self._load(sender)

        hour_ago, day_ago = now - 3600, now - 86400
        subs = [t for t in state.get("submit_times", []) if t > day_ago]
        if len([t for t in subs if t > hour_ago]) >= LIMITS["reports_per_hour"]:
            return {"allow": False, "reason": "hourly_quota",
                    "message": (f"You have already filed {LIMITS['reports_per_hour']} reports this hour. "
                                "Please wait before filing another so field teams can work through them.")}
        if len(subs) >= LIMITS["reports_per_day"]:
            return {"allow": False, "reason": "daily_quota",
                    "message": (f"You have reached the daily limit of {LIMITS['reports_per_day']} reports. "
                                "Please try again tomorrow.")}

        fp = text_fingerprint(description)
        cutoff = (datetime.now() - timedelta(hours=LIMITS["duplicate_window_h"])).isoformat()
        for entry in state.get("recent_reports", []):
            if entry.get("fp") == fp and entry.get("at", "") > cutoff:
                return {"allow": False, "reason": "duplicate", "reference_id": entry.get("ref"),
                        "message": (f"You have already reported this. Your existing reference is "
                                    f"#{entry.get('ref')}. Reply STATUS {entry.get('ref')} for an update.")}
        return {"allow": True, "reason": "ok", "fingerprint": fp}

    def record_submission(self, sender: str, description: str, reference_id: str) -> None:
        """Called after a report is successfully written."""
        now = time.time()
        state = self._load(sender)
        subs = [t for t in state.get("submit_times", []) if t > now - 86400]
        subs.append(now)
        state["submit_times"] = subs
        recent = state.get("recent_reports", [])
        recent.append({"fp": text_fingerprint(description), "ref": reference_id,
                       "at": datetime.now().isoformat()})
        state["recent_reports"] = recent[-40:]
        self._save(sender, state)

    def record_rejection(self, sender: str, kind: str) -> int:
        """Track off-topic or out-of-area submissions. Persistent abuse earns a
        temporary block; an occasional mistake costs nothing."""
        now = time.time()
        state = self._load(sender)
        state["strikes"] = state.get("strikes", 0) + 1
        state.setdefault("rejections", []).append({"kind": kind, "at": datetime.now().isoformat()})
        state["rejections"] = state["rejections"][-20:]
        if state["strikes"] >= LIMITS["strikes_to_block"]:
            state["blocked_until"] = now + LIMITS["block_minutes"] * 60
            state["strikes"] = 0
        self._save(sender, state)
        return state.get("strikes", 0)
