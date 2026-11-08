"""Simulated live data stream.

Generates citizens and walks each one through the production intake conversation
(main._process_whatsapp_sync), so a simulated report meets the same guardrails,
triage, clustering and dashboard as a real one. Only the citizen is simulated.

What makes it safe to point at the real `messages` collection:

* Every report carries `simulated: true` and a run id, and purge() removes them.
* Senders use the reserved +999 prefix, so they belong to no real phone, and the
  sanction and survey senders skip them (sim.is_simulated_sender).
* Conversation state and rate limits stay in stream_* collections.

Driven by ticks from the browser rather than a background loop: a Cloud Function
is throttled once it has answered, and a scheduler would keep spending quota
after the demo ended. Closing the tab stops the stream.

The run document holds counters and a short tail of recent events, so a viewer
polling state costs one Firestore read however long the run has been going.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import random
import time
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Optional

from fastapi import BackgroundTasks

import sim
from state import clear_session, get_session
from triage_rules import GAZETTEER, Locality

logger = logging.getLogger(__name__)

CONTROL_COLLECTION = "stream_control"
CONTROL_DOC = "current"
FILES_COLLECTION = "stream_files"
FILE_CHUNKS_COLLECTION = "stream_file_chunks"
FILES_LISTED = 50
MAX_FILE_CHUNKS = 1000  # a run is capped at 1000 events, so this always covers one
FILE_COLUMNS = ("at", "outcome", "kind", "reason", "name", "phone", "language", "text", "place", "location",
                "lat", "lng", "reference_id", "category", "priority", "department", "ward", "officer", "zone",
                "tag", "summary", "triage_source", "pipeline_ms", "reply")

MAX_BATCH = 5
MIN_TICK_GAP_SECONDS = 0.8
MIN_RATE, MAX_RATE = 6, 120
DEFAULT_MAX_EVENTS, HARD_MAX_EVENTS = 300, 1000
# With no tick for this long, the tab driving the run has gone away.
STALE_AFTER_SECONDS = 30
RECENT_EVENTS_KEPT = 40
TOP_CLUSTERS = 8
PURGE_BATCH = 400


class StreamError(Exception):
    """A request the current run state cannot honour."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ---------------------------------------------------------------------------
# Corpus. {place} is filled in at generation time.
# ---------------------------------------------------------------------------

COMPLAINTS: dict[str, list[tuple[str, str]]] = {
    "drainage_improvement": [
        ("en", "Water logging near {place} after every rain, the drain is blocked and the road disappears."),
        ("en", "Our lane in {place} is flooded knee deep since last night. Shops have water inside."),
        ("hing", "{place} mein barish ke baad paani bhar gaya hai, nikalne ka koi rasta nahi hai."),
        ("hi", "{place} में जलभराव की समस्या है, बारिश के बाद सड़क पर चलना मुश्किल है।"),
        ("ur", "{place} میں پانی بھر گیا ہے، گلی سے گزرنا مشکل ہے۔"),
    ],
    "sewage_overflow": [
        ("en", "Sewage is overflowing onto the road in {place}, the smell is unbearable."),
        ("hing", "{place} mein ganda nala overflow ho raha hai, ghar ke saamne sewer ka paani hai."),
        ("hi", "{place} में सीवर का पानी सड़क पर बह रहा है, बहुत बदबू है।"),
    ],
    "manhole_repair": [
        ("en", "There is an open manhole on the main road in {place}, children walk past it every day."),
        ("hi", "{place} में मैनहोल का ढक्कन टूटा है, कोई भी गिर सकता है।"),
    ],
    "water_supply": [
        ("en", "No water supply in {place} for four days, we are buying tankers."),
        ("hing", "{place} mein teen din se pani nahi aa raha hai, bahut pareshani hai."),
        ("hi", "{place} में दो दिन से पानी नहीं आ रहा है।"),
    ],
    "water_quality": [
        ("en", "Dirty drinking water coming from the taps in {place}, people are falling sick."),
        ("hi", "{place} में नल से गंदा पानी आ रहा है, बच्चे बीमार हो रहे हैं।"),
    ],
    "electrical_hazard": [
        ("en", "A live wire is hanging low over the street in {place}, it is dangerous for children."),
        ("en", "Transformer near {place} market is sparking since morning."),
        ("hing", "{place} mein khula taar latak raha hai, bahut khatra hai."),
        ("hi", "{place} में ट्रांसफार्मर से चिंगारी निकल रही है।"),
    ],
    "power_supply": [
        ("en", "Power cut in {place} for the last ten hours, no update from anyone."),
        ("hing", "{place} mein kal raat se bijli nahi hai."),
        ("ur", "{place} میں کل سے بجلی نہیں ہے۔"),
    ],
    "street_lighting": [
        ("en", "Street lights on our road in {place} have not worked for two weeks, it is unsafe at night."),
        ("hi", "{place} की गली में स्ट्रीट लाइट खराब है, रात में अंधेरा रहता है।"),
    ],
    "road_repair": [
        ("en", "Huge potholes on the inner road in {place}, two bikes fell this week."),
        ("hing", "{place} ki sadak kharab hai, bade gadde hain, roz accident hota hai."),
        ("hi", "{place} में सड़क टूटी हुई है और गड्ढे हैं।"),
    ],
    "garbage_collection": [
        ("en", "Garbage has not been collected in {place} for five days, the bin is overflowing."),
        ("hing", "{place} mein kachra nahi uthaya gaya hai ek hafte se."),
        ("hi", "{place} में कूड़ा नहीं उठाया जा रहा है।"),
        ("ur", "{place} میں کوڑا نہیں اٹھایا گیا۔"),
    ],
    "stray_animal_control": [
        ("en", "Stray dogs are chasing children in {place}, one child was bitten yesterday."),
        ("hi", "{place} में आवारा कुत्तों का आतंक है।"),
    ],
    "traffic_signal": [
        ("en", "The traffic signal at {place} crossing has been dead since Monday, jams every evening."),
    ],
    "school_repair": [
        ("en", "The roof of the government school in {place} leaks into the classroom during rain."),
        ("hi", "{place} के स्कूल की छत टूटी हुई है, बच्चों को खतरा है।"),
    ],
    "park_development": [
        ("en", "The park in {place} has broken swings and overgrown weeds, kids cannot use it."),
    ],
    "illegal_construction": [
        ("en", "Illegal construction blocking the lane in {place}, nobody has acted on it."),
        ("hi", "{place} में अतिक्रमण से रास्ता बंद हो गया है।"),
    ],
}

LANGUAGE_LABELS = {"en": "English", "hing": "Hinglish", "hi": "Hindi", "ur": "Urdu"}
LANDMARKS = ("near the main market", "behind the police chowki", "next to the temple", "opposite the school",
             "by the water tank", "near the bus stand", "behind the masjid", "near the crossing")
NAMES = ("Sunita", "Rakesh", "Farah", "Imran", "Anjali", "Vikas", "Pooja", "Salim", "Neha", "Arun", "Meena", "Tariq")

OTHER_CITY_PINS = (("Kanpur", 26.4499, 80.3319), ("Delhi", 28.6139, 77.2090), ("Varanasi", 25.3176, 82.9739))
OFF_TOPIC_TEXTS = ("What is the cricket score today?", "Please send me today's horoscope.",
                   "Earn fast money with bitcoin, forward this message to ten friends.",
                   "Tell me a joke please.", "Need a personal loan urgently, please arrange.")
OUT_OF_AREA_TEXT = ("Sewage overflowing on the main road in {city}, please fix this.",
                    "Potholes everywhere in {city} sector 5, nobody is listening.")

# Keyed by zone so a surge concentrates demand somewhere an MP could act on.
_BY_ZONE: dict[str, list[Locality]] = {}
for _loc in GAZETTEER:
    _BY_ZONE.setdefault(_loc.zone, []).append(_loc)

SCENARIOS: dict[str, dict] = {
    "steady": {
        "label": "Steady city traffic",
        "description": "A normal day: every issue type, every zone, a little noise.",
        "focus": None, "zones": None, "focus_share": 0.0, "noise": 0.10,
    },
    "monsoon": {
        "label": "Monsoon surge, East Lucknow",
        "description": "Flooding hits Gomti Nagar and Indira Nagar. Watch one project climb the queue.",
        "focus": ("drainage_improvement", "sewage_overflow", "manhole_repair"),
        "zones": ("East",), "focus_share": 0.75, "noise": 0.05,
    },
    "outage": {
        "label": "Power failure, North Lucknow",
        "description": "A grid fault across Aliganj and Jankipuram, with live-wire hazards mixed in.",
        "focus": ("power_supply", "electrical_hazard", "street_lighting"),
        "zones": ("North",), "focus_share": 0.75, "noise": 0.05,
    },
    "adversarial": {
        "label": "Adversarial traffic",
        "description": "40% junk: other cities, off-topic messages and repeats. Watch the guardrails work.",
        "focus": None, "zones": None, "focus_share": 0.0, "noise": 0.40,
    },
}


def _phone(rng: random.Random) -> str:
    return sim.SIMULATED_PHONE_PREFIX + "".join(rng.choice("0123456789") for _ in range(9))


def _mask(phone: str) -> str:
    return f"{sim.SIMULATED_PHONE_PREFIX} •••• {phone[-4:]}"


def _civic_event(rng: random.Random, scenario: dict, kind: str = "citizen") -> dict:
    focused = scenario["focus"] and rng.random() < scenario["focus_share"]
    tag = rng.choice(scenario["focus"]) if focused else rng.choice(list(COMPLAINTS))
    places = (_BY_ZONE[rng.choice(scenario["zones"])] if focused else GAZETTEER)
    place = rng.choice(places)
    lang, template = rng.choice(COMPLAINTS[tag])

    event = {
        "kind": kind,
        "name": rng.choice(NAMES),
        "phone": _phone(rng),
        "text": template.format(place=place.name),
        "language": LANGUAGE_LABELS[lang],
        "place": place.name,
    }
    if rng.random() < 0.6:
        event["lat"] = round(place.lat + rng.uniform(-0.008, 0.008), 6)
        event["lng"] = round(place.lng + rng.uniform(-0.008, 0.008), 6)
        event["location_label"] = f"GPS pin, {place.name}"
    else:
        event["location_text"] = f"{rng.choice(LANDMARKS)}, {place.name}"
        event["location_label"] = event["location_text"]
    return event


def generate_event(scenario_key: str, rng: Optional[random.Random] = None) -> dict:
    """One simulated citizen. Noise is what the guardrails exist to catch."""
    rng = rng or random.Random()
    scenario = SCENARIOS[scenario_key]

    if rng.random() >= scenario["noise"]:
        return _civic_event(rng, scenario)

    noise = rng.choice(("out_of_area_pin", "out_of_area_text", "off_topic", "duplicate"))
    event = _civic_event(rng, scenario, kind=noise)
    if noise == "out_of_area_pin":
        city, lat, lng = rng.choice(OTHER_CITY_PINS)
        event.update(lat=lat, lng=lng, place=city, location_label=f"GPS pin, {city}")
        event.pop("location_text", None)
    elif noise == "out_of_area_text":
        city, _, _ = rng.choice(OTHER_CITY_PINS)
        event.update(text=rng.choice(OUT_OF_AREA_TEXT).format(city=city), language="English", place=city,
                     location_text=city, location_label=city)
        event.pop("lat", None)
        event.pop("lng", None)
    elif noise == "off_topic":
        event.update(text=rng.choice(OFF_TOPIC_TEXTS), language="English")
    return event


# ---------------------------------------------------------------------------
# Running one citizen through the real pipeline
# ---------------------------------------------------------------------------

def _twiml_bodies(xml_bytes: bytes) -> list[str]:
    return [m.text or "" for m in ET.fromstring(xml_bytes).iter("Message")]


def _conversation(event: dict, sender: str) -> list[dict]:
    """The shortest real conversation that files a report. 'new report' is the
    production shortcut past the name and intent prompts."""
    location = ({"Body": "", "Latitude": str(event["lat"]), "Longitude": str(event["lng"])}
                if "lat" in event else {"Body": event["location_text"]})
    return [
        {"From": sender, "Body": "new report"},
        {"From": sender, "Body": event["text"]},
        {"From": sender, **location},
        {"From": sender, "Body": "skip"},
    ]


def _rejection_reason(event: dict, replies: list[str]) -> str:
    text = " ".join(replies).lower()
    if "already reported" in text:
        return "duplicate"
    if "outside lucknow" in text or "outside municipal" in text or event["kind"].startswith("out_of_area"):
        return "out_of_area"
    if "jurisdiction" in text or event["kind"] == "off_topic":
        return "off_topic"
    if "this hour" in text or "daily limit" in text or "very quickly" in text:
        return "rate_limited"
    return "guardrail"


def _run_conversation(event: dict, pipeline: Callable, db, run_id: str) -> tuple[list[dict], list[str]]:
    sender = "whatsapp:" + event["phone"]
    tags = {"simulated": True, "stream_run": run_id, "stream_kind": event["kind"]}
    filed: list[dict] = []
    replies: list[str] = []
    outbox: list[str] = []
    steps = _conversation(event, sender)

    # Context variables do not cross into a worker thread on their own, so they
    # are set here, inside the thread doing the work.
    with sim.session(db, outbox), sim.tagging_reports(tags, filed):
        for index, form in enumerate(steps):
            response = pipeline(form, BackgroundTasks(), db)
            replies.extend(_twiml_bodies(response.body))
            # A rejected location clears the session. Sending the next step would
            # open a brand-new conversation instead of finishing this one.
            if index == 2 and get_session(db, sender) is None:
                break
    replies.extend(outbox)
    clear_session(db, sender)
    return filed, replies


def _result(event: dict, filed: list[dict], replies: list[str], started: float) -> dict:
    result = {
        "id": uuid.uuid4().hex[:12],
        "at": time.time(),
        "kind": event["kind"],
        "name": event["name"],
        "phone": _mask(event["phone"]),
        "text": event["text"],
        "language": event["language"],
        "place": event["place"],
        "location": event["location_label"],
        "lat": event.get("lat"),
        "lng": event.get("lng"),
        "reply": next((r for r in reversed(replies) if r.strip()), "")[:220],
        "pipeline_ms": int((time.perf_counter() - started) * 1000),
    }
    if not filed:
        result.update(outcome="rejected", reason=_rejection_reason(event, replies))
        return result

    report = filed[0]
    result.update(
        outcome="filed",
        reference_id=report.get("reference_id"),
        category=report.get("category"),
        priority=report.get("priority"),
        department=report.get("responsible_body"),
        ward=report.get("ward_no"),
        officer=report.get("ward_officer"),
        zone=report.get("constituency_zone"),
        tag=report.get("semantic_tag"),
        summary=report.get("summary"),
        triage_source=report.get("triage_source") or "unknown",
    )
    return result


def process_event(event: dict, pipeline: Callable, raw_db, run_id: str) -> list[dict]:
    """Returns one result per conversation. Never raises: one citizen failing
    must not take the rest of the batch with it."""
    db = sim.StreamDB(raw_db)
    started = time.perf_counter()
    try:
        filed, replies = _run_conversation(event, pipeline, db, run_id)
        results = [_result(event, filed, replies, started)]
        if event["kind"] == "duplicate":
            # The same citizen sends it again; the second must be refused.
            again = time.perf_counter()
            filed2, replies2 = _run_conversation(event, pipeline, db, run_id)
            results.append(_result(event, filed2, replies2, again))
        return results
    except Exception as e:
        logger.exception("[STREAM] event failed: %s", e)
        return [{
            "id": uuid.uuid4().hex[:12], "at": time.time(), "kind": event["kind"], "name": event["name"],
            "phone": _mask(event["phone"]), "text": event["text"], "language": event["language"],
            "place": event["place"], "location": event["location_label"], "reply": "",
            "pipeline_ms": int((time.perf_counter() - started) * 1000),
            "outcome": "error", "reason": type(e).__name__,
        }]


# ---------------------------------------------------------------------------
# Run state
# ---------------------------------------------------------------------------

def _control(raw_db):
    return raw_db.collection(CONTROL_COLLECTION).document(CONTROL_DOC)


def _load(raw_db) -> dict:
    snap = _control(raw_db).get()
    return (snap.to_dict() or {}) if snap.exists else {}


def _is_live(state: dict, now: Optional[float] = None) -> bool:
    now = now or time.time()
    return state.get("status") == "running" and now - state.get("last_tick_at", 0) < STALE_AFTER_SECONDS


def _empty_run(run_id: str, scenario: str, rate: int, max_events: int) -> dict:
    now = time.time()
    return {
        "run_id": run_id, "status": "running", "scenario": scenario, "rate_per_min": rate,
        "max_events": max_events, "started_at": now, "last_tick_at": now, "events": 0, "filed": 0,
        "rejected": 0, "errors": 0, "pipeline_ms_total": 0, "by_priority": {}, "by_source": {},
        "by_reason": {}, "clusters": {}, "recent": [],
    }


def start(raw_db, scenario: str, rate_per_min: int, max_events: int) -> dict:
    if scenario not in SCENARIOS:
        raise StreamError(422, "Unknown scenario")
    current = _load(raw_db)
    if _is_live(current):
        raise StreamError(409, "A stream is already running. Stop it first.")
    run = _empty_run(uuid.uuid4().hex[:10], scenario,
                     max(MIN_RATE, min(rate_per_min, MAX_RATE)),
                     max(1, min(max_events, HARD_MAX_EVENTS)))
    _control(raw_db).set(run)
    _write_file_index(raw_db, run)
    logger.info("[STREAM] run %s started: %s at %s/min", run["run_id"], scenario, run["rate_per_min"])
    return public_state(run)


def stop(raw_db) -> dict:
    state = _load(raw_db)
    if state:
        state["status"] = "stopped"
        _control(raw_db).set(state)
        _write_file_index(raw_db, state)
    return public_state(state)


# ---------------------------------------------------------------------------
# Data files: the complete record of a run
# ---------------------------------------------------------------------------
# The run document keeps only a short tail, and refused messages never reach
# `messages`, so neither can answer "what exactly was sent?". Each tick appends
# its results as one document, which costs one write however large the batch.

def _write_file_index(raw_db, state: dict) -> None:
    # A failed write here loses part of an export, not a citizen's report, so it
    # must not stop the stream.
    try:
        raw_db.collection(FILES_COLLECTION).document(state["run_id"]).set({
            "run_id": state["run_id"],
            "scenario": state["scenario"],
            "scenario_label": SCENARIOS[state["scenario"]]["label"],
            "status": state["status"],
            "started_at": state["started_at"],
            "last_tick_at": state["last_tick_at"],
            "events": state["events"],
            "filed": state["filed"],
            "rejected": state["rejected"],
            "errors": state["errors"],
        })
    except Exception as e:
        logger.warning("[STREAM] could not update the file index: %s", e)


def _append_to_file(raw_db, run_id: str, seq: int, results: list[dict]) -> None:
    try:
        raw_db.collection(FILE_CHUNKS_COLLECTION).document(f"{run_id}-{seq:06d}").set({
            "run_id": run_id, "seq": seq, "results": results,
        })
    except Exception as e:
        logger.warning("[STREAM] could not record results for %s: %s", run_id, e)


def list_files(raw_db) -> list[dict]:
    files = [d.to_dict() for d in raw_db.collection(FILES_COLLECTION).limit(FILES_LISTED).stream()]
    files.sort(key=lambda f: f.get("started_at", 0), reverse=True)
    return files


def file_rows(raw_db, run_id: str) -> list[dict]:
    """Every result in one run, in the order it happened."""
    if not raw_db.collection(FILES_COLLECTION).document(run_id).get().exists:
        raise StreamError(404, "No such run.")
    query = raw_db.collection(FILE_CHUNKS_COLLECTION).where("run_id", "==", run_id).limit(MAX_FILE_CHUNKS)
    chunks = sorted((d.to_dict() for d in query.stream()), key=lambda c: c.get("seq", 0))
    return [row for chunk in chunks for row in chunk.get("results", [])]


def rows_to_jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def rows_to_csv(rows: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=FILE_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({**row, "at": datetime.fromtimestamp(row.get("at", 0), timezone.utc).isoformat()})
    return out.getvalue()


def _absorb(state: dict, results: list[dict]) -> None:
    def bump(bucket: str, key: Optional[str]) -> None:
        if key:
            state[bucket][key] = state[bucket].get(key, 0) + 1

    for r in results:
        state["events"] += 1
        state["pipeline_ms_total"] += r["pipeline_ms"]
        if r["outcome"] == "filed":
            state["filed"] += 1
            bump("by_priority", r.get("priority"))
            bump("by_source", r.get("triage_source"))
            if r.get("tag"):
                bump("clusters", f"{r['tag']}|{r.get('zone') or 'Unknown'}")
        elif r["outcome"] == "rejected":
            state["rejected"] += 1
            bump("by_reason", r.get("reason"))
        else:
            state["errors"] += 1
    state["recent"] = (state["recent"] + results)[-RECENT_EVENTS_KEPT:]


def tick(raw_db, pipeline: Callable, run_id: str, batch: int, rng: Optional[random.Random] = None) -> dict:
    """Generate and process one batch. The page driving the run keeps a single
    tick in flight, so this read-modify-write of the run document has no writer
    to race with."""
    state = _load(raw_db)
    if state.get("run_id") != run_id:
        raise StreamError(409, "This run is no longer current.")
    if state.get("status") != "running":
        raise StreamError(409, "This run has stopped.")

    now = time.time()
    if now - state.get("last_tick_at", 0) < MIN_TICK_GAP_SECONDS and state.get("events"):
        raise StreamError(429, "Ticking too fast.")

    remaining = state["max_events"] - state["events"]
    count = max(1, min(batch, MAX_BATCH, remaining))
    rng = rng or random.Random()
    events = [generate_event(state["scenario"], rng) for _ in range(count)]

    state["last_tick_at"] = now
    with ThreadPoolExecutor(max_workers=count) as pool:
        batches = list(pool.map(lambda e: process_event(e, pipeline, raw_db, run_id), events))
    results = [r for group in batches for r in group]

    seq = state.get("ticks", 0)
    state["ticks"] = seq + 1
    _absorb(state, results)
    state["last_tick_at"] = time.time()
    if state["events"] >= state["max_events"]:
        state["status"] = "completed"
    _control(raw_db).set(state)
    _append_to_file(raw_db, run_id, seq, results)
    _write_file_index(raw_db, state)
    return {"results": results, "state": public_state(state)}


def public_state(state: dict) -> dict:
    """What a viewer sees. Holds no real phone number: every sender here is a
    masked simulated one."""
    now = time.time()
    events = state.get("events", 0)
    clusters = sorted((state.get("clusters") or {}).items(), key=lambda kv: kv[1], reverse=True)[:TOP_CLUSTERS]
    recent = state.get("recent") or []
    return {
        "run_id": state.get("run_id"),
        "status": state.get("status", "idle"),
        "live": _is_live(state, now),
        "scenario": state.get("scenario"),
        "rate_per_min": state.get("rate_per_min"),
        "max_events": state.get("max_events"),
        "started_at": state.get("started_at"),
        "last_tick_at": state.get("last_tick_at"),
        "events": events,
        "filed": state.get("filed", 0),
        "rejected": state.get("rejected", 0),
        "errors": state.get("errors", 0),
        "avg_pipeline_ms": int(state.get("pipeline_ms_total", 0) / events) if events else None,
        "observed_per_min": sum(1 for r in recent if now - r.get("at", 0) < 60),
        "by_priority": state.get("by_priority") or {},
        "by_source": state.get("by_source") or {},
        "by_reason": state.get("by_reason") or {},
        "clusters": [{"tag": k.split("|")[0], "zone": k.split("|")[-1], "count": v} for k, v in clusters],
        "recent": recent,
    }


def read_state(raw_db) -> dict:
    return public_state(_load(raw_db))


def scenarios() -> list[dict]:
    return [{"key": k, "label": v["label"], "description": v["description"]} for k, v in SCENARIOS.items()]


def purge(raw_db) -> dict:
    """Remove every simulated report and the stream's own state. Refuses while a
    run is live, because that run would only write more."""
    if _is_live(_load(raw_db)):
        raise StreamError(409, "Stop the stream before clearing its data.")

    removed = _delete_matching(raw_db, raw_db.collection(sim.REPORTS_COLLECTION).where("simulated", "==", True))
    for name in ("sessions", "rate_limits"):
        _delete_matching(raw_db, raw_db.collection(sim.STREAM_PREFIX + name))
    for name in (FILES_COLLECTION, FILE_CHUNKS_COLLECTION):
        _delete_matching(raw_db, raw_db.collection(name))
    from google.cloud.firestore import SERVER_TIMESTAMP
    raw_db.collection(sim.CLEANUP_COLLECTION).document(sim.CLEANUP_DOC).set({"at": SERVER_TIMESTAMP})
    _control(raw_db).delete()
    logger.info("[STREAM] purged %s simulated reports", removed)
    return {"removed_reports": removed}


def _delete_matching(raw_db, query) -> int:
    removed = 0
    while True:
        docs = list(query.limit(PURGE_BATCH).stream())
        if not docs:
            return removed
        batch = raw_db.batch()
        for doc in docs:
            batch.delete(doc.reference)
        batch.commit()
        removed += len(docs)
