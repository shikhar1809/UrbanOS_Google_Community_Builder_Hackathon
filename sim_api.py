"""HTTP API for the WhatsApp sandbox.

Mounted only on the `sandbox` Cloud Function (see main.py). It runs the same
intake code as the production webhook: a chat message becomes the form Twilio
would post, and the reply is read back from the TwiML the pipeline returns.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

import sim
import sim_stream
from main import _process_whatsapp_sync, firestore_db
from state import clear_session, update_session

logger = logging.getLogger(__name__)

PHONE_PATTERN = r"^\+?\d{8,15}$"
MEDIA_ID_PATTERN = r"^[0-9a-f]{32}$"

sim_app = FastAPI(title="UrbanOS WhatsApp sandbox", docs_url=None, redoc_url=None, openapi_url=None)


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


@sim_app.get("/sim/api/status")
def status():
    return {"ok": True}


@sim_app.post("/sim/api/message")
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


@sim_app.post("/sim/api/media")
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
    # Media ids are 128-bit random values, so they are not guessable.
    found = sim.read_media(_sandbox_db(), media_id)
    if not found:
        raise HTTPException(status_code=404, detail="Not found")
    data, content_type = found
    return Response(content=data, media_type=content_type, headers={"Cache-Control": "private, max-age=3600"})


@sim_app.get("/sim/api/inbox")
def inbox(phone: str = Query(pattern=PHONE_PATTERN), since: float | None = Query(default=None, ge=0)):
    """Broadcasts posted after `since`, for an open sandbox chat to show.

    Without `since`, nothing is returned and `latest` is the server's current
    time. A chat starts from that rather than its own clock: a laptop running a
    few seconds fast would otherwise skip a survey sent just after it opened.

    Receiving a survey puts the chat in the same state as a citizen who was just
    asked one, so a numeric reply is recorded as the answer.
    """
    if since is None:
        return {"messages": [], "latest": time.time()}
    db = _sandbox_db()
    query = db.collection("inbox")
    try:
        from google.cloud.firestore_v1.base_query import FieldFilter
        query = query.where(filter=FieldFilter("sent_epoch", ">", since))
    except Exception:
        query = query.where("sent_epoch", ">", since)
    items = [d.to_dict() for d in query.order_by("sent_epoch").limit(5).stream()]
    surveys = [i for i in items if i.get("kind") == "survey"]
    if surveys:
        update_session(db, _sender(phone), "awaiting_survey", {"survey_id": surveys[-1].get("survey_id")})
    return {
        "messages": [{"text": i.get("text", ""), "sent_epoch": i.get("sent_epoch")} for i in items],
        "latest": max([i.get("sent_epoch", since) for i in items], default=since),
    }


@sim_app.post("/sim/api/reset")
def reset_session(req: SimReset):
    clear_session(_sandbox_db(), _sender(req.phone))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Live stream
# ---------------------------------------------------------------------------

def _raw_db():
    if firestore_db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return firestore_db


class StreamStart(BaseModel):
    scenario: str = Field(default="steady", max_length=32)
    mode: Literal["pipeline", "dashboard"] = "pipeline"
    rate_per_min: int = Field(default=30, ge=1, le=sim_stream.BULK_MAX_RATE)
    max_events: int = Field(default=sim_stream.DEFAULT_MAX_EVENTS, ge=1, le=100000)


class StreamTick(BaseModel):
    run_id: str = Field(max_length=32)
    batch: int = Field(default=1, ge=1, le=sim_stream.BULK_MAX_BATCH)


def _stream_call(fn, *args):
    try:
        return fn(*args)
    except sim_stream.StreamError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


@sim_app.get("/sim/api/stream/state")
def stream_state():
    # Open on purpose so anyone can watch. Every sender in it is a masked,
    # simulated number, and watching spends nothing but one read.
    state = sim_stream.read_state(_raw_db())
    state["scenarios"] = sim_stream.scenarios()
    state["limits"] = {"min_rate": sim_stream.MIN_RATE, "max_rate": sim_stream.MAX_RATE,
                       "max_batch": sim_stream.MAX_BATCH, "hard_max_events": sim_stream.HARD_MAX_EVENTS}
    state["dashboard_limits"] = {"min_rate": sim_stream.MIN_RATE, "max_rate": sim_stream.BULK_MAX_RATE,
                                 "max_batch": sim_stream.BULK_MAX_BATCH, "hard_max_events": sim_stream.BULK_MAX_EVENTS,
                                 "daily_events": sim_stream.BULK_DAILY_EVENTS}
    return state


RUN_ID_PATTERN = r"^[0-9a-f]{10}$"
FILE_FORMATS = {
    "jsonl": ("application/x-ndjson; charset=utf-8", sim_stream.rows_to_jsonl),
    "csv": ("text/csv; charset=utf-8", sim_stream.rows_to_csv),
}


@sim_app.get("/sim/api/stream/files")
def stream_files():
    # Open like the state endpoint: every row is simulated, with masked senders.
    return {"files": sim_stream.list_files(_raw_db())}


@sim_app.get("/sim/api/stream/files/{run_id}")
def stream_file(run_id: str = Path(pattern=RUN_ID_PATTERN), format: str = Query(default="json", pattern="^(json|jsonl|csv)$")):
    rows = _stream_call(sim_stream.file_rows, _raw_db(), run_id)
    if format == "json":
        return {"run_id": run_id, "rows": rows}
    content_type, render = FILE_FORMATS[format]
    return Response(content=render(rows), media_type=content_type, headers={
        "Content-Disposition": f'attachment; filename="urbanos-stream-{run_id}.{format}"',
    })


@sim_app.post("/sim/api/stream/start")
def stream_start(req: StreamStart):
    return _stream_call(sim_stream.start, _raw_db(), req.scenario, req.rate_per_min, req.max_events, req.mode)


@sim_app.post("/sim/api/stream/tick")
def stream_tick(req: StreamTick):
    return _stream_call(sim_stream.tick, _raw_db(), _process_whatsapp_sync, req.run_id, req.batch)


@sim_app.post("/sim/api/stream/stop")
def stream_stop():
    return _stream_call(sim_stream.stop, _raw_db())


@sim_app.post("/sim/api/stream/purge")
def stream_purge():
    return _stream_call(sim_stream.purge, _raw_db())
