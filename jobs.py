"""Deferred work for the intake pipeline.

Triage is several seconds of model time. Running it inside the webhook is what
makes WhatsApp deliveries time out, which makes the provider retry, which is
why intake has to deduplicate at all. This module lets a filed report be
triaged after the citizen has already been answered.

Backends
--------
``inline``      run immediately, which is the historical behaviour and the default.
``background``  run after the response is sent, via FastAPI's BackgroundTasks.
                Local use only: main._run_asgi buffers the response until the
                whole ASGI call returns, so on Firebase the citizen still waits.
``queue``       hand the report to a Cloud Tasks queue. The only backend that
                both answers at once on Firebase and survives a lost instance.

The queue
---------
The report is saved before anything is enqueued, so the database and not the
queue is the record of what is owed. A queued report carries an open flag until
it is fully finished, and a scheduled sweep re-enqueues any that have gone quiet.
That covers a failed enqueue and a task that ran out of retries with one path. The
flag also keeps the sweep away from old and simulated reports, which would
otherwise start spending model quota the moment it was deployed.

Everything here is idempotent: a report already triaged is skipped, so a
repeated job is harmless.

Two messages
------------
A deferred report is answered with an instant receipt, before triage has decided
anything, so the category, ward and officer follow in a second message. Only a
report flagged at filing gets one: draining an old backlog must not message
citizens who were already given a full receipt.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from google.cloud.firestore import SERVER_TIMESTAMP

import idempotency as IDEM
import sim
import triage_rules

logger = logging.getLogger(__name__)

COLLECTION = "messages"
LIVE_UPDATED_FIELD = "live_updated_at"

STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

BACKEND_INLINE = "inline"
BACKEND_BACKGROUND = "background"
BACKEND_QUEUE = "queue"
LEFT_FOR_SWEEP = "sweep"

DETAILS_FIELD = "details_notification"
DETAILS_PENDING = "pending"
DETAILS_SENT = "sent"

# Each name is the Cloud Function that drains that queue, declared in main.py.
LANE_NORMAL = "normal"
LANE_URGENT = "urgent"
QUEUE_FOR_LANE = {LANE_NORMAL: "triagetask", LANE_URGENT: "triagetaskurgent"}

QUEUE_OPEN_FIELD = "triage_queue_open"
LANE_FIELD = "triage_lane"
ENQUEUED_AT_FIELD = "triage_enqueued_at"

# Longer than a task's own retries (see main.TRIAGE_RETRY), so the sweep does not
# pile a second task on top of one that is still being retried.
SWEEP_GRACE_SECONDS = 20 * 60
SWEEP_GIVE_UP_SECONDS = 72 * 3600
SWEEP_SCAN_LIMIT = 2000
SWEEP_ENQUEUE_LIMIT = 500


def backend() -> str:
    """Read per call so it can be switched without a redeploy."""
    choice = os.getenv("TRIAGE_BACKEND", BACKEND_INLINE).strip().lower()
    if choice not in (BACKEND_INLINE, BACKEND_BACKGROUND, BACKEND_QUEUE):
        logger.warning("[JOBS] unknown TRIAGE_BACKEND %r, falling back to inline", choice)
        return BACKEND_INLINE
    return choice


def defers(db) -> bool:
    """Whether a report filed against `db` is triaged after the citizen is answered.

    The sandbox and the stream always triage inline: they discard their background
    tasks, and queue workers read the production database, so a deferred simulated
    report would never be triaged at all.
    """
    return backend() != BACKEND_INLINE and not isinstance(db, sim.NamespacedDB)


def uses_fast_triage(description: str, media_type: str = "") -> bool:
    return (
        os.getenv("TRIAGE_RULES_FIRST", "").strip().lower() in {"1", "true"}
        and not media_type.startswith("image/")
        and triage_rules.recognises_issue(description)
    )


def lane_for(report: dict) -> str:
    """Decide from keyword rules alone, since the point is to run before any model."""
    urgent = triage_rules.is_urgent(report.get("body") or "", report.get("extracted_location") or "",
                                    report.get("latitude"), report.get("longitude"))
    return LANE_URGENT if urgent else LANE_NORMAL


def queue_fields(report: dict) -> dict:
    """Fields that put a report under the queue's care. Empty for other backends."""
    if backend() != BACKEND_QUEUE:
        return {}
    return {QUEUE_OPEN_FIELD: True, LANE_FIELD: lane_for(report)}


def _enqueue(lane: str, doc_id: str) -> None:
    from firebase_admin import functions
    # The explicit "data" wrapper is required. The Python Admin SDK sends this
    # value as the whole request body, and the worker's SDK rejects, with a 400,
    # any body that has no "data" key. Enqueueing still reports success.
    functions.task_queue(QUEUE_FOR_LANE.get(lane, QUEUE_FOR_LANE[LANE_NORMAL])).enqueue({"data": {"doc_id": doc_id}})


def details_fields(sender: str, language: str) -> dict:
    """Fields that ask for a details message once this report has been triaged.

    Empty for a simulated citizen: anything that messages citizens must skip them.
    """
    if sim.is_simulated_sender(sender):
        return {}
    return {DETAILS_FIELD: DETAILS_PENDING, "citizen_language": language}


def dispatch(db, doc_id: str, background_tasks=None, runner=None, report=None) -> str:
    """Schedule triage for one filed report. Returns the backend actually used."""
    runner = runner or process_triage
    chosen = backend()

    if chosen == BACKEND_QUEUE:
        try:
            _enqueue((report or {}).get(LANE_FIELD, LANE_NORMAL), doc_id)
            return BACKEND_QUEUE
        except Exception as e:
            # Not run inline: a queue outage must not turn into webhook timeouts.
            logger.error("[JOBS] could not enqueue %s, leaving it for the sweep: %s", doc_id, e, exc_info=True)
            return LEFT_FOR_SWEEP

    if chosen == BACKEND_BACKGROUND and background_tasks is not None:
        background_tasks.add_task(runner, db, doc_id)
        return BACKEND_BACKGROUND

    if chosen == BACKEND_BACKGROUND:
        logger.warning("[JOBS] no background queue available for %s, running inline", doc_id)

    runner(db, doc_id)
    return BACKEND_INLINE


def _triage_fields(result: dict) -> dict:
    fields = {
        "category": result["category"],
        "priority": result["priority"],
        "sentiment": result["feasibility"],
        "summary": result["summary"],
        "original_language": result["original_language"],
        "constituency_zone": result["constituency_zone"],
        "estimated_budget": result["estimated_budget"],
        "semantic_tag": result["semantic_tag"],
        "visual_evidence": result["visual_evidence"],
        "responsible_body": result["responsible_body"],
        "ward_no": result["ward_no"],
        "ward_officer": result["ward_officer"],
        "triage_source": result.get("triage_source"),
    }
    if result.get("extracted_location"):
        fields["extracted_location"] = result["extracted_location"]
    return fields


def _estimated_pin(report: dict, result: dict) -> dict:
    """Place a text-only location on the map, as inline intake does. The geofence
    still applies, so a wrong guess cannot land off the map."""
    lat, lng = result.get("estimated_latitude"), result.get("estimated_longitude")
    if report.get("latitude") is not None or lat is None or lng is None:
        return {}
    import guardrails
    if guardrails.check_coordinates(lat, lng)["verdict"] != "accept":
        return {}
    return {"latitude": lat, "longitude": lng}


def process_triage(db, doc_id: str, triage_module=None, client=None, media_loader=None, notifier=None,
                   rules_fallback: bool = False, client_factory=None) -> bool:
    """Classify an already-filed report and write the fields back.

    Returns True when the report ends up triaged. Never raises: this runs
    detached from any request, so there is nobody to return an error to, and a
    failure must leave the report retriable rather than lost.

    `notifier(report) -> bool` delivers the details message for a flagged report
    and reports whether it was accepted for delivery.

    With `rules_fallback`, a report no model could classify gets keyword-rule
    fields so the citizen and the dashboard are not left waiting out an outage. It
    stays failed, and so returns False, because a model should still look at it.
    """
    import triage as default_triage
    triage_module = triage_module or default_triage

    try:
        ref = db.collection(COLLECTION).document(doc_id)
        snap = ref.get()
        if not getattr(snap, "exists", False):
            logger.warning("[JOBS] report %s no longer exists", doc_id)
            return False
        report = snap.to_dict() or {}
    except Exception as e:
        logger.error("[JOBS] could not load report %s: %s", doc_id, e, exc_info=True)
        return False

    if report.get("triage_status") == STATUS_DONE:
        # Already done; a repeated job must not re-bill the model. It may still
        # owe the citizen a message that an earlier attempt could not deliver.
        _send_details(db, ref, doc_id, report, notifier)
        return True

    description = report.get("body") or ""
    media_type = report.get("media_type") or ""
    fast = rules_fallback and uses_fast_triage(description, media_type)
    if not fast and client_factory:
        client = client_factory()
    image_part = None
    if media_loader and not fast:
        try:
            image_part = media_loader(report.get("media_url"), report.get("media_type") or "")
        except Exception as e:
            logger.warning("[JOBS] could not load evidence photo for %s: %s", doc_id, e)

    result = triage_module.run_triage(
        None if fast else client,
        description=description,
        location_raw=report.get("extracted_location") or "",
        loc_source=report.get("location_source") or "",
        image_part=image_part,
        lat=report.get("latitude"),
        lng=report.get("longitude"),
        rules_fallback=rules_fallback,
    )

    if not result.get("ok") and not (fast and result.get("triage_source") == "rules"):
        # Leave it pending-but-marked so a later run picks it up again.
        update = {
            "triage_status": STATUS_FAILED,
            LIVE_UPDATED_FIELD: SERVER_TIMESTAMP,
            "triage_attempted_at": datetime.now(timezone.utc).isoformat(),
        }
        decided_by_rules = result.get("triage_source") == "rules"
        if decided_by_rules:
            update.update(_triage_fields(result))
            update.update(_estimated_pin(report, result))
        if _safe_update(ref, update, doc_id) and decided_by_rules:
            _send_details(db, ref, doc_id, {**report, **update}, notifier)
        return False

    update = {
        **_triage_fields(result),
        **_estimated_pin(report, result),
        "triage_status": STATUS_DONE,
        LIVE_UPDATED_FIELD: SERVER_TIMESTAMP,
        "triaged_at": datetime.now(timezone.utc).isoformat(),
    }

    if not _safe_update(ref, update, doc_id):
        return False
    logger.info("[JOBS] triaged %s as %s/%s", doc_id, result["category"], result["priority"])
    _send_details(db, ref, doc_id, {**report, **update}, notifier)
    return True


def _send_details(db, ref, doc_id: str, report: dict, notifier) -> None:
    """Deliver a flagged report's details message once, even with competing jobs."""
    if notifier is None or report.get(DETAILS_FIELD) != DETAILS_PENDING:
        return

    claim_id = f"triage-details-{doc_id}"
    if not IDEM.claim(db, claim_id).granted:
        return

    try:
        delivered = notifier(report)
    except Exception as e:
        logger.error("[JOBS] details message failed for %s: %s", doc_id, e, exc_info=True)
        delivered = False

    if not delivered:
        IDEM.release(db, claim_id)  # the flag stays pending, so a later run retries
        return

    _safe_update(ref, {
        DETAILS_FIELD: DETAILS_SENT,
        "details_sent_at": datetime.now(timezone.utc).isoformat(),
    }, doc_id)
    IDEM.record_reply(db, claim_id, DETAILS_SENT)


def _safe_update(ref, data: dict, doc_id: str) -> bool:
    try:
        ref.update(data)
        return True
    except Exception as e:
        logger.error("[JOBS] could not write triage for %s: %s", doc_id, e, exc_info=True)
        return False


def pending_reports(db, limit: int = 100) -> list:
    """Report ids that still need triage or an undelivered details message.

    Covers both reports filed while triage was deferred and reports whose
    triage failed, which is the state the whole backlog landed in while the
    model quota was exhausted.
    """
    found: list[str] = []
    wanted = (("triage_status", STATUS_PENDING),
              ("triage_status", STATUS_FAILED),
              (DETAILS_FIELD, DETAILS_PENDING))
    for field, value in wanted:
        if len(found) >= limit:
            break
        try:
            query = db.collection(COLLECTION)
            try:
                from google.cloud.firestore_v1.base_query import FieldFilter
                query = query.where(filter=FieldFilter(field, "==", value))
            except Exception:
                query = query.where(field, "==", value)
            # A report awaiting triage usually awaits its details message too, so
            # ask for `limit` and let the check below cap and de-duplicate.
            for doc in query.limit(limit).stream():
                if len(found) < limit and doc.id not in found:
                    found.append(doc.id)
        except Exception as e:
            logger.error("[JOBS] could not list %s=%s reports: %s", field, value, e)
    return found


def retriage_pending(db, limit: int = 100, **kwargs) -> dict:
    """Drain the backlog. Safe to call repeatedly."""
    ids = pending_reports(db, limit)
    triaged = sum(1 for doc_id in ids if process_triage(db, doc_id, **kwargs))
    return {"found": len(ids), "triaged": triaged, "still_pending": len(ids) - triaged}


def process_queued_report(db, doc_id: str, **kwargs) -> bool:
    """Run one queue task. True means nothing more is owed, so the task may end.

    False asks the queue to retry. Never raises, for the same reason as
    process_triage.
    """
    process_triage(db, doc_id, rules_fallback=True, **kwargs)
    try:
        ref = db.collection(COLLECTION).document(doc_id)
        snap = ref.get()
        if not getattr(snap, "exists", False):
            return True  # deleted since it was filed: retrying cannot help
        report = snap.to_dict() or {}
    except Exception as e:
        logger.error("[JOBS] could not re-read report %s: %s", doc_id, e, exc_info=True)
        return False

    finished = report.get("triage_status") == STATUS_DONE and report.get(DETAILS_FIELD) != DETAILS_PENDING
    if not finished:
        return False
    # If this write fails the sweep repeats the task, which finds nothing left to do.
    _safe_update(ref, {QUEUE_OPEN_FIELD: False}, doc_id)
    return True


def _age_seconds(stamp, now: datetime):
    """Age of a naive ISO timestamp, the format reports are filed with. None if unreadable."""
    try:
        return (now - datetime.fromisoformat(stamp)).total_seconds()
    except (TypeError, ValueError):
        return None


def sweep_open_reports(db, now: datetime = None) -> dict:
    """Re-enqueue queued reports that have gone quiet. Safe to run repeatedly."""
    now = now or datetime.now()
    stats = {"scanned": 0, "enqueued": 0, "waiting": 0, "gave_up": 0, "enqueue_failed": False}

    query = db.collection(COLLECTION)
    try:
        from google.cloud.firestore_v1.base_query import FieldFilter
        query = query.where(filter=FieldFilter(QUEUE_OPEN_FIELD, "==", True))
    except Exception:
        query = query.where(QUEUE_OPEN_FIELD, "==", True)

    for doc in query.limit(SWEEP_SCAN_LIMIT).stream():
        stats["scanned"] += 1
        report = doc.to_dict() or {}
        ref = db.collection(COLLECTION).document(doc.id)

        filed_age = _age_seconds(report.get("timestamp"), now)
        if filed_age is not None and filed_age > SWEEP_GIVE_UP_SECONDS:
            logger.error("[JOBS] giving up on queued report %s after %.0f hours", doc.id, filed_age / 3600)
            _safe_update(ref, {QUEUE_OPEN_FIELD: False, "needs_review": True}, doc.id)
            stats["gave_up"] += 1
            continue

        ages = [a for a in (filed_age, _age_seconds(report.get(ENQUEUED_AT_FIELD), now)) if a is not None]
        if ages and min(ages) < SWEEP_GRACE_SECONDS:
            stats["waiting"] += 1
            continue

        if stats["enqueued"] >= SWEEP_ENQUEUE_LIMIT:
            stats["waiting"] += 1
            continue

        try:
            _enqueue(report.get(LANE_FIELD, LANE_NORMAL), doc.id)
        except Exception as e:
            logger.error("[JOBS] sweep could not enqueue %s, stopping this run: %s", doc.id, e, exc_info=True)
            stats["enqueue_failed"] = True
            break
        _safe_update(ref, {ENQUEUED_AT_FIELD: now.isoformat()}, doc.id)
        stats["enqueued"] += 1

    # A warning because anything here means a task went quiet, and because INFO
    # does not reach the logs on the deployed runtime.
    if stats["enqueued"] or stats["gave_up"] or stats["enqueue_failed"]:
        logger.warning("[JOBS] sweep: %s", stats)
    return stats
