"""Desk workflow and historical measures based on explicitly recorded case dates."""

from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from math import ceil, floor
from typing import Literal

from google.cloud.firestore_v1 import SERVER_TIMESTAMP, transactional

import analytics as AN
from jobs import LIVE_UPDATED_FIELD
from planning_review import SIGNED_INTAKE, intake_formats
from sim import is_simulated_sender


Source = Literal["demo", "signed", "examples"]
Stage = Literal["all", "incoming", "acknowledged", "review", "resolved"]
Action = Literal["acknowledge", "resolve"]
MIN_ESTIMATE_SAMPLE = 5


class DeskError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def local_now() -> datetime:
    return datetime.now(AN.CASE_TIMEZONE).replace(tzinfo=None)


def _iso(value):
    return value.replace(tzinfo=AN.CASE_TIMEZONE).isoformat() if value else None


def _month(value: datetime, shift: int = 0) -> datetime:
    year, month = divmod(value.year * 12 + value.month - 1 + shift, 12)
    return datetime(year, month + 1, 1)


def _examples(now):
    """Illustrative records stay separate from every report stored in Firestore."""
    for index in range(25, 0, -1):
        month = _month(now, -index)
        for department, category in (("Jal Sansthan", "Water & Sanitation"), ("Nagar Nigam", "Infrastructure")):
            spike = 6 if month.month in (7, 8) and department == "Jal Sansthan" else 0
            for count in range(6 + spike):
                filed = month + timedelta(days=count + 1)
                closed = filed + timedelta(days=2 + count % 5 + spike)
                key = f"{department}-{month:%Y%m}-{count}"
                yield AN.normalize_record(key, {
                    "timestamp": filed, "resolved_at": closed, "status": "Completed",
                    "responsible_body": department, "category": category, "priority": "High",
                    "summary": f"Illustrative {category.lower()} case", "simulated": True,
                })
            if department == "Nagar Nigam" and index <= 6:
                filed = _month(month, 1) - timedelta(days=1)
                closed = filed + timedelta(days=30 + (7 - index) * 6)
                yield AN.normalize_record(f"followup-{month:%Y%m}", {
                    "timestamp": filed, "resolved_at": closed if closed <= now else None,
                    "status": "Completed" if closed <= now else "Open",
                    "responsible_body": department, "category": category, "priority": "Medium",
                    "summary": "Illustrative road repair follow-up", "simulated": True,
                })
    yield AN.normalize_record("example-incoming", {
        "reference_id": "EXAMPLE-01", "timestamp": now - timedelta(hours=3),
        "responsible_body": "Jal Sansthan", "category": "Water & Sanitation", "priority": "High",
        "status": "Open", "summary": "Water supply interruption near a school", "simulated": True,
    })


def records_for(records, source: Source, now: datetime):
    if source == "examples":
        return list(_examples(now))
    if source == "demo":
        return [row for row in records if row["simulated"] or is_simulated_sender(row["sender"])]
    return [row for row in records if row["intake_origin"] == SIGNED_INTAKE
            and not (row["simulated"] or is_simulated_sender(row["sender"]))]


def desk_stage(row):
    if row["status_bucket"] == "completed":
        return "resolved"
    if row["status_bucket"] != "open":
        return None
    if row["desk_acknowledged_at"]:
        return "acknowledged"
    if (row["responsible_body"].casefold() in {"", "unknown dept", "unassigned", "needs triage"}
            or row["triage_status"] == "pending"
            or (row["triage_status"] == "failed" and row["triage_source"] != "rules")):
        return "review"
    return "incoming"


def _closure(row, now):
    filed, closed = row["filed_at"], row["resolved_at"]
    return closed if row["status_bucket"] == "completed" and filed and closed and filed <= closed <= now else None


def _cohort(row):
    return row["responsible_body"].casefold(), row["category"].casefold()


def _estimate(durations):
    count = len(durations)
    estimate = {"sample_size": count, "minimum_sample": MIN_ESTIMATE_SAMPLE}
    if count >= MIN_ESTIMATE_SAMPLE:
        # The middle half of observed durations, rounded outwards in hours.
        estimate.update(low_hours=floor(durations[(count - 1) // 4]),
                        high_hours=ceil(durations[ceil((count - 1) * .75)]))
    return estimate


def queue(records, source: Source, stage: Stage, department: str, offset: int, limit: int, now: datetime,
          reference: str = ""):
    reference = reference.strip().lstrip("#").casefold()
    grouped = defaultdict(list)
    departments = sorted({row["responsible_body"] for row in records}, key=str.casefold)
    rows = []
    for row in records:
        closed = _closure(row, now)
        if closed:
            grouped[_cohort(row)].append((closed - row["filed_at"]).total_seconds() / 3600)
        state = desk_stage(row)
        if (state and (not department or row["responsible_body"] == department)
                and (not reference or str(row["reference_id"]).casefold() == reference)):
            rows.append((row, state))
    counts = Counter(state for _, state in rows)
    filtered = [(row, state) for row, state in rows
                if state == stage or (stage == "all" and (reference or state != "resolved"))]
    filtered.sort(key=lambda item: (-item[0]["priority_rank"], item[0]["filed_at"] or datetime.max, item[0]["id"]))
    items = []
    for row, state in filtered[offset:offset + limit]:
        mutable = source == "demo" and row["simulated"] and is_simulated_sender(row["sender"])
        items.append({
            "id": row["id"], "reference": row["reference_id"], "title": row["summary"] or row["category"],
            "department": row["responsible_body"], "category": row["category"], "priority": row["priority"],
            "intake_formats": intake_formats(row),
            "stage": state, "action": {"incoming": "acknowledge", "acknowledged": "resolve"}.get(state) if mutable else None,
            "filed_at": _iso(row["filed_at"]), "acknowledged_at": _iso(row["desk_acknowledged_at"]),
            "resolved_at": _iso(_closure(row, now)),
            "estimate": _estimate(sorted(grouped[_cohort(row)])) if state in {"incoming", "acknowledged"} else None,
        })
    return {"source": source, "departments": departments, "counts": dict(counts), "total": len(filtered), "items": items}


def history(records, department: str, now: datetime, truncated: bool):
    departments = sorted({row["responsible_body"] for row in records}, key=str.casefold)
    earliest = min((row["filed_at"] for row in records if row["filed_at"] and row["filed_at"] <= now), default=None)
    rows = [row for row in records if not department or row["responsible_body"] == department]
    filed, tracked, closures = [], [], []
    missing_filing, missing_closure = 0, 0
    for row in rows:
        start = row["filed_at"]
        if not start or start > now:
            missing_filing += 1
            continue
        filed.append(start)
        closed = _closure(row, now)
        if closed:
            closures.append(closed)
        elif row["status_bucket"] == "completed":
            missing_closure += 1
            continue
        tracked.append(start)
    filed.sort()
    tracked.sort()
    closures.sort()
    monthly = []
    for index in range(12, 0, -1):
        start, end = _month(now, -index), _month(now, 1 - index)
        prev_start, prev_end = _month(start, -12), _month(end, -12)
        covered = earliest is not None and earliest <= start
        prior_covered = earliest is not None and earliest <= prev_start
        monthly.append({
            "month": start.strftime("%Y-%m"),
            "filed": bisect_left(filed, end) - bisect_left(filed, start) if covered else None,
            "closed": bisect_left(closures, end) - bisect_left(closures, start) if covered else None,
            "backlog": bisect_left(tracked, end) - bisect_left(closures, end) if covered else None,
            "prior_year_filed": bisect_left(filed, prev_end) - bisect_left(filed, prev_start) if prior_covered else None,
        })
    return {"departments": departments, "monthly": monthly, "missing_filing_dates": missing_filing,
            "missing_closure_dates": missing_closure, "truncated": truncated,
            "current_unresolved": sum(row["status_bucket"] != "completed" for row in rows)}


def desk_update(transaction, ref, action: Action, now: datetime):
    snapshot = ref.get(transaction=transaction)
    if not snapshot.exists:
        raise DeskError(404, "Case not found.")
    raw = snapshot.to_dict()
    if not raw.get("simulated") or not is_simulated_sender(raw.get("sender")):
        raise DeskError(403, "Desk actions are available for simulated cases.")
    row = AN.normalize_record(ref.id, raw)
    state = desk_stage(row)
    if action == "acknowledge" and row["desk_acknowledged_at"]:
        return state
    if action == "resolve" and state == "resolved" and row["resolved_at"] and row["desk_acknowledged_at"]:
        return state
    update = {LIVE_UPDATED_FIELD: SERVER_TIMESTAMP}
    if action == "acknowledge" and state == "incoming":
        update["desk_acknowledged_at"] = now
        state = "acknowledged"
    elif action == "resolve" and state == "acknowledged":
        update.update(status="Completed", resolved_at=now)
        state = "resolved"
    else:
        raise DeskError(409, "This case cannot move to the requested stage.")
    transaction.update(ref, update)
    return state


def change_desk_stage(db, case_id: str, action: Action):
    ref = db.collection("messages").document(case_id)
    return transactional(desk_update)(db.transaction(), ref, action, datetime.now(timezone.utc))
