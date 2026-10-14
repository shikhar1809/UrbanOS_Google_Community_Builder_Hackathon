"""Plumbing for the WhatsApp sandbox.

The sandbox runs the production intake pipeline (main._process_whatsapp_sync)
unchanged. Three things differ, and this module supplies them:

* Storage: every collection is namespaced under ``sim_`` so the sandbox can
  never read or write production data.
* Media: attachments live in Firestore and are referenced as
  ``sim://media/<id>`` instead of Twilio media URLs.
* Outbound: replies are captured for the chat UI instead of going through
  Twilio.

Each piece is driven by a context variable that only the sandbox sets, so
production code paths behave exactly as before.
"""

from __future__ import annotations

import contextlib
import contextvars
import re
import uuid
from datetime import datetime, timezone
from typing import Callable, Iterator, Optional

SIM_MEDIA_PREFIX = "sim://media/"
COLLECTION_PREFIX = "sim_"
MEDIA_COLLECTION = "media"
MAX_MEDIA_BYTES = 700 * 1024

ALLOWED_MEDIA_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
}

_MEDIA_ID_RE = re.compile(r"[0-9a-f]{32}")

_db_var: contextvars.ContextVar = contextvars.ContextVar("sim_db", default=None)
_sink_var: contextvars.ContextVar = contextvars.ContextVar("sim_outbound", default=None)


class NamespacedDB:
    """Proxy that prefixes every collection name, so the production pipeline
    runs against ``sim_*`` collections while its code stays unchanged."""

    def __init__(self, db, prefix: str = COLLECTION_PREFIX):
        self._db = db
        self._prefix = prefix

    def collection(self, name: str):
        return self._db.collection(self._prefix + name)

    def __getattr__(self, item):
        return getattr(self._db, item)


@contextlib.contextmanager
def session(db, outbox: list) -> Iterator[None]:
    """Route one request through the sandbox: sim storage, sim media, and
    outbound replies appended to ``outbox``."""
    db_token = _db_var.set(db)
    sink_token = _sink_var.set(lambda to, body: outbox.append(body))
    try:
        yield
    finally:
        _sink_var.reset(sink_token)
        _db_var.reset(db_token)


def outbound_sink() -> Optional[Callable[[str, str], None]]:
    """The active reply capture, or None outside the sandbox."""
    return _sink_var.get()


def is_sim_media(media_url: Optional[str]) -> bool:
    return bool(media_url) and media_url.startswith(SIM_MEDIA_PREFIX)


def load_media(media_url: str) -> Optional[tuple[bytes, str]]:
    """Resolve a ``sim://media/<id>`` URL against the active sandbox database."""
    db = _db_var.get()
    if db is None or not is_sim_media(media_url):
        return None
    return read_media(db, media_url[len(SIM_MEDIA_PREFIX):])


def read_media(db, media_id: str) -> Optional[tuple[bytes, str]]:
    if not _MEDIA_ID_RE.fullmatch(media_id or ""):
        return None
    snap = db.collection(MEDIA_COLLECTION).document(media_id).get()
    if not snap.exists:
        return None
    data = snap.to_dict()
    return bytes(data["data"]), data["content_type"]


def store_media(db, content_type: str, raw: bytes) -> str:
    media_id = uuid.uuid4().hex
    db.collection(MEDIA_COLLECTION).document(media_id).set({
        "content_type": content_type,
        "data": raw,
        "size": len(raw),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return media_id
