"""
UrbanOS Analytics Engine
========================

Single source of truth for every read-heavy surface in the product: the raw
feed, the insights dashboards, and the RAG agent.

Design principles
-----------------
1. ONE Firestore read, shared by everybody. `get_snapshot()` pulls the whole
   `messages` collection once and hands the same normalized, pre-sorted list to
   the feed endpoint, the analytics endpoint and the chat endpoint. TTL'd at
   `SNAPSHOT_TTL` seconds and explicitly invalidated when a new report lands.

2. Normalize on ingest, never at query time. Timestamps become datetimes, ward
   labels become ints, budgets become numbers, and the messy legacy category /
   sentiment vocabularies collapse into stable buckets exactly once per
   snapshot instead of once per request.

3. Pre-computed aggregates. Ward, officer, category, zone, department and daily
   series are folded during snapshot construction, so answering "worst ward" is
   a dict lookup rather than a scan.

4. Arithmetic is Python's job, prose is the model's job. The RAG agent never
   counts anything — it receives a computed FACTS block plus a computed chart
   and map filter, and only narrates them. That is what makes the answers
   reproducible.
"""

from __future__ import annotations

import re
import time
import threading
import logging
from datetime import datetime, timedelta
from typing import Any, Iterable

logger = logging.getLogger(__name__)

SNAPSHOT_TTL = 60.0  # seconds

# ---------------------------------------------------------------------------
# VOCABULARY NORMALIZATION
# ---------------------------------------------------------------------------
# The collection accumulated two generations of taxonomy: an early free-form
# triage vocabulary and the current seven-bucket civic taxonomy. Collapse the
# legacy labels so counts are not silently split across synonyms.

PRIMARY_CATEGORIES = [
    "Electricity",
    "Water & Sanitation",
    "Infrastructure",
    "Transport",
    "Public Safety",
    "Parks & Rec",
    "Waste Management",
]

_LEGACY_CATEGORY_MAP = {
    "construction development": "Infrastructure",
    "infrastructure upgrade": "Infrastructure",
    "land development": "Infrastructure",
    "maintenance & repair": "Infrastructure",
    "public utility works": "Infrastructure",
    "environmental project": "Parks & Rec",
    "other": "Other",
    "none": "Other",
    "": "Other",
}

# Query-side aliases: what a human types -> canonical category.
CATEGORY_ALIASES = {
    "electricity": "Electricity",
    "electric": "Electricity",
    "power": "Electricity",
    "power cut": "Electricity",
    "outage": "Electricity",
    "transformer": "Electricity",
    "streetlight": "Electricity",
    "street light": "Electricity",
    "light": "Electricity",
    "lesa": "Electricity",
    "uppcl": "Electricity",
    "water": "Water & Sanitation",
    "sanitation": "Water & Sanitation",
    "sewage": "Water & Sanitation",
    "drain": "Water & Sanitation",
    "drainage": "Water & Sanitation",
    "pipe": "Water & Sanitation",
    "manhole": "Water & Sanitation",
    "jal sansthan": "Water & Sanitation",
    "waterlogging": "Water & Sanitation",
    "infrastructure": "Infrastructure",
    "road": "Infrastructure",
    "roads": "Infrastructure",
    "pothole": "Infrastructure",
    "potholes": "Infrastructure",
    "bridge": "Infrastructure",
    "construction": "Infrastructure",
    "transport": "Transport",
    "traffic": "Transport",
    "bus": "Transport",
    "signal": "Transport",
    "parking": "Transport",
    "auto": "Transport",
    "metro": "Transport",
    "safety": "Public Safety",
    "public safety": "Public Safety",
    "crime": "Public Safety",
    "accident": "Public Safety",
    "stray": "Public Safety",
    "park": "Parks & Rec",
    "parks": "Parks & Rec",
    "playground": "Parks & Rec",
    "garden": "Parks & Rec",
    "swings": "Parks & Rec",
    "recreation": "Parks & Rec",
    "waste": "Waste Management",
    "garbage": "Waste Management",
    "trash": "Waste Management",
    "sweeping": "Waste Management",
    "waste management": "Waste Management",
}

# Sentiment vocabulary -> polarity bucket. "Disliked" / "dissatisfaction"
# questions are answered from these buckets.
_NEGATIVE_SENTIMENTS = {"frustrated", "concerned", "urgent", "high", "angry", "complex"}
_POSITIVE_SENTIMENTS = {"hopeful", "satisfied", "positive"}

_OPEN_STATUSES = {"open", "pending", "in review", "in progress"}
_DONE_STATUSES = {"completed", "resolved", "closed"}
_ACTIONED_STATUSES = _DONE_STATUSES | {"sanctioned"}

PRIORITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}

# Service-level targets from the escalation matrix, in days.
SLA_DAYS = {"critical": 0.25, "high": 2, "medium": 7, "low": 15}


def _to_float(v: Any) -> float | None:
    if v is None or v == "" or v == "None":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any, default: int = 0) -> int:
    f = _to_float(v)
    return int(f) if f is not None else default


def parse_ts(value: Any) -> datetime | None:
    """Parse the several timestamp shapes present in the collection."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if not isinstance(value, str):
        # Firestore Timestamp / DatetimeWithNanoseconds
        try:
            return datetime.fromtimestamp(value.timestamp())
        except Exception:
            return None
    s = value.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1]
    try:
        dt = datetime.fromisoformat(s)
        return dt.replace(tzinfo=None)
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s[:19], fmt)
            except ValueError:
                continue
    return None


def normalize_category(raw: Any) -> str:
    c = (str(raw) if raw is not None else "").strip()
    if c in PRIMARY_CATEGORIES:
        return c
    return _LEGACY_CATEGORY_MAP.get(c.lower(), c or "Other")


def sentiment_polarity(raw: Any) -> str:
    s = (str(raw) if raw is not None else "").strip().lower()
    if s in _NEGATIVE_SENTIMENTS:
        return "negative"
    if s in _POSITIVE_SENTIMENTS:
        return "positive"
    return "neutral"


def status_bucket(raw: Any) -> str:
    s = (str(raw) if raw is not None else "").strip().lower()
    if s in _DONE_STATUSES:
        return "completed"
    if s == "sanctioned":
        return "sanctioned"
    if s in _OPEN_STATUSES:
        return "open"
    return "open"


_WARD_RE = re.compile(r"(\d+)")


def ward_number(raw: Any) -> int | None:
    m = _WARD_RE.search(str(raw or ""))
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# RECORD NORMALIZATION
# ---------------------------------------------------------------------------

def normalize_record(doc_id: str, d: dict) -> dict:
    ts = parse_ts(d.get("timestamp"))
    cat = normalize_category(d.get("category"))
    status_raw = (str(d.get("status") or "Open")).strip()
    prio_raw = (str(d.get("priority") or "Medium")).strip()
    wno = ward_number(d.get("ward_no"))
    media_urls = d.get("media_urls") or []
    if isinstance(media_urls, str):
        media_urls = []
    if not media_urls and d.get("media_url") and d.get("media_url") != "None":
        media_urls = [d.get("media_url")]

    return {
        "id": d.get("id") or doc_id,
        "reference_id": d.get("reference_id") or doc_id,
        "sender": d.get("sender"),
        "timestamp": ts,
        "ts_iso": ts.isoformat() if ts else None,
        "category": cat,
        "category_raw": d.get("category"),
        "semantic_tag": (d.get("semantic_tag") or "").strip() or None,
        "summary": d.get("summary") or "",
        "body": d.get("body") or "",
        "status": status_raw,
        "status_bucket": status_bucket(status_raw),
        "priority": prio_raw,
        "priority_rank": PRIORITY_RANK.get(prio_raw.lower(), 2),
        "sentiment": d.get("sentiment"),
        "polarity": sentiment_polarity(d.get("sentiment")),
        "ward_no": d.get("ward_no"),
        "ward_num": wno,
        "ward_officer": (d.get("ward_officer") or "Unassigned").strip(),
        "responsible_body": (d.get("responsible_body") or "Unknown Dept").strip(),
        "constituency_zone": (d.get("constituency_zone") or "Unknown").strip(),
        "location": d.get("extracted_location") or "",
        "location_source": d.get("location_source"),
        "latitude": _to_float(d.get("latitude")),
        "longitude": _to_float(d.get("longitude")),
        "estimated_budget": _to_int(d.get("estimated_budget")),
        "media_urls": media_urls,
        "media_type": d.get("media_type") if d.get("media_type") != "None" else None,
        "original_language": d.get("original_language"),
        "is_urgent": bool(d.get("is_urgent")),
        "visual_evidence": d.get("visual_evidence"),
        # Lowercased haystack built once so server-side search is a substring
        # test instead of six string concatenations per record per keystroke.
        "_search": " ".join(
            str(x).lower()
            for x in (
                d.get("reference_id"),
                d.get("summary"),
                d.get("body"),
                d.get("extracted_location"),
                d.get("constituency_zone"),
                cat,
                d.get("semantic_tag"),
                d.get("ward_no"),
                d.get("ward_officer"),
                d.get("responsible_body"),
                prio_raw,
                status_raw,
            )
            if x
        ),
    }


# ---------------------------------------------------------------------------
# SNAPSHOT CACHE
# ---------------------------------------------------------------------------

_snapshot_lock = threading.Lock()
_SNAPSHOT: dict[str, Any] = {"records": None, "at": 0.0, "version": 0}


def invalidate_snapshot() -> None:
    """Called when a new report is written so the next read refetches."""
    with _snapshot_lock:
        _SNAPSHOT["records"] = None
        _SNAPSHOT["at"] = 0.0


def get_snapshot(db, force: bool = False) -> dict[str, Any]:
    """Return the shared, normalized, timestamp-DESC snapshot of all reports."""
    now = time.time()
    cached = _SNAPSHOT.get("records")
    if not force and cached is not None and (now - _SNAPSHOT["at"]) < SNAPSHOT_TTL:
        return _SNAPSHOT

    with _snapshot_lock:
        # Re-check: another thread may have rebuilt while we waited.
        cached = _SNAPSHOT.get("records")
        if not force and cached is not None and (time.time() - _SNAPSHOT["at"]) < SNAPSHOT_TTL:
            return _SNAPSHOT

        records: list[dict] = []
        if db is not None:
            try:
                for doc in db.collection("messages").stream():
                    try:
                        records.append(normalize_record(doc.id, doc.to_dict() or {}))
                    except Exception as e:  # one bad doc must not kill the feed
                        logger.warning(f"[SNAPSHOT] skipped doc {doc.id}: {e}")
            except Exception as e:
                logger.error(f"[SNAPSHOT] Firestore read failed: {e}")
                if cached is not None:
                    return _SNAPSHOT  # serve stale rather than nothing

        _EPOCH = datetime(1970, 1, 1)
        records.sort(key=lambda r: r["timestamp"] or _EPOCH, reverse=True)

        stamps = [r["timestamp"] for r in records if r["timestamp"]]
        _SNAPSHOT["records"] = records
        _SNAPSHOT["at"] = time.time()
        _SNAPSHOT["version"] = _SNAPSHOT.get("version", 0) + 1
        _SNAPSHOT["latest_ts"] = max(stamps) if stamps else None
        _SNAPSHOT["earliest_ts"] = min(stamps) if stamps else None
        logger.info(f"[SNAPSHOT] rebuilt: {len(records)} records (v{_SNAPSHOT['version']})")
        return _SNAPSHOT


# ---------------------------------------------------------------------------
# TIMELINE WINDOWS
# ---------------------------------------------------------------------------
# `anchor` decides what "now" means:
#   "now"  -> wall clock (correct for production)
#   "data" -> the newest record in the dataset, so "last 7 days" means the last
#             seven days that actually contain data (useful for demos and for
#             datasets that are refreshed in batches).

RANGE_LABELS = {
    "7d": "Last 7 Days",
    "30d": "Last 30 Days",
    "month": "This Month",
    "90d": "Last 90 Days",
    "all": "All Time",
}


def resolve_window(
    range_key: str | None,
    anchor: str = "now",
    latest_ts: datetime | None = None,
    custom_start: str | None = None,
    custom_end: str | None = None,
) -> dict[str, Any]:
    """Turn a range key into a concrete [start, end] window."""
    key = (range_key or "all").strip().lower()

    if custom_start or custom_end:
        start = parse_ts(custom_start) if custom_start else None
        end = parse_ts(custom_end) if custom_end else None
        return {
            "key": "custom",
            "label": f"{custom_start or 'start'} -> {custom_end or 'now'}",
            "start": start,
            "end": end,
            "anchor": anchor,
        }

    ref = latest_ts if (anchor == "data" and latest_ts) else datetime.now()

    if key == "all":
        return {"key": "all", "label": "All Time", "start": None, "end": None, "anchor": anchor}
    if key == "month":
        start = ref.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif key in ("7d", "7", "week"):
        key = "7d"
        start = ref - timedelta(days=7)
    elif key in ("30d", "30"):
        key = "30d"
        start = ref - timedelta(days=30)
    elif key in ("90d", "90", "quarter"):
        key = "90d"
        start = ref - timedelta(days=90)
    else:
        return {"key": "all", "label": "All Time", "start": None, "end": None, "anchor": anchor}

    label = RANGE_LABELS.get(key, key)
    if anchor == "data":
        label += " (of data)"
    return {"key": key, "label": label, "start": start, "end": ref, "anchor": anchor}


def in_window(rec: dict, window: dict) -> bool:
    start, end = window.get("start"), window.get("end")
    if start is None and end is None:
        return True
    ts = rec.get("timestamp")
    if ts is None:
        return False
    if start is not None and ts < start:
        return False
    if end is not None and ts > end:
        return False
    return True


def apply_window(records: Iterable[dict], window: dict) -> list[dict]:
    if window.get("start") is None and window.get("end") is None:
        return list(records)
    return [r for r in records if in_window(r, window)]


def apply_filters(
    records: Iterable[dict],
    q: str | None = None,
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    ward: str | None = None,
    officer: str | None = None,
    zone: str | None = None,
    dept: str | None = None,
) -> list[dict]:
    out = list(records)
    if category and category.lower() not in ("", "all"):
        canon = CATEGORY_ALIASES.get(category.lower(), category)
        out = [r for r in out if r["category"].lower() == canon.lower()]
    if priority and priority.lower() not in ("", "all"):
        out = [r for r in out if r["priority"].lower() == priority.lower()]
    if status and status.lower() not in ("", "all"):
        want = status.lower()
        if want in ("open", "completed", "sanctioned"):
            out = [r for r in out if r["status_bucket"] == want]
        else:
            out = [r for r in out if r["status"].lower() == want]
    if ward not in (None, "", "all"):
        wn = ward_number(ward)
        if wn is not None:
            out = [r for r in out if r["ward_num"] == wn]
    if officer and officer.lower() not in ("", "all"):
        out = [r for r in out if officer.lower() in r["ward_officer"].lower()]
    if zone and zone.lower() not in ("", "all"):
        out = [r for r in out if zone.lower() in r["constituency_zone"].lower()]
    if dept and dept.lower() not in ("", "all"):
        out = [r for r in out if dept.lower() in r["responsible_body"].lower()]
    if q:
        terms = [t for t in q.lower().split() if t]
        if terms:
            out = [r for r in out if all(t in r["_search"] for t in terms)]
    return out


# ---------------------------------------------------------------------------
# SCORING
# ---------------------------------------------------------------------------
# Both scores are explicit weighted sums over normalized 0..1 components, and
# both return their component contributions. That is what lets the agent answer
# "and why" with real drivers instead of an invented narrative.

WARD_SCORE_WEIGHTS = {
    "unresolved_rate": 0.28,
    "critical_rate": 0.22,
    "negative_rate": 0.18,
    "ageing_rate": 0.17,
    "volume_pressure": 0.15,
}

OFFICER_SCORE_WEIGHTS = {
    "negative_rate": 0.35,
    "unresolved_rate": 0.30,
    "critical_open_rate": 0.20,
    "ageing_rate": 0.15,
}

_DRIVER_LABELS = {
    "unresolved_rate": "unresolved backlog",
    "critical_rate": "critical-severity share",
    "negative_rate": "negative citizen sentiment",
    "ageing_rate": "ageing open complaints",
    "volume_pressure": "raw complaint volume",
    "critical_open_rate": "unresolved critical cases",
}

MIN_SAMPLE = 5           # wards: below this, flagged low-confidence
OFFICER_MIN_SAMPLE = 20  # officers cover ~20 wards each, so a handful of reports is noise


def _group_stats(rows: list[dict], now: datetime) -> dict:
    total = len(rows)
    if total == 0:
        return {}
    open_rows = [r for r in rows if r["status_bucket"] == "open"]
    completed = sum(1 for r in rows if r["status_bucket"] == "completed")
    sanctioned = sum(1 for r in rows if r["status_bucket"] == "sanctioned")
    critical = sum(1 for r in rows if r["priority"].lower() == "critical")
    high = sum(1 for r in rows if r["priority"].lower() == "high")
    negative = sum(1 for r in rows if r["polarity"] == "negative")
    positive = sum(1 for r in rows if r["polarity"] == "positive")
    critical_open = sum(1 for r in open_rows if r["priority"].lower() == "critical")

    ages = [(now - r["timestamp"]).total_seconds() / 86400.0 for r in open_rows if r["timestamp"]]
    avg_open_age = sum(ages) / len(ages) if ages else 0.0

    breached = 0
    for r in open_rows:
        if not r["timestamp"]:
            continue
        age = (now - r["timestamp"]).total_seconds() / 86400.0
        if age > SLA_DAYS.get(r["priority"].lower(), 7):
            breached += 1

    cat_counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}
    for r in rows:
        cat_counts[r["category"]] = cat_counts.get(r["category"], 0) + 1
        if r["semantic_tag"]:
            tag_counts[r["semantic_tag"]] = tag_counts.get(r["semantic_tag"], 0) + 1

    return {
        "total": total,
        "open": len(open_rows),
        "completed": completed,
        "sanctioned": sanctioned,
        "critical": critical,
        "high": high,
        "critical_open": critical_open,
        "negative": negative,
        "positive": positive,
        "avg_open_age_days": round(avg_open_age, 1),
        "sla_breached": breached,
        "sla_breach_rate": round(breached / len(open_rows), 3) if open_rows else 0.0,
        "resolution_rate": round(completed / total, 3),
        "unresolved_rate": round(len(open_rows) / total, 3),
        "critical_rate": round(critical / total, 3),
        "negative_rate": round(negative / total, 3),
        "budget": sum(r["estimated_budget"] for r in rows),
        "top_categories": sorted(cat_counts.items(), key=lambda x: -x[1])[:5],
        "top_issues": sorted(tag_counts.items(), key=lambda x: -x[1])[:5],
    }


def _score(stats: dict, weights: dict, extra: dict) -> tuple[float, list[dict]]:
    components = dict(extra)
    total = 0.0
    drivers = []
    for name, weight in weights.items():
        value = min(max(components.get(name, 0.0), 0.0), 1.0)
        contribution = weight * value
        total += contribution
        drivers.append(
            {
                "name": name,
                "label": _DRIVER_LABELS.get(name, name),
                "value": round(value, 3),
                "weight": weight,
                "contribution": round(contribution * 100, 1),
            }
        )
    drivers.sort(key=lambda d: -d["contribution"])
    return round(total * 100, 1), drivers


def ward_performance(records: list[dict], now: datetime | None = None) -> list[dict]:
    """Rank wards by a distress score. Higher score = worse performing."""
    now = now or datetime.now()
    buckets: dict[Any, list[dict]] = {}
    for r in records:
        buckets.setdefault(r["ward_num"] if r["ward_num"] is not None else r["ward_no"], []).append(r)

    max_total = max((len(v) for v in buckets.values()), default=1) or 1
    max_age = max(
        (
            max(
                ((now - r["timestamp"]).total_seconds() / 86400.0 for r in v if r["timestamp"] and r["status_bucket"] == "open"),
                default=0.0,
            )
            for v in buckets.values()
        ),
        default=1.0,
    ) or 1.0

    out = []
    for key, rows in buckets.items():
        stats = _group_stats(rows, now)
        extra = {
            "unresolved_rate": stats["unresolved_rate"],
            "critical_rate": stats["critical_rate"],
            "negative_rate": stats["negative_rate"],
            "ageing_rate": min(stats["avg_open_age_days"] / max_age, 1.0) if max_age else 0.0,
            "volume_pressure": stats["total"] / max_total,
        }
        score, drivers = _score(stats, WARD_SCORE_WEIGHTS, extra)
        label = rows[0]["ward_no"] or (f"Ward {key}" if key is not None else "Unknown Ward")
        officers = {}
        for r in rows:
            officers[r["ward_officer"]] = officers.get(r["ward_officer"], 0) + 1
        out.append(
            {
                "ward": label,
                "ward_num": key if isinstance(key, int) else None,
                "officer": max(officers.items(), key=lambda x: x[1])[0] if officers else "Unassigned",
                "distress_score": score,
                "drivers": drivers,
                "low_confidence": stats["total"] < MIN_SAMPLE,
                **stats,
            }
        )

    out.sort(key=lambda w: (-w["distress_score"], -w["total"]))
    return out


def officer_scorecard(records: list[dict], now: datetime | None = None, min_sample: int = OFFICER_MIN_SAMPLE) -> list[dict]:
    """Rank ward officers by a citizen-dissatisfaction score."""
    now = now or datetime.now()
    buckets: dict[str, list[dict]] = {}
    for r in records:
        buckets.setdefault(r["ward_officer"] or "Unassigned", []).append(r)

    max_age = max(
        (
            max(
                ((now - r["timestamp"]).total_seconds() / 86400.0 for r in v if r["timestamp"] and r["status_bucket"] == "open"),
                default=0.0,
            )
            for v in buckets.values()
        ),
        default=1.0,
    ) or 1.0

    out = []
    for officer, rows in buckets.items():
        stats = _group_stats(rows, now)
        extra = {
            "negative_rate": stats["negative_rate"],
            "unresolved_rate": stats["unresolved_rate"],
            "critical_open_rate": stats["critical_open"] / stats["total"] if stats["total"] else 0.0,
            "ageing_rate": min(stats["avg_open_age_days"] / max_age, 1.0) if max_age else 0.0,
        }
        score, drivers = _score(stats, OFFICER_SCORE_WEIGHTS, extra)
        wards = sorted({r["ward_no"] for r in rows if r["ward_no"]})
        out.append(
            {
                "officer": officer,
                "dissatisfaction_score": score,
                "drivers": drivers,
                "wards_covered": len(wards),
                "ward_list": wards[:12],
                "low_confidence": stats["total"] < min_sample,
                **stats,
            }
        )

    out.sort(key=lambda o: (-o["dissatisfaction_score"], -o["total"]))
    return out


def breakdown(records: list[dict], field: str, limit: int = 20) -> list[dict]:
    counts: dict[str, dict] = {}
    for r in records:
        key = r.get(field) or "Unknown"
        if isinstance(key, list):
            continue
        b = counts.setdefault(str(key), {"key": str(key), "total": 0, "open": 0, "completed": 0, "critical": 0, "negative": 0})
        b["total"] += 1
        if r["status_bucket"] == "open":
            b["open"] += 1
        elif r["status_bucket"] == "completed":
            b["completed"] += 1
        if r["priority"].lower() == "critical":
            b["critical"] += 1
        if r["polarity"] == "negative":
            b["negative"] += 1
    rows = sorted(counts.values(), key=lambda x: -x["total"])[:limit]
    for row in rows:
        row["resolution_rate"] = round(row["completed"] / row["total"], 3) if row["total"] else 0.0
    return rows


def trend_series(records: list[dict], bucket: str = "day", limit: int = 60) -> list[dict]:
    """Time series of report volume. bucket = day | week | month."""
    counts: dict[str, int] = {}
    for r in records:
        ts = r["timestamp"]
        if not ts:
            continue
        if bucket == "month":
            key = ts.strftime("%Y-%m")
        elif bucket == "week":
            key = (ts - timedelta(days=ts.weekday())).strftime("%Y-%m-%d")
        else:
            key = ts.strftime("%Y-%m-%d")
        counts[key] = counts.get(key, 0) + 1
    rows = [{"period": k, "count": v} for k, v in sorted(counts.items())]
    return rows[-limit:]


def headline_stats(records: list[dict], now: datetime | None = None) -> dict:
    now = now or datetime.now()
    stats = _group_stats(records, now) if records else {
        "total": 0, "open": 0, "completed": 0, "sanctioned": 0, "critical": 0,
        "high": 0, "critical_open": 0, "negative": 0, "positive": 0,
        "avg_open_age_days": 0.0, "sla_breached": 0, "sla_breach_rate": 0.0,
        "resolution_rate": 0.0, "unresolved_rate": 0.0, "critical_rate": 0.0,
        "negative_rate": 0.0, "budget": 0, "top_categories": [], "top_issues": [],
    }
    stats["wards_active"] = len({r["ward_num"] for r in records if r["ward_num"] is not None})
    stats["geo_located"] = sum(1 for r in records if r["latitude"] and r["longitude"])
    return stats


# ---------------------------------------------------------------------------
# QUERY ROUTER
# ---------------------------------------------------------------------------
# A cheap deterministic pass over the question. When it recognises an intent we
# compute the exact answer and hand the model a FACTS block plus a ready-made
# chart and map filter. Unrecognised questions still get the full briefing, so
# the model degrades to "well-briefed generalist" rather than to guessing.

_TIMEFRAME_PATTERNS = [
    (r"\blast\s+7\s*days?\b|\bpast\s+7\s*days?\b|\bthis\s+week\b|\blast\s+week\b|\bweekly\b", "7d"),
    (r"\blast\s+30\s*days?\b|\bpast\s+30\s*days?\b|\blast\s+month\b", "30d"),
    (r"\bthis\s+month\b|\bcurrent\s+month\b|\bmonth\s+to\s+date\b|\bmtd\b", "month"),
    (r"\blast\s+90\s*days?\b|\bthis\s+quarter\b|\bquarterly\b", "90d"),
    (r"\ball\s*[- ]?time\b|\boverall\b|\bever\b|\bhistorical\b|\bhistory\b|\bin\s+total\b", "all"),
]

_INTENT_PATTERNS = [
    ("officer_ranking", r"\bofficer\b|\bofficial\b|\bin[- ]charge\b|\bdisliked\b|\bunpopular\b|\bcomplained\s+about\b"),
    ("worst_wards", r"\bworst\b|\bpoorest\b|\bworst[- ]performing\b|\bunderperform|\bfailing\b|\bproblematic\b|\bbad(?:ly)?\s+perform"),
    ("best_wards", r"\bbest\b|\btop[- ]performing\b|\bstrongest\b|\bmost\s+improved\b|\bbest[- ]performing\b"),
    ("trend", r"\btrend\b|\bover\s+time\b|\bdaily\b|\bmonthly\b|\bgrowth\b|\bspike\b|\bpattern\s+over\b|\btime\s*series\b"),
    ("sla", r"\bsla\b|\bbreach|\boverdue\b|\bdelayed\b|\bageing\b|\baging\b|\bpending\s+too\s+long\b"),
    ("dept_breakdown", r"\bdepartment\b|\bdept\b|\bnagar nigam\b|\bjal sansthan\b|\bpwd\b|\blda\b|\blesa\b|\bresponsible body\b"),
    ("zone_breakdown", r"\bzone\b|\bconstituency\b|\barea\s+wise\b|\bregion\b|\bneighbou?rhood\b"),
    ("top_issues", r"\btop\s+issues?\b|\bcommon\s+(?:issues?|problems?|complaints?)\b|\bmost\s+reported\b|\bfrequent\b"),
    ("category_breakdown", r"\bcategor|\bbreakdown\b|\btypes?\s+of\b|\bdistribution\b|\bsplit\b"),
    ("ward_ranking", r"\bward\b"),
]

_CHART_HINT = r"\bchart\b|\bgraph\b|\bplot\b|\bvisuali[sz]e\b|\bpie\b|\bbar\b|\btrend\b|\bshow\s+me\s+a\b"
_WARD_MENTION = re.compile(r"\bward\s*(?:no\.?|number|#)?\s*(\d{1,3})\b", re.I)


def detect_timeframe(query: str) -> str | None:
    q = query.lower()
    for pattern, key in _TIMEFRAME_PATTERNS:
        if re.search(pattern, q):
            return key
    return None


def detect_category(query: str) -> str | None:
    q = query.lower()
    # Longest alias first so "street light" beats "light" and
    # "water & sanitation" beats "water".
    for alias in sorted(CATEGORY_ALIASES, key=len, reverse=True):
        if re.search(r"\b" + re.escape(alias) + r"\b", q):
            return CATEGORY_ALIASES[alias]
    return None


def detect_officer(query: str, records: list[dict]) -> str | None:
    q = query.lower()
    names = {r["ward_officer"] for r in records if r["ward_officer"]}
    for name in names:
        if not name or name == "Unassigned":
            continue
        if name.lower() in q:
            return name
        first = name.split()[0].lower()
        if len(first) > 3 and re.search(r"\b" + re.escape(first) + r"\b", q):
            return name
    return None


def detect_zone(query: str, records: list[dict]) -> str | None:
    q = query.lower()
    zones = {r["constituency_zone"] for r in records if r["constituency_zone"]}
    best = None
    for z in zones:
        if z and z.lower() != "unknown" and z.lower() in q:
            if best is None or len(z) > len(best):
                best = z
    return best


def detect_intent(query: str) -> str:
    q = query.lower()
    for intent, pattern in _INTENT_PATTERNS:
        if re.search(pattern, q):
            return intent
    return "general"


def route_query(query: str, records: list[dict]) -> dict:
    """Classify a natural-language question into a computable plan."""
    ward_match = _WARD_MENTION.search(query)
    return {
        "intent": detect_intent(query),
        "timeframe": detect_timeframe(query),
        "category": detect_category(query),
        "officer": detect_officer(query, records),
        "zone": detect_zone(query, records),
        "ward": int(ward_match.group(1)) if ward_match else None,
        "wants_chart": bool(re.search(_CHART_HINT, query.lower())),
        "wants_best": bool(re.search(r"\bbest\b|\btop[- ]performing\b|\bleast\b|\bfewest\b", query.lower())),
    }


def _fmt_money(n: int) -> str:
    if n >= 10_000_000:
        return f"INR {n/10_000_000:.2f} Cr"
    if n >= 100_000:
        return f"INR {n/100_000:.2f} L"
    return f"INR {n:,}"


def compute_answer(plan: dict, records: list[dict], window: dict, now: datetime | None = None) -> dict:
    """Compute the exact answer, chart and map filter for a routed question.

    Returns {facts, chart, map_ids, headline} — `facts` is plain text the model
    is instructed to narrate rather than recompute.
    """
    now = now or datetime.now()
    intent = plan["intent"]
    scope = records

    # Narrow to the entity the question is about before ranking.
    if plan.get("category"):
        scope = [r for r in scope if r["category"] == plan["category"]]
    if plan.get("zone"):
        scope = [r for r in scope if plan["zone"].lower() in r["constituency_zone"].lower()]
    if plan.get("ward") is not None:
        scope = [r for r in scope if r["ward_num"] == plan["ward"]]
    if plan.get("officer"):
        scope = [r for r in scope if r["ward_officer"] == plan["officer"]]

    lines: list[str] = []
    chart = None
    map_ids = [r["reference_id"] for r in scope]
    headline = ""

    qualifier = []
    if plan.get("category"):
        qualifier.append(plan["category"])
    if plan.get("zone"):
        qualifier.append(f"in {plan['zone']}")
    if plan.get("ward") is not None:
        qualifier.append(f"Ward {plan['ward']}")
    qual = " ".join(qualifier)
    scope_label = f"{qual} complaints" if qual else "complaints"

    lines.append(f"WINDOW: {window['label']} ({_window_text(window)})")
    lines.append(f"SCOPE: {len(scope)} {scope_label} out of {len(records)} in window.")

    if not scope:
        lines.append("RESULT: No records match this scope. Say so plainly; do not estimate.")
        return {"facts": "\n".join(lines), "chart": None, "map_ids": [], "headline": "No matching records"}

    if intent == "officer_ranking":
        cards = officer_scorecard(scope, now)
        ranked = sorted(cards, key=lambda c: c["dissatisfaction_score"], reverse=not plan["wants_best"])
        # A single complaint can score 100%; never let that outrank a real caseload.
        ranked = [c for c in ranked if not c["low_confidence"]] or ranked
        top = ranked[0]
        verb = "most positively rated" if plan["wants_best"] else "most disliked"
        headline = f"{top['officer']} is the {verb} ward officer ({top['dissatisfaction_score']}/100)"
        lines.append(f"RANKING — ward officers by citizen-dissatisfaction score (0=best, 100=worst):")
        for c in ranked:
            flag = " [LOW SAMPLE]" if c["low_confidence"] else ""
            lines.append(
                f"  {c['officer']}: score {c['dissatisfaction_score']}{flag} | "
                f"{c['total']} complaints across {c['wards_covered']} wards | "
                f"negative sentiment {c['negative']}/{c['total']} ({c['negative_rate']*100:.0f}%) | "
                f"unresolved {c['open']} ({c['unresolved_rate']*100:.0f}%) | "
                f"critical still open {c['critical_open']} | "
                f"avg age of open cases {c['avg_open_age_days']}d | "
                f"SLA breaches {c['sla_breached']}"
            )
        lines.append(f"WHY {top['officer']} RANKS {'BEST' if plan['wants_best'] else 'WORST'} — score drivers (points of 100):")
        for d in top["drivers"]:
            lines.append(f"  {d['label']}: {d['contribution']} pts (component value {d['value']})")
        lines.append(f"  Top issue types in their wards: {', '.join(f'{k} ({v})' for k, v in top['top_issues']) or 'n/a'}")
        lines.append(
            "NOTE: 'dissatisfaction' is a computed composite of citizen sentiment, unresolved backlog, "
            "open critical cases and case ageing. It is a workload/outcome signal, not a personal judgement — say so."
        )
        chart = {
            "type": "bar",
            "title": f"Ward officer dissatisfaction score — {window['label']}",
            "labels": [c["officer"] for c in ranked],
            "values": [c["dissatisfaction_score"] for c in ranked],
            "unit": "score",
        }
        map_ids = [r["reference_id"] for r in scope if r["ward_officer"] == top["officer"]]

    elif intent in ("worst_wards", "best_wards"):
        cards = ward_performance(scope, now)
        want_best = plan["wants_best"] or intent == "best_wards"
        ranked = sorted(cards, key=lambda c: c["distress_score"], reverse=not want_best)
        ranked = [c for c in ranked if not c["low_confidence"]] or ranked
        top = ranked[0]
        shown = ranked[:10]
        headline = (
            f"{top['ward']} is the {'best' if want_best else 'worst'} performing ward "
            f"(distress score {top['distress_score']}/100)"
        )
        lines.append(
            f"RANKING — wards by distress score (0=healthy, 100=worst), {scope_label}, "
            f"{'ascending (best first)' if want_best else 'descending (worst first)'}:"
        )
        for c in shown:
            lines.append(
                f"  {c['ward']} (officer {c['officer']}): score {c['distress_score']} | "
                f"{c['total']} complaints | open {c['open']} ({c['unresolved_rate']*100:.0f}%) | "
                f"completed {c['completed']} (resolution {c['resolution_rate']*100:.0f}%) | "
                f"critical {c['critical']} | negative sentiment {c['negative']} | "
                f"avg open age {c['avg_open_age_days']}d | SLA breaches {c['sla_breached']} | "
                f"top issues: {', '.join(f'{k} ({v})' for k, v in c['top_issues'][:3]) or 'n/a'}"
            )
        lines.append(f"WHY {top['ward']} RANKS {'BEST' if want_best else 'WORST'} — score drivers (points of 100):")
        for d in top["drivers"]:
            lines.append(f"  {d['label']}: {d['contribution']} pts (component value {d['value']})")
        lines.append(
            f"  Category mix in {top['ward']}: "
            + (", ".join(f"{k} ({v})" for k, v in top["top_categories"]) or "n/a")
        )
        lines.append(
            f"  Recurring issue types: "
            + (", ".join(f"{k} ({v})" for k, v in top["top_issues"]) or "n/a")
        )
        lines.append(f"  Budget exposure in {top['ward']}: {_fmt_money(top['budget'])}")
        chart = {
            "type": "bar",
            "title": f"Ward distress score{(' — ' + qual) if qual else ''} — {window['label']}",
            "labels": [c["ward"] for c in shown],
            "values": [c["distress_score"] for c in shown],
            "unit": "score",
        }
        map_ids = [r["reference_id"] for r in scope if r["ward_no"] in {c["ward"] for c in shown[:5]}]

    elif intent == "trend":
        bucket = "month" if window["key"] in ("all", "90d") else "day"
        series = trend_series(scope, bucket)
        headline = f"{len(scope)} {scope_label} across {len(series)} {bucket}s"
        lines.append(f"TREND — {scope_label} per {bucket}:")
        for row in series:
            lines.append(f"  {row['period']}: {row['count']}")
        if len(series) >= 2:
            first_half = sum(r["count"] for r in series[: len(series) // 2])
            second_half = sum(r["count"] for r in series[len(series) // 2 :])
            direction = "rising" if second_half > first_half else "falling" if second_half < first_half else "flat"
            lines.append(f"  Direction: {direction} ({first_half} -> {second_half} across the two halves of the window).")
        peak = max(series, key=lambda r: r["count"]) if series else None
        if peak:
            lines.append(f"  Peak {bucket}: {peak['period']} with {peak['count']} reports.")
        chart = {
            "type": "line",
            "title": f"{qual or 'Complaint'} volume per {bucket} — {window['label']}",
            "labels": [r["period"] for r in series],
            "values": [r["count"] for r in series],
            "unit": "reports",
        }

    elif intent == "sla":
        cards = ward_performance(scope, now)
        ranked = sorted(cards, key=lambda c: -c["sla_breached"])[:10]
        total_breach = sum(c["sla_breached"] for c in cards)
        headline = f"{total_breach} open {scope_label} are past their SLA target"
        lines.append(
            "SLA TARGETS: Critical 6h, High 48h, Medium 7d, Low 15d. "
            f"Total open past target in window: {total_breach}."
        )
        for c in ranked:
            lines.append(
                f"  {c['ward']} (officer {c['officer']}): {c['sla_breached']} breaches of {c['open']} open "
                f"({c['sla_breach_rate']*100:.0f}%) | avg open age {c['avg_open_age_days']}d"
            )
        chart = {
            "type": "bar",
            "title": f"SLA breaches by ward — {window['label']}",
            "labels": [c["ward"] for c in ranked],
            "values": [c["sla_breached"] for c in ranked],
            "unit": "breaches",
        }
        map_ids = [
            r["reference_id"]
            for r in scope
            if r["status_bucket"] == "open"
            and r["timestamp"]
            and (now - r["timestamp"]).total_seconds() / 86400.0 > SLA_DAYS.get(r["priority"].lower(), 7)
        ]

    elif intent == "dept_breakdown":
        rows = breakdown(scope, "responsible_body")
        headline = f"{rows[0]['key']} owns the most {scope_label} ({rows[0]['total']})" if rows else ""
        lines.append(f"BY DEPARTMENT — {scope_label}:")
        for r in rows:
            lines.append(
                f"  {r['key']}: {r['total']} total | open {r['open']} | completed {r['completed']} "
                f"(resolution {r['resolution_rate']*100:.0f}%) | critical {r['critical']} | negative {r['negative']}"
            )
        chart = {
            "type": "doughnut",
            "title": f"{qual or 'Complaints'} by department — {window['label']}",
            "labels": [r["key"] for r in rows],
            "values": [r["total"] for r in rows],
            "unit": "reports",
        }

    elif intent == "zone_breakdown":
        rows = breakdown(scope, "constituency_zone")
        headline = f"{rows[0]['key']} leads with {rows[0]['total']} {scope_label}" if rows else ""
        lines.append(f"BY ZONE / LOCALITY — {scope_label}:")
        for r in rows:
            lines.append(
                f"  {r['key']}: {r['total']} total | open {r['open']} | completed {r['completed']} "
                f"(resolution {r['resolution_rate']*100:.0f}%) | critical {r['critical']}"
            )
        chart = {
            "type": "bar",
            "title": f"{qual or 'Complaints'} by locality — {window['label']}",
            "labels": [r["key"] for r in rows[:12]],
            "values": [r["total"] for r in rows[:12]],
            "unit": "reports",
        }

    elif intent == "top_issues":
        counts: dict[str, int] = {}
        for r in scope:
            if r["semantic_tag"]:
                counts[r["semantic_tag"]] = counts.get(r["semantic_tag"], 0) + 1
        rows = sorted(counts.items(), key=lambda x: -x[1])[:15]
        headline = f"'{rows[0][0]}' is the most reported issue ({rows[0][1]})" if rows else ""
        lines.append(f"MOST REPORTED ISSUE TYPES — {scope_label}:")
        for k, v in rows:
            lines.append(f"  {k}: {v}")
        chart = {
            "type": "bar",
            "title": f"Top issue types{(' — ' + qual) if qual else ''} — {window['label']}",
            "labels": [k for k, _ in rows[:12]],
            "values": [v for _, v in rows[:12]],
            "unit": "reports",
        }

    else:  # category_breakdown and general
        # When the question named a category, the useful answer is almost
        # always "which wards", not "which categories".
        # "which ward has the MOST X" is a volume question, not a performance one --
        # rank by count here and leave the distress score to worst/best queries.
        if intent == "ward_ranking" or plan.get("category") or plan.get("zone") or plan.get("ward") is not None:
            rows = breakdown(scope, "ward_no")
            headline = f"{rows[0]['key']} has the most {scope_label} ({rows[0]['total']})" if rows else ""
            lines.append(f"BY WARD — {scope_label}, highest first:")
            for r in rows[:15]:
                lines.append(
                    f"  {r['key']}: {r['total']} total | open {r['open']} | completed {r['completed']} "
                    f"(resolution {r['resolution_rate']*100:.0f}%) | critical {r['critical']} | negative {r['negative']}"
                )
            officers = breakdown(scope, "ward_officer")
            lines.append("BY OFFICER for this scope: " + "; ".join(f"{o['key']}: {o['total']}" for o in officers))
            chart = {
                "type": "bar",
                "title": f"{qual or 'Complaints'} by ward — {window['label']}",
                "labels": [r["key"] for r in rows[:12]],
                "values": [r["total"] for r in rows[:12]],
                "unit": "reports",
            }
        else:
            rows = breakdown(scope, "category")
            headline = f"{rows[0]['key']} is the largest category ({rows[0]['total']})" if rows else ""
            lines.append(f"BY CATEGORY — {scope_label}:")
            for r in rows:
                lines.append(
                    f"  {r['key']}: {r['total']} total | open {r['open']} | completed {r['completed']} "
                    f"(resolution {r['resolution_rate']*100:.0f}%) | critical {r['critical']} | negative {r['negative']}"
                )
            chart = {
                "type": "doughnut",
                "title": f"Complaints by category — {window['label']}",
                "labels": [r["key"] for r in rows],
                "values": [r["total"] for r in rows],
                "unit": "reports",
            }

    return {"facts": "\n".join(lines), "chart": chart, "map_ids": map_ids[:800], "headline": headline}


def _window_text(window: dict) -> str:
    s, e = window.get("start"), window.get("end")
    if s is None and e is None:
        return "no date restriction"
    fmt = "%Y-%m-%d"
    return f"{s.strftime(fmt) if s else 'beginning'} to {e.strftime(fmt) if e else 'now'}"


def build_briefing(records: list[dict], window: dict, now: datetime | None = None, max_wards: int = 12) -> str:
    """Compact always-on analytics briefing for the model's system prompt.

    Deliberately aggregate-only. Individual records are supplied separately and
    capped, so the prompt stays a fixed, predictable size no matter how large
    the collection grows.
    """
    now = now or datetime.now()
    h = headline_stats(records, now)
    parts = [
        f"ACTIVE TIMELINE: {window['label']} ({_window_text(window)}); anchor={window.get('anchor', 'now')}",
        f"Reports in window: {h['total']} | wards active: {h['wards_active']} | geo-located: {h['geo_located']}",
        f"Status: open {h['open']}, completed {h['completed']}, sanctioned {h['sanctioned']} "
        f"(resolution rate {h['resolution_rate']*100:.1f}%)",
        f"Severity: critical {h['critical']}, high {h['high']}, critical-still-open {h['critical_open']}",
        f"Sentiment: negative {h['negative']} ({h['negative_rate']*100:.1f}%), positive {h['positive']}",
        f"Ageing: avg open case {h['avg_open_age_days']} days | SLA breaches {h['sla_breached']} "
        f"({h['sla_breach_rate']*100:.0f}% of open)",
        f"Budget exposure in window: {_fmt_money(h['budget'])}",
    ]
    if not records:
        return "\n".join(parts) + "\nNo records fall inside this window."

    parts.append("\nCATEGORY TABLE (category | total | open | completed | critical | negative):")
    for r in breakdown(records, "category"):
        parts.append(f"  {r['key']} | {r['total']} | {r['open']} | {r['completed']} | {r['critical']} | {r['negative']}")

    parts.append("\nDEPARTMENT TABLE (dept | total | open | completed | resolution%):")
    for r in breakdown(records, "responsible_body"):
        parts.append(f"  {r['key']} | {r['total']} | {r['open']} | {r['completed']} | {r['resolution_rate']*100:.0f}%")

    parts.append("\nZONE TABLE (locality | total | open | completed | critical):")
    for r in breakdown(records, "constituency_zone", limit=14):
        parts.append(f"  {r['key']} | {r['total']} | {r['open']} | {r['completed']} | {r['critical']}")

    wards = ward_performance(records, now)
    parts.append(
        f"\nWARD DISTRESS RANKING (worst first; score 0-100 = weighted mix of unresolved backlog, "
        f"critical share, negative sentiment, case ageing, volume). Showing top {max_wards} of {len(wards)}:"
    )
    parts.append("  ward | officer | score | total | open | completed | critical | negative | avgOpenAgeDays | slaBreaches")
    for c in wards[:max_wards]:
        parts.append(
            f"  {c['ward']} | {c['officer']} | {c['distress_score']} | {c['total']} | {c['open']} | "
            f"{c['completed']} | {c['critical']} | {c['negative']} | {c['avg_open_age_days']} | {c['sla_breached']}"
        )
    parts.append("  BEST-PERFORMING WARDS (lowest distress): " + ", ".join(
        f"{c['ward']} ({c['distress_score']})" for c in reversed(wards[-6:])
    ))

    officers = officer_scorecard(records, now)
    parts.append("\nWARD OFFICER SCORECARD (dissatisfaction score 0-100, worst first):")
    parts.append("  officer | score | wards | total | open | unresolved% | negativeSentiment | negative% | criticalOpen | avgOpenAgeDays")
    for c in officers:
        parts.append(
            f"  {c['officer']} | {c['dissatisfaction_score']} | {c['wards_covered']} | {c['total']} | {c['open']} | "
            f"{c['unresolved_rate']*100:.0f}% | {c['negative']} | {c['negative_rate']*100:.0f}% | "
            f"{c['critical_open']} | {c['avg_open_age_days']}"
        )

    tags: dict[str, int] = {}
    for r in records:
        if r["semantic_tag"]:
            tags[r["semantic_tag"]] = tags.get(r["semantic_tag"], 0) + 1
    parts.append("\nTOP ISSUE TYPES: " + ", ".join(
        f"{k} ({v})" for k, v in sorted(tags.items(), key=lambda x: -x[1])[:20]
    ))

    bucket = "month" if window["key"] in ("all", "90d") else "day"
    series = trend_series(records, bucket, limit=40)
    parts.append(f"\nVOLUME PER {bucket.upper()}: " + ", ".join(f"{r['period']}={r['count']}" for r in series))

    return "\n".join(parts)

# ---------------------------------------------------------------------------
# SURVEY AUDIENCE TARGETING
# ---------------------------------------------------------------------------
# A survey audience is defined by the reports its recipients filed, so the same
# snapshot and the same filter vocabulary that drive the feed and the dashboards
# also decide who gets asked. Targeting a ward, a locality or a problem type is
# therefore a filter over records followed by a unique-sender fold - no separate
# recipient database to keep in sync.

def _norm_list(values) -> list[str]:
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    return [str(v).strip() for v in values if str(v).strip() and str(v).strip().lower() != "all"]


def filter_by_audience(records: list[dict], audience: dict) -> list[dict]:
    """Multi-select filter. Within a field the values are OR'd; across fields
    they are AND'd, which is how an operator reads "Ward 12 or 14, water issues"."""
    a = audience or {}
    out = list(records)

    wards = {ward_number(w) for w in _norm_list(a.get("wards"))} - {None}
    if wards:
        out = [r for r in out if r["ward_num"] in wards]

    zones = [z.lower() for z in _norm_list(a.get("zones"))]
    if zones:
        out = [r for r in out if any(z in r["constituency_zone"].lower() for z in zones)]

    cats = {CATEGORY_ALIASES.get(c.lower(), c).lower() for c in _norm_list(a.get("categories"))}
    if cats:
        out = [r for r in out if r["category"].lower() in cats]

    tags = [t.lower() for t in _norm_list(a.get("semantic_tags"))]
    if tags:
        out = [r for r in out if r["semantic_tag"] and any(t in r["semantic_tag"].lower() for t in tags)]

    prios = {p.lower() for p in _norm_list(a.get("priorities"))}
    if prios:
        out = [r for r in out if r["priority"].lower() in prios]

    statuses = {st.lower() for st in _norm_list(a.get("statuses"))}
    if statuses:
        out = [r for r in out if r["status_bucket"] in statuses or r["status"].lower() in statuses]

    pols = {x.lower() for x in _norm_list(a.get("sentiments"))}
    if pols:
        out = [r for r in out if r["polarity"] in pols or (r["sentiment"] or "").lower() in pols]

    officers = [o.lower() for o in _norm_list(a.get("officers"))]
    if officers:
        out = [r for r in out if any(o in r["ward_officer"].lower() for o in officers)]

    depts = [d.lower() for d in _norm_list(a.get("departments"))]
    if depts:
        out = [r for r in out if any(d in r["responsible_body"].lower() for d in depts)]

    return out


def resolve_audience(records: list[dict], audience: dict, window: dict | None = None) -> dict:
    """Turn an audience definition into a concrete recipient list plus a preview
    of who is being reached, so an operator can sanity-check before broadcasting."""
    scoped = apply_window(records, window) if window else list(records)
    matched = filter_by_audience(scoped, audience)

    min_reports = int((audience or {}).get("min_reports") or 1)
    by_sender: dict[str, list[dict]] = {}
    for r in matched:
        if r["sender"]:
            by_sender.setdefault(r["sender"], []).append(r)

    recipients = []
    for sender, rows in by_sender.items():
        if len(rows) < min_reports:
            continue
        neg = sum(1 for r in rows if r["polarity"] == "negative")
        open_n = sum(1 for r in rows if r["status_bucket"] == "open")
        recipients.append({
            "sender": sender,
            "reports": len(rows),
            "open_reports": open_n,
            "negative_reports": neg,
            "wards": sorted({r["ward_no"] for r in rows if r["ward_no"]}),
            "categories": sorted({r["category"] for r in rows}),
            "last_report": max((r["ts_iso"] for r in rows if r["ts_iso"]), default=None),
        })
    recipients.sort(key=lambda x: -x["reports"])

    return {
        "recipients": recipients,
        "recipient_count": len(recipients),
        "matched_reports": len(matched),
        "scoped_reports": len(scoped),
        "breakdown": {
            "wards": breakdown(matched, "ward_no", limit=15),
            "zones": breakdown(matched, "constituency_zone", limit=12),
            "categories": breakdown(matched, "category"),
            "officers": breakdown(matched, "ward_officer"),
            "departments": breakdown(matched, "responsible_body"),
            "sentiments": breakdown(matched, "sentiment", limit=10),
        },
    }


def describe_audience(audience: dict) -> str:
    """Human-readable label for an audience, used in the dashboard and logs."""
    a = audience or {}
    bits = []
    for key, prefix in (
        ("wards", "Ward"), ("zones", ""), ("categories", ""), ("semantic_tags", "issue"),
        ("priorities", "priority"), ("statuses", "status"), ("sentiments", "sentiment"),
        ("officers", "officer"), ("departments", "dept"),
    ):
        vals = _norm_list(a.get(key))
        if vals:
            label = ", ".join(vals[:4]) + (f" +{len(vals) - 4}" if len(vals) > 4 else "")
            bits.append(f"{prefix} {label}".strip())
    if int(a.get("min_reports") or 1) > 1:
        bits.append(f"{a['min_reports']}+ reports filed")
    return " | ".join(bits) if bits else "All citizens who have reported"


# ---------------------------------------------------------------------------
# SURVEY SENTIMENT REPORTING
# ---------------------------------------------------------------------------

def survey_report(
    survey: dict,
    responses: list[dict],
    records: list[dict],
    now: datetime | None = None,
) -> dict:
    """Cross-tabulate survey answers against the grievance history of the people
    who answered.

    A bare option tally says what people chose; joining each respondent back to
    their own reports says which wards, which problem types and which prior
    experience produced that choice, which is the part an administrator can act
    on.
    """
    now = now or datetime.now()
    options = [o.strip() for o in (survey.get("options") or "").split(",") if o.strip()]
    sent_to = survey.get("sent_to") or []

    by_sender: dict[str, list[dict]] = {}
    for r in records:
        if r["sender"]:
            by_sender.setdefault(r["sender"], []).append(r)

    option_counts = {o: 0 for o in options}
    cross_ward: dict[str, dict[str, int]] = {}
    cross_zone: dict[str, dict[str, int]] = {}
    cross_cat: dict[str, dict[str, int]] = {}
    respondent_rows: list[dict] = []
    sentiment_by_option: dict[str, dict[str, int]] = {o: {"negative": 0, "neutral": 0, "positive": 0} for o in options}
    resolution_by_option: dict[str, dict[str, int]] = {o: {"open": 0, "completed": 0, "total": 0} for o in options}

    for resp in responses:
        opt = resp.get("selected_option")
        sender = resp.get("sender")
        if opt not in option_counts:
            option_counts[opt] = 0
            sentiment_by_option.setdefault(opt, {"negative": 0, "neutral": 0, "positive": 0})
            resolution_by_option.setdefault(opt, {"open": 0, "completed": 0, "total": 0})
        option_counts[opt] += 1

        rows = by_sender.get(sender, [])
        respondent_rows.extend(rows)
        for r in rows:
            w, z, c = r["ward_no"] or "Unknown", r["constituency_zone"] or "Unknown", r["category"]
            cross_ward.setdefault(w, {}).setdefault(opt, 0)
            cross_ward[w][opt] += 1
            cross_zone.setdefault(z, {}).setdefault(opt, 0)
            cross_zone[z][opt] += 1
            cross_cat.setdefault(c, {}).setdefault(opt, 0)
            cross_cat[c][opt] += 1
            sentiment_by_option[opt][r["polarity"]] += 1
            resolution_by_option[opt]["total"] += 1
            if r["status_bucket"] == "open":
                resolution_by_option[opt]["open"] += 1
            elif r["status_bucket"] == "completed":
                resolution_by_option[opt]["completed"] += 1

    total_responses = len(responses)
    sent_count = len(sent_to)
    response_rate = round(total_responses / sent_count, 3) if sent_count else None

    # Does prior experience predict the answer? Comparing each option's cohort
    # resolution rate against the others is the actionable signal in the survey.
    option_insight = []
    for opt in option_counts:
        res = resolution_by_option.get(opt, {"open": 0, "completed": 0, "total": 0})
        sen = sentiment_by_option.get(opt, {"negative": 0, "neutral": 0, "positive": 0})
        sen_total = sum(sen.values()) or 1
        option_insight.append({
            "option": opt,
            "votes": option_counts[opt],
            "share": round(option_counts[opt] / total_responses, 3) if total_responses else 0.0,
            "respondent_reports": res["total"],
            "respondent_open": res["open"],
            "respondent_resolution_rate": round(res["completed"] / res["total"], 3) if res["total"] else None,
            "negative_share": round(sen["negative"] / sen_total, 3),
            "sentiment": sen,
        })
    option_insight.sort(key=lambda x: -x["votes"])

    respondent_stats = headline_stats(respondent_rows, now) if respondent_rows else {}

    return {
        "survey_id": survey.get("id"),
        "question": survey.get("question"),
        "options": options,
        "audience_label": survey.get("audience_label") or describe_audience(survey.get("audience")),
        "sent_count": sent_count,
        "sent_at": survey.get("sent_at"),
        "response_count": total_responses,
        "response_rate": response_rate,
        "option_counts": option_counts,
        "option_insight": option_insight,
        "cross_tab": {
            "by_ward": [{"key": k, "options": v, "total": sum(v.values())}
                        for k, v in sorted(cross_ward.items(), key=lambda x: -sum(x[1].values()))[:15]],
            "by_zone": [{"key": k, "options": v, "total": sum(v.values())}
                        for k, v in sorted(cross_zone.items(), key=lambda x: -sum(x[1].values()))[:12]],
            "by_category": [{"key": k, "options": v, "total": sum(v.values())}
                            for k, v in sorted(cross_cat.items(), key=lambda x: -sum(x[1].values()))],
        },
        "respondent_profile": {
            "total_reports": respondent_stats.get("total", 0),
            "open": respondent_stats.get("open", 0),
            "resolution_rate": respondent_stats.get("resolution_rate", 0.0),
            "negative_rate": respondent_stats.get("negative_rate", 0.0),
            "avg_open_age_days": respondent_stats.get("avg_open_age_days", 0.0),
            "top_categories": respondent_stats.get("top_categories", []),
            "top_issues": respondent_stats.get("top_issues", []),
        },
    }
