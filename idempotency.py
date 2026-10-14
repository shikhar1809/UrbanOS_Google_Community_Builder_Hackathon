"""Webhook idempotency.

Twilio redelivers a webhook when it times out or gets a 5xx, and intake is not
naturally idempotent: a redelivery would file a second civic report for one
citizen message, inflating the demand count that drives sanctioning. Because
triage runs several model calls inline, timeouts are exactly what happens under
load, so retries are the normal case rather than the rare one.

Every delivery carries a provider message id. The first delivery claims that id
with an atomic Firestore ``create`` (which fails if the document already
exists), processes the message, and stores its reply. A redelivery finds the
claim and replays the stored reply instead of running the pipeline again.

A claim whose processing never finished (the instance crashed, or was evicted
mid-triage) would otherwise block that message forever, so a claim older than
``CLAIM_TTL_SECONDS`` is treated as abandoned and may be taken over.

Fails OPEN: if the claim store is unreachable, the message is processed. A
duplicate report is a worse outcome than a dropped one, but an outage that
silently swallowed every citizen report would be worse still.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

COLLECTION = "processed_messages"

# How long a claim may sit unfinished before another delivery may take it over.
# Comfortably longer than the worst-case triage (several sequential model
# calls), so a slow request is never treated as abandoned.
CLAIM_TTL_SECONDS = 300

STATUS_PROCESSING = "processing"
STATUS_DONE = "done"


class Claim:
    """Outcome of trying to claim a message id.

    ``granted`` means this caller should process the message. Otherwise the
    message is a duplicate: ``reply`` holds the original response when it is
    known, and is None while the first delivery is still in flight.
    """

    __slots__ = ("granted", "reply", "message_id")

    def __init__(self, granted: bool, reply: Optional[str] = None, message_id: Optional[str] = None):
        self.granted = granted
        self.reply = reply
        self.message_id = message_id

    def __repr__(self) -> str:
        return f"Claim(granted={self.granted}, reply={'set' if self.reply else 'none'})"


def _doc(db, message_id: str):
    return db.collection(COLLECTION).document(message_id)


def _is_stale(data: dict, now: float) -> bool:
    if data.get("status") == STATUS_DONE:
        return False
    claimed_at = data.get("claimed_at_epoch")
    if not isinstance(claimed_at, (int, float)):
        return True  # unreadable claim: treat as abandoned rather than wedge the sender
    return (now - claimed_at) > CLAIM_TTL_SECONDS


def claim(db, message_id: Optional[str]) -> Claim:
    """Try to claim ``message_id`` for processing."""
    # No id (the sandbox, or a provider that does not send one): nothing to
    # deduplicate against, so process normally.
    if not message_id or db is None:
        return Claim(granted=True, message_id=message_id)

    now = time.time()
    record = {
        "message_id": message_id,
        "status": STATUS_PROCESSING,
        "claimed_at": datetime.now(timezone.utc).isoformat(),
        "claimed_at_epoch": now,
    }

    try:
        _doc(db, message_id).create(record)
        return Claim(granted=True, message_id=message_id)
    except Exception as exc:
        if type(exc).__name__ != "AlreadyExists":
            # The claim store is unreachable. Fail open.
            logger.error("[IDEMPOTENCY] claim failed for %s: %s", message_id, exc, exc_info=True)
            return Claim(granted=True, message_id=message_id)

    # Someone else holds the claim.
    try:
        snap = _doc(db, message_id).get()
        data = (snap.to_dict() or {}) if getattr(snap, "exists", False) else {}
    except Exception as exc:
        logger.error("[IDEMPOTENCY] could not read claim %s: %s", message_id, exc, exc_info=True)
        return Claim(granted=True, message_id=message_id)

    if _is_stale(data, now):
        logger.warning("[IDEMPOTENCY] taking over abandoned claim %s", message_id)
        try:
            _doc(db, message_id).set(record)
        except Exception as exc:
            logger.error("[IDEMPOTENCY] could not take over %s: %s", message_id, exc, exc_info=True)
        return Claim(granted=True, message_id=message_id)

    logger.info("[IDEMPOTENCY] duplicate delivery of %s, replaying stored reply", message_id)
    return Claim(granted=False, reply=data.get("reply"), message_id=message_id)


def record_reply(db, message_id: Optional[str], reply: str) -> None:
    """Store the reply so a later redelivery replays it rather than reprocessing."""
    if not message_id or db is None:
        return
    try:
        _doc(db, message_id).set({
            "message_id": message_id,
            "status": STATUS_DONE,
            "reply": reply,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "completed_at_epoch": time.time(),
        }, merge=True)
    except Exception as exc:
        # The reply is already on its way to the citizen; losing the record only
        # costs us replay on a retry.
        logger.error("[IDEMPOTENCY] could not record reply for %s: %s", message_id, exc, exc_info=True)


def release(db, message_id: Optional[str]) -> None:
    """Drop a claim after a failed attempt, so the retry is processed rather
    than silently swallowed as a duplicate."""
    if not message_id or db is None:
        return
    try:
        _doc(db, message_id).delete()
    except Exception as exc:
        logger.error("[IDEMPOTENCY] could not release claim %s: %s", message_id, exc, exc_info=True)
