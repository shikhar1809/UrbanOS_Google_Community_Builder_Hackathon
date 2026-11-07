"""Deferred work for the intake pipeline.

Triage is several seconds of model time. Running it inside the webhook is what
makes WhatsApp deliveries time out, which makes the provider retry, which is
why intake has to deduplicate at all. This module lets a filed report be
triaged after the citizen has already been answered.

Backends
--------
``inline``      run immediately, which is the historical behaviour and the default.
``background``  run after the response is sent, via FastAPI's BackgroundTasks.

A durable queue (Pub/Sub, Cloud Tasks) is the next step and slots in at
``dispatch`` alone. It is deliberately not implemented here: it cannot be
verified from this environment, and an untested dispatch path that silently
drops civic reports is worse than a known in-process one. ``background`` does
not survive an instance being evicted mid-task, which is exactly what
``retriage_pending`` exists to repair.

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

import idempotency as IDEM
import sim

logger = logging.getLogger(__name__)

COLLECTION = "messages"

STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

BACKEND_INLINE = "inline"
BACKEND_BACKGROUND = "background"

DETAILS_FIELD = "details_notification"
DETAILS_PENDING = "pending"
DETAILS_SENT = "sent"


def backend() -> str:
    """Read per call so it can be switched without a redeploy."""
    choice = os.getenv("TRIAGE_BACKEND", BACKEND_INLINE).strip().lower()
    if choice not in (BACKEND_INLINE, BACKEND_BACKGROUND):
        logger.warning("[JOBS] unknown TRIAGE_BACKEND %r, falling back to inline", choice)
        return BACKEND_INLINE
    return choice


def details_fields(sender: str, language: str) -> dict:
    """Fields that ask for a details message once this report has been triaged.

    Empty for a simulated citizen: anything that messages citizens must skip them.
    """
    if sim.is_simulated_sender(sender):
        return {}
    return {DETAILS_FIELD: DETAILS_PENDING, "citizen_language": language}


def dispatch(db, doc_id: str, background_tasks=None, runner=None) -> str:
    """Schedule triage for one filed report. Returns the backend actually used."""
    runner = runner or process_triage
    chosen = backend()

    if chosen == BACKEND_BACKGROUND and background_tasks is not None:
        background_tasks.add_task(runner, db, doc_id)
        return BACKEND_BACKGROUND

    if chosen == BACKEND_BACKGROUND:
        logger.warning("[JOBS] no background queue available for %s, running inline", doc_id)

    runner(db, doc_id)
    return BACKEND_INLINE


def process_triage(db, doc_id: str, triage_module=None, client=None, media_loader=None, notifier=None) -> bool:
    """Classify an already-filed report and write the fields back.

    Returns True when the report ends up triaged. Never raises: this runs
    detached from any request, so there is nobody to return an error to, and a
    failure must leave the report retriable rather than lost.

    `notifier(report) -> bool` delivers the details message for a flagged report
    and reports whether it was accepted for delivery.
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

    image_part = None
    if media_loader:
        try:
            image_part = media_loader(report.get("media_url"), report.get("media_type") or "")
        except Exception as e:
            logger.warning("[JOBS] could not load evidence photo for %s: %s", doc_id, e)

    result = triage_module.run_triage(
        client,
        description=report.get("body") or "",
        location_raw=report.get("extracted_location") or "",
        loc_source=report.get("location_source") or "",
        image_part=image_part,
        lat=report.get("latitude"),
        lng=report.get("longitude"),
    )

    if not result.get("ok"):
        # Leave it pending-but-marked so a later run picks it up again.
        _safe_update(ref, {
            "triage_status": STATUS_FAILED,
            "triage_attempted_at": datetime.now(timezone.utc).isoformat(),
        }, doc_id)
        return False

    update = {
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
        "triage_status": STATUS_DONE,
        "triaged_at": datetime.now(timezone.utc).isoformat(),
    }
    if result.get("extracted_location"):
        update["extracted_location"] = result["extracted_location"]

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
