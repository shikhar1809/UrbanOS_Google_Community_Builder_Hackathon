"""HTTP API for the WhatsApp sandbox.

Mounted only on the `sandbox` Cloud Function (see main.py). It runs the same
intake code as the production webhook: a chat message becomes the form Twilio
would post, and the reply is read back from the TwiML the pipeline returns.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import logging
import os
import re
import xml.etree.ElementTree as ET

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

import sim
from main import _process_whatsapp_sync, firestore_db
from state import clear_session

logger = logging.getLogger(__name__)

PHONE_PATTERN = r"^\+?\d{8,15}$"
MEDIA_ID_PATTERN = r"^[0-9a-f]{32}$"

sim_app = FastAPI(title="UrbanOS WhatsApp sandbox", docs_url=None, redoc_url=None, openapi_url=None)


def access_code() -> str:
    """Shared secret for the chat UI, read per request so the secret can be
    rotated without a code change. When unset the API is open, which is only
    appropriate for local development."""
    return os.getenv("SIM_ACCESS_CODE", "")


def require_access(request: Request) -> None:
    expected = access_code()
    if not expected:
        return
    supplied = request.headers.get("X-Sim-Code", "")
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Access code required")


def _sandbox_db() -> sim.NamespacedDB:
    if firestore_db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return sim.NamespacedDB(firestore_db)


def _sender(phone: str) -> str:
    # Same identity format Twilio uses for WhatsApp senders.
    return "whatsapp:+" + re.sub(r"\D", "", phone)


def _twiml_bodies(xml_bytes: bytes) -> list[str]:
    root = ET.fromstring(xml_bytes)
    return [message.text or "" for message in root.iter("Message")]


class SimMessage(BaseModel):
    phone: str = Field(pattern=PHONE_PATTERN)
    body: str = Field(default="", max_length=1000)
    media_id: str | None = Field(default=None, pattern=MEDIA_ID_PATTERN)
    media_type: str | None = Field(default=None, max_length=40)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class SimMedia(BaseModel):
    content_type: str = Field(max_length=40)
    data: str = Field(max_length=1_000_000)  # base64; ~700KB decoded


class SimReset(BaseModel):
    phone: str = Field(pattern=PHONE_PATTERN)


@sim_app.get("/sim/api/status", dependencies=[Depends(require_access)])
def status():
    return {"ok": True, "access_code_required": bool(access_code())}


@sim_app.post("/sim/api/message", dependencies=[Depends(require_access)])
async def send_message(msg: SimMessage):
    if msg.media_id and msg.media_type not in sim.ALLOWED_MEDIA_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported media type")

    form = {"From": _sender(msg.phone), "Body": msg.body.strip()}
    if msg.media_id:
        form.update({
            "NumMedia": "1",
            "MediaUrl0": sim.SIM_MEDIA_PREFIX + msg.media_id,
            "MediaContentType0": msg.media_type,
        })
    if msg.latitude is not None and msg.longitude is not None:
        form.update({"Latitude": str(msg.latitude), "Longitude": str(msg.longitude)})

    db = _sandbox_db()
    captured: list[str] = []
    try:
        with sim.session(db, captured):
            # Same threadpool offload as the production webhook.
            result = await asyncio.to_thread(_process_whatsapp_sync, form, BackgroundTasks(), db)
        twiml_replies = _twiml_bodies(result.body)
    except Exception:
        logger.exception("[SANDBOX] Intake failed for %s", msg.phone)
        raise HTTPException(status_code=500, detail="The bot could not process that message. Please try again.")

    replies = [text for text in captured + twiml_replies if text.strip()]
    return {"replies": [{"text": text} for text in replies]}


@sim_app.post("/sim/api/media", dependencies=[Depends(require_access)])
async def upload_media(media: SimMedia):
    if media.content_type not in sim.ALLOWED_MEDIA_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported media type")
    try:
        raw = base64.b64decode(media.data, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="Media is not valid base64")
    if not raw:
        raise HTTPException(status_code=422, detail="Media is empty")
    if len(raw) > sim.MAX_MEDIA_BYTES:
        raise HTTPException(status_code=413, detail="Media is larger than 700KB")

    media_id = sim.store_media(_sandbox_db(), media.content_type, raw)
    return {"id": media_id, "url": f"/sim/api/media/{media_id}"}


@sim_app.get("/sim/api/media/{media_id}")
def get_media(media_id: str):
    # Not behind the access code: <img> and <audio> tags cannot send headers.
    # Media ids are 128-bit random values, so they are not guessable.
    found = sim.read_media(_sandbox_db(), media_id)
    if not found:
        raise HTTPException(status_code=404, detail="Not found")
    data, content_type = found
    return Response(content=data, media_type=content_type, headers={"Cache-Control": "private, max-age=3600"})


@sim_app.post("/sim/api/reset", dependencies=[Depends(require_access)])
def reset_session(req: SimReset):
    clear_session(_sandbox_db(), _sender(req.phone))
    return {"ok": True}
