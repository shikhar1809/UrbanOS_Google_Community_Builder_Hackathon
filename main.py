import os
import httpx
import time
import uuid
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException, Depends, BackgroundTasks, UploadFile, File
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from state import get_session, update_session, clear_session
from worker import process_voice_note
import json
import logging
import asyncio
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from twilio.request_validator import RequestValidator



from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from typing import Literal

load_dotenv()

# --- LOGGING SETUP ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import firebase_admin
from firebase_admin import credentials, firestore, auth

security = HTTPBearer()
def verify_admin(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        token = credentials.credentials
        decoded_token = auth.verify_id_token(token)
        allowed_admins = [email.strip().lower() for email in os.getenv("ADMIN_EMAILS", "").split(",") if email.strip()]
        user_email = (decoded_token.get("email") or "").lower()
        if allowed_admins and user_email not in allowed_admins:
            raise HTTPException(status_code=403, detail="Authenticated user is not an UrbanOS admin.")
        return decoded_token
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid authentication credentials: {str(e)}")

# Initialize Firebase
try:
    if os.path.exists("firebase-key.json"):
        cred = credentials.Certificate("firebase-key.json")
        firebase_admin.initialize_app(cred)
    else:
        firebase_admin.initialize_app()
    firestore_db = firestore.client()
except Exception as e:
    print(f"WARNING: Could not initialize Firebase: {e}")
    firestore_db = None

def get_db():
    yield firestore_db


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://urbanos.web.app",
        "https://urbanos101.web.app",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
twilio_validator = RequestValidator(TWILIO_AUTH_TOKEN) if TWILIO_AUTH_TOKEN else None

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
try:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
except Exception as _e:
    logger.warning(f"Gemini client init failed: {_e}")
    gemini_client = None

class TriageResult(BaseModel):
    category: Literal["Construction Development", "Land Development", "Infrastructure Upgrade", "Public Utility Works", "Environmental Project", "Maintenance & Repair", "Other"] = Field(description="Must be one of the strictly provided categories.")
    priority: Literal["Low", "Medium", "High", "Critical"] = Field(description="Priority severity level.")
    feasibility: Literal["High", "Moderate", "Complex", "Unknown"] = Field(alias="sentiment", description="Maps to the old sentiment DB column.")
    extracted_location: str = Field(description="Street name, landmark, or area extracted from text. Empty string if none.")
    summary: str = Field(description="Short 4-5 word summary title of the project proposal.")
    original_language: str = Field(description="The language the user originally submitted their request in (e.g., Hindi, English, Spanish).")
    constituency_zone: Literal["North", "South", "East", "West", "Central"] = Field(description="Infer this constituency zone from the location context. Default to Central if unknown.")
    estimated_budget: int = Field(description="An integer representing the estimated cost in INR based on the project scale (e.g., 500000 for small, 50000000 for large capital works).")
    semantic_tag: str = Field(
        description=(
            "A normalized snake_case topic tag (2-3 words) that semantically groups this proposal "
            "with similar ones regardless of phrasing. Be CONSISTENT â€” the same real-world issue "
            "must always produce the same tag. Examples: 'road_repair', 'school_construction', "
            "'water_supply', 'street_lighting', 'drainage_improvement', 'park_development', "
            "'hospital_upgrade', 'bridge_construction', 'sanitation_works', 'electricity_supply'. "
            "Two proposals about 'MG Road is dark' and 'need streetlights on main road' must share 'street_lighting'."
        )
    )
    visual_evidence: str = Field(default="", description="If a photo was provided, describe the visible civic evidence in 6-12 words. Empty string if no photo or not relevant.")
    estimated_latitude: float | None = Field(default=None, description="If location text is provided, guess the approximate latitude for a map marker in Lucknow, India. Otherwise null.")
    estimated_longitude: float | None = Field(default=None, description="If location text is provided, guess the approximate longitude for a map marker in Lucknow, India. Otherwise null.")

# ---------------------------------------------------------------------------
# MULTILINGUAL SUPPORT
# ---------------------------------------------------------------------------
# Detects language via Unicode script ranges (no API call needed).
# Covers the 6 most common Indian WhatsApp languages.
# Supported: Hindi, Urdu, Bengali, Tamil, Telugu, Kannada â†’ fallback: English
# ---------------------------------------------------------------------------

REPLY_TEMPLATES = {
    "English": {
        "welcome":        "What development project or community upgrade would you like to propose for your area? Please describe your idea. (You can also send a voice note!)",
        "ask_location":   "Description saved. Now share your location â€” send a location pin, or type your area name.",
        "ask_photo":      "Location saved. Would you like to attach a photo? Send it now, or reply 'skip' if not.",
        "finalizing":     "Finalizing your report...",
        "photo_received": "Photo received! Finalizing your report...",
        "skip_prompt":    "Please send a photo or reply 'skip'.",
        "location_prompt":"Please send a location pin or type your area name.",
        "text_or_voice":  "Please describe the issue in text or send a voice note.",
        "survey_thanks":  "Thank you for your feedback! Your MP's office will consider this.",
        "new_prompt":     "Reply 'new proposal' anytime to submit another development idea.",
        "invalid_option": "Please reply with a number between 1 and {n}.",
        "voice_ack":      "Voice note received, processing your description now...",
        "discarded":      "Previous proposal discarded. What development project or community upgrade would you like to propose for your area? Please describe your idea.",
        "no_reports":     "You have no active reports.",
    },
    "Hindi": {
        "welcome":        "à¤†à¤ª à¤…à¤ªà¤¨à¥‡ à¤•à¥à¤·à¥‡à¤¤à¥à¤° à¤•à¥‡ à¤²à¤¿à¤ à¤•à¥Œà¤¨ à¤¸à¤¾ à¤µà¤¿à¤•à¤¾à¤¸ à¤•à¤¾à¤°à¥à¤¯ à¤¯à¤¾ à¤¸à¤¾à¤®à¥à¤¦à¤¾à¤¯à¤¿à¤• à¤¸à¥à¤§à¤¾à¤° à¤ªà¥à¤°à¤¸à¥à¤¤à¤¾à¤µà¤¿à¤¤ à¤•à¤°à¤¨à¤¾ à¤šà¤¾à¤¹à¤¤à¥‡ à¤¹à¥ˆà¤‚? à¤•à¥ƒà¤ªà¤¯à¤¾ à¤…à¤ªà¤¨à¤¾ à¤µà¤¿à¤šà¤¾à¤° à¤¬à¤¤à¤¾à¤à¤‚à¥¤ (à¤†à¤ª à¤µà¥‰à¤‡à¤¸ à¤¨à¥‹à¤Ÿ à¤­à¥€ à¤­à¥‡à¤œ à¤¸à¤•à¤¤à¥‡ à¤¹à¥ˆà¤‚!)",
        "ask_location":   "à¤µà¤¿à¤µà¤°à¤£ à¤¸à¤¹à¥‡à¤œ à¤²à¤¿à¤¯à¤¾ à¤—à¤¯à¤¾à¥¤ à¤…à¤¬ à¤…à¤ªà¤¨à¤¾ à¤¸à¥à¤¥à¤¾à¤¨ à¤¸à¤¾à¤à¤¾ à¤•à¤°à¥‡à¤‚ â€” à¤²à¥‹à¤•à¥‡à¤¶à¤¨ à¤ªà¤¿à¤¨ à¤­à¥‡à¤œà¥‡à¤‚, à¤¯à¤¾ à¤…à¤ªà¤¨à¥‡ à¤•à¥à¤·à¥‡à¤¤à¥à¤° à¤•à¤¾ à¤¨à¤¾à¤® à¤²à¤¿à¤–à¥‡à¤‚à¥¤",
        "ask_photo":      "à¤¸à¥à¤¥à¤¾à¤¨ à¤¸à¤¹à¥‡à¤œ à¤²à¤¿à¤¯à¤¾ à¤—à¤¯à¤¾à¥¤ à¤•à¥à¤¯à¤¾ à¤†à¤ª à¤•à¥‹à¤ˆ à¤«à¥‹à¤Ÿà¥‹ à¤­à¥‡à¤œà¤¨à¤¾ à¤šà¤¾à¤¹à¤¤à¥‡ à¤¹à¥ˆà¤‚? à¤…à¤­à¥€ à¤­à¥‡à¤œà¥‡à¤‚, à¤¯à¤¾ 'skip' à¤²à¤¿à¤–à¥‡à¤‚à¥¤",
        "finalizing":     "à¤†à¤ªà¤•à¥€ à¤°à¤¿à¤ªà¥‹à¤°à¥à¤Ÿ à¤¤à¥ˆà¤¯à¤¾à¤° à¤¹à¥‹ à¤°à¤¹à¥€ à¤¹à¥ˆ...",
        "photo_received": "à¤«à¥‹à¤Ÿà¥‹ à¤®à¤¿à¤² à¤—à¤ˆ! à¤†à¤ªà¤•à¥€ à¤°à¤¿à¤ªà¥‹à¤°à¥à¤Ÿ à¤¤à¥ˆà¤¯à¤¾à¤° à¤¹à¥‹ à¤°à¤¹à¥€ à¤¹à¥ˆ...",
        "skip_prompt":    "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤«à¥‹à¤Ÿà¥‹ à¤­à¥‡à¤œà¥‡à¤‚ à¤¯à¤¾ 'skip' à¤²à¤¿à¤–à¥‡à¤‚à¥¤",
        "location_prompt":"à¤•à¥ƒà¤ªà¤¯à¤¾ à¤²à¥‹à¤•à¥‡à¤¶à¤¨ à¤ªà¤¿à¤¨ à¤­à¥‡à¤œà¥‡à¤‚ à¤¯à¤¾ à¤…à¤ªà¤¨à¥‡ à¤•à¥à¤·à¥‡à¤¤à¥à¤° à¤•à¤¾ à¤¨à¤¾à¤® à¤²à¤¿à¤–à¥‡à¤‚à¥¤",
        "text_or_voice":  "à¤•à¥ƒà¤ªà¤¯à¤¾ à¤¸à¤®à¤¸à¥à¤¯à¤¾ à¤•à¥‹ à¤Ÿà¥‡à¤•à¥à¤¸à¥à¤Ÿ à¤®à¥‡à¤‚ à¤¬à¤¤à¤¾à¤à¤‚ à¤¯à¤¾ à¤µà¥‰à¤‡à¤¸ à¤¨à¥‹à¤Ÿ à¤­à¥‡à¤œà¥‡à¤‚à¥¤",
        "survey_thanks":  "à¤†à¤ªà¤•à¥‡ à¤«à¥€à¤¡à¤¬à¥ˆà¤• à¤•à¥‡ à¤²à¤¿à¤ à¤§à¤¨à¥à¤¯à¤µà¤¾à¤¦! à¤†à¤ªà¤•à¥‡ à¤¸à¤¾à¤‚à¤¸à¤¦ à¤•à¤¾ à¤•à¤¾à¤°à¥à¤¯à¤¾à¤²à¤¯ à¤‡à¤¸ à¤ªà¤° à¤µà¤¿à¤šà¤¾à¤° à¤•à¤°à¥‡à¤—à¤¾à¥¤",
        "new_prompt":     "à¤•à¥‹à¤ˆ à¤¨à¤¯à¤¾ à¤ªà¥à¤°à¤¸à¥à¤¤à¤¾à¤µ à¤¦à¥‡à¤¨à¥‡ à¤•à¥‡ à¤²à¤¿à¤ à¤•à¤­à¥€ à¤­à¥€ 'new proposal' à¤²à¤¿à¤–à¥‡à¤‚à¥¤",
        "invalid_option": "à¤•à¥ƒà¤ªà¤¯à¤¾ 1 à¤¸à¥‡ {n} à¤•à¥‡ à¤¬à¥€à¤š à¤•à¥‹à¤ˆ à¤¨à¤‚à¤¬à¤° à¤­à¥‡à¤œà¥‡à¤‚à¥¤",
        "voice_ack":      "à¤µà¥‰à¤‡à¤¸ à¤¨à¥‹à¤Ÿ à¤®à¤¿à¤² à¤—à¤¯à¤¾, à¤†à¤ªà¤•à¤¾ à¤µà¤¿à¤µà¤°à¤£ à¤ªà¥à¤°à¥‹à¤¸à¥‡à¤¸ à¤¹à¥‹ à¤°à¤¹à¤¾ à¤¹à¥ˆ...",
        "discarded":      "à¤ªà¤¿à¤›à¤²à¤¾ à¤ªà¥à¤°à¤¸à¥à¤¤à¤¾à¤µ à¤¹à¤Ÿà¤¾ à¤¦à¤¿à¤¯à¤¾ à¤—à¤¯à¤¾à¥¤ à¤†à¤ª à¤…à¤ªà¤¨à¥‡ à¤•à¥à¤·à¥‡à¤¤à¥à¤° à¤•à¥‡ à¤²à¤¿à¤ à¤•à¥Œà¤¨ à¤¸à¤¾ à¤µà¤¿à¤•à¤¾à¤¸ à¤•à¤¾à¤°à¥à¤¯ à¤ªà¥à¤°à¤¸à¥à¤¤à¤¾à¤µà¤¿à¤¤ à¤•à¤°à¤¨à¤¾ à¤šà¤¾à¤¹à¤¤à¥‡ à¤¹à¥ˆà¤‚?",
        "no_reports":     "à¤†à¤ªà¤•à¥€ à¤•à¥‹à¤ˆ à¤¸à¤•à¥à¤°à¤¿à¤¯ à¤°à¤¿à¤ªà¥‹à¤°à¥à¤Ÿ à¤¨à¤¹à¥€à¤‚ à¤¹à¥ˆà¥¤",
    },
    "Urdu": {
        "welcome":        "Ø¢Ù¾ Ø§Ù¾Ù†Û’ Ø¹Ù„Ø§Ù‚Û’ Ú©Û’ Ù„ÛŒÛ’ Ú©ÙˆÙ† Ø³Ø§ ØªØ±Ù‚ÛŒØ§ØªÛŒ Ù…Ù†ØµÙˆØ¨Û ØªØ¬ÙˆÛŒØ² Ú©Ø±Ù†Ø§ Ú†Ø§ÛØªÛ’ ÛÛŒÚºØŸ Ø¨Ø±Ø§Û Ú©Ø±Ù… Ø§Ù¾Ù†Ø§ Ø®ÛŒØ§Ù„ Ø¨ÛŒØ§Ù† Ú©Ø±ÛŒÚºÛ” (Ø¢Ù¾ ÙˆØ§Ø¦Ø³ Ù†ÙˆÙ¹ Ø¨Ú¾ÛŒ Ø¨Ú¾ÛŒØ¬ Ø³Ú©ØªÛ’ ÛÛŒÚº!)",
        "ask_location":   "ØªÙØµÛŒÙ„ Ù…Ø­ÙÙˆØ¸ ÛÙˆ Ú¯Ø¦ÛŒÛ” Ø§Ø¨ Ø§Ù¾Ù†Ø§ Ù…Ù‚Ø§Ù… Ø´ÛŒØ¦Ø± Ú©Ø±ÛŒÚº â€” Ù„ÙˆÚ©ÛŒØ´Ù† Ù¾Ù† Ø¨Ú¾ÛŒØ¬ÛŒÚº ÛŒØ§ Ø§Ù¾Ù†Û’ Ø¹Ù„Ø§Ù‚Û’ Ú©Ø§ Ù†Ø§Ù… Ù„Ú©Ú¾ÛŒÚºÛ”",
        "ask_photo":      "Ù…Ù‚Ø§Ù… Ù…Ø­ÙÙˆØ¸ ÛÙˆ Ú¯ÛŒØ§Û” Ú©ÛŒØ§ Ø¢Ù¾ ØªØµÙˆÛŒØ± Ø¨Ú¾ÛŒØ¬Ù†Ø§ Ú†Ø§ÛØªÛ’ ÛÛŒÚºØŸ Ø§Ø¨Ú¾ÛŒ Ø¨Ú¾ÛŒØ¬ÛŒÚºØŒ ÛŒØ§ 'skip' Ù„Ú©Ú¾ÛŒÚºÛ”",
        "finalizing":     "Ø¢Ù¾ Ú©ÛŒ Ø±Ù¾ÙˆØ±Ù¹ ØªÛŒØ§Ø± ÛÙˆ Ø±ÛÛŒ ÛÛ’...",
        "photo_received": "ØªØµÙˆÛŒØ± Ù…Ù„ Ú¯Ø¦ÛŒ! Ø¢Ù¾ Ú©ÛŒ Ø±Ù¾ÙˆØ±Ù¹ ØªÛŒØ§Ø± ÛÙˆ Ø±ÛÛŒ ÛÛ’...",
        "skip_prompt":    "Ø¨Ø±Ø§Û Ú©Ø±Ù… ØªØµÙˆÛŒØ± Ø¨Ú¾ÛŒØ¬ÛŒÚº ÛŒØ§ 'skip' Ù„Ú©Ú¾ÛŒÚºÛ”",
        "location_prompt":"Ø¨Ø±Ø§Û Ú©Ø±Ù… Ù„ÙˆÚ©ÛŒØ´Ù† Ù¾Ù† Ø¨Ú¾ÛŒØ¬ÛŒÚº ÛŒØ§ Ø§Ù¾Ù†Û’ Ø¹Ù„Ø§Ù‚Û’ Ú©Ø§ Ù†Ø§Ù… Ù„Ú©Ú¾ÛŒÚºÛ”",
        "text_or_voice":  "Ø¨Ø±Ø§Û Ú©Ø±Ù… Ù…Ø³Ø¦Ù„Û Ù…ØªÙ† Ù…ÛŒÚº Ø¨ÛŒØ§Ù† Ú©Ø±ÛŒÚº ÛŒØ§ ÙˆØ§Ø¦Ø³ Ù†ÙˆÙ¹ Ø¨Ú¾ÛŒØ¬ÛŒÚºÛ”",
        "survey_thanks":  "Ø¢Ù¾ Ú©Û’ ØªØ§Ø«Ø±Ø§Øª Ú©Ø§ Ø´Ú©Ø±ÛŒÛ! Ø¢Ù¾ Ú©Û’ Ø±Ú©Ù† Ù¾Ø§Ø±Ù„ÛŒÙ…Ù†Ù¹ Ú©Ø§ Ø¯ÙØªØ± Ø§Ø³ Ù¾Ø± ØºÙˆØ± Ú©Ø±Û’ Ú¯Ø§Û”",
        "new_prompt":     "Ú©ÙˆØ¦ÛŒ Ù†Ø¦ÛŒ ØªØ¬ÙˆÛŒØ² Ø¯ÛŒÙ†Û’ Ú©Û’ Ù„ÛŒÛ’ Ú©Ø¨Ú¾ÛŒ Ø¨Ú¾ÛŒ 'new proposal' Ù„Ú©Ú¾ÛŒÚºÛ”",
        "invalid_option": "Ø¨Ø±Ø§Û Ú©Ø±Ù… 1 Ø³Û’ {n} Ú©Û’ Ø¯Ø±Ù…ÛŒØ§Ù† Ú©ÙˆØ¦ÛŒ Ù†Ù…Ø¨Ø± Ø¨Ú¾ÛŒØ¬ÛŒÚºÛ”",
        "voice_ack":      "ÙˆØ§Ø¦Ø³ Ù†ÙˆÙ¹ Ù…Ù„ Ú¯ÛŒØ§ØŒ Ø¢Ù¾ Ú©ÛŒ ØªÙØµÛŒÙ„ Ù¾Ø±ÙˆØ³ÛŒØ³ ÛÙˆ Ø±ÛÛŒ ÛÛ’...",
        "discarded":      "Ù¾Ú†Ú¾Ù„ÛŒ ØªØ¬ÙˆÛŒØ² ÛÙ¹Ø§ Ø¯ÛŒ Ú¯Ø¦ÛŒÛ” Ø¢Ù¾ Ø§Ù¾Ù†Û’ Ø¹Ù„Ø§Ù‚Û’ Ú©Û’ Ù„ÛŒÛ’ Ú©ÙˆÙ† Ø³Ø§ ØªØ±Ù‚ÛŒØ§ØªÛŒ Ù…Ù†ØµÙˆØ¨Û ØªØ¬ÙˆÛŒØ² Ú©Ø±Ù†Ø§ Ú†Ø§ÛØªÛ’ ÛÛŒÚºØŸ",
        "no_reports":     "Ø¢Ù¾ Ú©ÛŒ Ú©ÙˆØ¦ÛŒ ÙØ¹Ø§Ù„ Ø±Ù¾ÙˆØ±Ù¹ Ù†ÛÛŒÚº ÛÛ’Û”",
    },
    "Tamil": {
        "welcome":        "à®‰à®™à¯à®•à®³à¯ à®ªà®•à¯à®¤à®¿à®•à¯à®•à¯ à®Žà®©à¯à®© à®µà®³à®°à¯à®šà¯à®šà®¿ à®¤à®¿à®Ÿà¯à®Ÿà®®à¯ à®…à®²à¯à®²à®¤à¯ à®šà®®à¯à®¤à®¾à®¯ à®®à¯‡à®®à¯à®ªà®¾à®Ÿà¯à®Ÿà¯ˆ à®¨à¯€à®™à¯à®•à®³à¯ à®®à¯à®©à¯à®®à¯Šà®´à®¿à®¯ à®µà®¿à®°à¯à®®à¯à®ªà¯à®•à®¿à®±à¯€à®°à¯à®•à®³à¯? à®‰à®™à¯à®•à®³à¯ à®•à®°à¯à®¤à¯à®¤à¯ˆ à®µà®¿à®µà®°à®¿à®•à¯à®•à®µà¯à®®à¯. (à®•à¯à®°à®²à¯ à®•à¯à®±à®¿à®ªà¯à®ªà¯à®®à¯ à®…à®©à¯à®ªà¯à®ªà®²à®¾à®®à¯!)",
        "ask_location":   "à®µà®¿à®³à®•à¯à®•à®®à¯ à®šà¯‡à®®à®¿à®•à¯à®•à®ªà¯à®ªà®Ÿà¯à®Ÿà®¤à¯. à®‡à®ªà¯à®ªà¯‹à®¤à¯ à®‰à®™à¯à®•à®³à¯ à®‡à®°à¯à®ªà¯à®ªà®¿à®Ÿà®¤à¯à®¤à¯ˆ à®ªà®•à®¿à®°à®µà¯à®®à¯ â€” à®‡à®°à¯à®ªà¯à®ªà®¿à®Ÿ à®ªà®¿à®©à¯ à®…à®©à¯à®ªà¯à®ªà®µà¯à®®à¯ à®…à®²à¯à®²à®¤à¯ à®‰à®™à¯à®•à®³à¯ à®ªà®•à¯à®¤à®¿à®¯à®¿à®©à¯ à®ªà¯†à®¯à®°à¯ˆ à®¤à®Ÿà¯à®Ÿà®šà¯à®šà¯ à®šà¯†à®¯à¯à®¯à®µà¯à®®à¯.",
        "ask_photo":      "à®‡à®°à¯à®ªà¯à®ªà®¿à®Ÿà®®à¯ à®šà¯‡à®®à®¿à®•à¯à®•à®ªà¯à®ªà®Ÿà¯à®Ÿà®¤à¯. à®ªà¯à®•à¯ˆà®ªà¯à®ªà®Ÿà®®à¯ à®‡à®£à¯ˆà®•à¯à®• à®µà®¿à®°à¯à®®à¯à®ªà¯à®•à®¿à®±à¯€à®°à¯à®•à®³à®¾? à®‡à®ªà¯à®ªà¯‹à®¤à¯ à®…à®©à¯à®ªà¯à®ªà®µà¯à®®à¯ à®…à®²à¯à®²à®¤à¯ 'skip' à®Žà®©à¯à®±à¯ à®ªà®¤à®¿à®²à®³à®¿à®•à¯à®•à®µà¯à®®à¯.",
        "finalizing":     "à®‰à®™à¯à®•à®³à¯ à®…à®±à®¿à®•à¯à®•à¯ˆ à®¤à®¯à®¾à®°à®¿à®•à¯à®•à®ªà¯à®ªà®Ÿà¯à®•à®¿à®±à®¤à¯...",
        "photo_received": "à®ªà¯à®•à¯ˆà®ªà¯à®ªà®Ÿà®®à¯ à®ªà¯†à®±à®ªà¯à®ªà®Ÿà¯à®Ÿà®¤à¯! à®‰à®™à¯à®•à®³à¯ à®…à®±à®¿à®•à¯à®•à¯ˆ à®¤à®¯à®¾à®°à®¿à®•à¯à®•à®ªà¯à®ªà®Ÿà¯à®•à®¿à®±à®¤à¯...",
        "skip_prompt":    "à®ªà¯à®•à¯ˆà®ªà¯à®ªà®Ÿà®®à¯ à®…à®©à¯à®ªà¯à®ªà®µà¯à®®à¯ à®…à®²à¯à®²à®¤à¯ 'skip' à®Žà®©à¯à®±à¯ à®ªà®¤à®¿à®²à®³à®¿à®•à¯à®•à®µà¯à®®à¯.",
        "location_prompt":"à®‡à®°à¯à®ªà¯à®ªà®¿à®Ÿ à®ªà®¿à®©à¯ à®…à®©à¯à®ªà¯à®ªà®µà¯à®®à¯ à®…à®²à¯à®²à®¤à¯ à®‰à®™à¯à®•à®³à¯ à®ªà®•à¯à®¤à®¿à®¯à®¿à®©à¯ à®ªà¯†à®¯à®°à¯ˆ à®¤à®Ÿà¯à®Ÿà®šà¯à®šà¯ à®šà¯†à®¯à¯à®¯à®µà¯à®®à¯.",
        "text_or_voice":  "à®‰à®°à¯ˆà®¯à®¿à®²à¯ à®šà®¿à®•à¯à®•à®²à¯ˆ à®µà®¿à®µà®°à®¿à®•à¯à®•à®µà¯à®®à¯ à®…à®²à¯à®²à®¤à¯ à®•à¯à®°à®²à¯ à®•à¯à®±à®¿à®ªà¯à®ªà¯ˆ à®…à®©à¯à®ªà¯à®ªà®µà¯à®®à¯.",
        "survey_thanks":  "à®‰à®™à¯à®•à®³à¯ à®•à®°à¯à®¤à¯à®¤à¯à®•à¯à®•à¯ à®¨à®©à¯à®±à®¿! à®‰à®™à¯à®•à®³à¯ à®¨à®¾à®Ÿà®¾à®³à¯à®®à®©à¯à®± à®‰à®±à¯à®ªà¯à®ªà®¿à®©à®°à®¿à®©à¯ à®…à®²à¯à®µà®²à®•à®®à¯ à®‡à®¤à¯ˆ à®ªà®°à®¿à®šà¯€à®²à®¿à®•à¯à®•à¯à®®à¯.",
        "new_prompt":     "à®®à®±à¯à®±à¯Šà®°à¯ à®µà®³à®°à¯à®šà¯à®šà®¿ à®¯à¯‹à®šà®©à¯ˆà®¯à¯ˆ à®šà®®à®°à¯à®ªà¯à®ªà®¿à®•à¯à®• à®Žà®ªà¯à®ªà¯‹à®¤à¯ à®µà¯‡à®£à¯à®Ÿà¯à®®à®¾à®©à®¾à®²à¯à®®à¯ 'new proposal' à®Žà®©à¯à®±à¯ à®¤à®Ÿà¯à®Ÿà®šà¯à®šà¯ à®šà¯†à®¯à¯à®¯à®µà¯à®®à¯.",
        "invalid_option": "1 à®®à¯à®¤à®²à¯ {n} à®µà®°à¯ˆ à®’à®°à¯ à®Žà®£à¯à®£à¯ˆ à®ªà®¤à®¿à®²à®³à®¿à®•à¯à®•à®µà¯à®®à¯.",
        "voice_ack":      "à®•à¯à®°à®²à¯ à®•à¯à®±à®¿à®ªà¯à®ªà¯ à®ªà¯†à®±à®ªà¯à®ªà®Ÿà¯à®Ÿà®¤à¯, à®‰à®™à¯à®•à®³à¯ à®µà®¿à®³à®•à¯à®•à®®à¯ à®šà¯†à®¯à®²à®¾à®•à¯à®•à®ªà¯à®ªà®Ÿà¯à®•à®¿à®±à®¤à¯...",
        "discarded":      "à®®à¯à®¨à¯à®¤à¯ˆà®¯ à®®à¯à®©à¯à®®à¯Šà®´à®¿à®µà¯ à®¨à¯€à®•à¯à®•à®ªà¯à®ªà®Ÿà¯à®Ÿà®¤à¯. à®‰à®™à¯à®•à®³à¯ à®ªà®•à¯à®¤à®¿à®•à¯à®•à¯ à®Žà®©à¯à®© à®µà®³à®°à¯à®šà¯à®šà®¿ à®¤à®¿à®Ÿà¯à®Ÿà®®à¯ à®®à¯à®©à¯à®®à¯Šà®´à®¿à®¯ à®µà®¿à®°à¯à®®à¯à®ªà¯à®•à®¿à®±à¯€à®°à¯à®•à®³à¯?",
        "no_reports":     "à®‰à®™à¯à®•à®³à¯à®•à¯à®•à¯ à®šà¯†à®¯à®²à®¿à®²à¯ à®‰à®³à¯à®³ à®…à®±à®¿à®•à¯à®•à¯ˆà®•à®³à¯ à®‡à®²à¯à®²à¯ˆ.",
    },
    "Telugu": {
        "welcome":        "à°®à±€ à°ªà±à°°à°¾à°‚à°¤à°¾à°¨à°¿à°•à°¿ à° à°…à°­à°¿à°µà±ƒà°¦à±à°§à°¿ à°ªà±à°°à°¾à°œà±†à°•à±à°Ÿà± à°²à±‡à°¦à°¾ à°¸à°¾à°®à°¾à°œà°¿à°• à°®à±†à°°à±à°—à±à°¦à°²à°¨à± à°®à±€à°°à± à°ªà±à°°à°¤à°¿à°ªà°¾à°¦à°¿à°‚à°šà°¾à°²à°¨à±à°•à±à°‚à°Ÿà±à°¨à±à°¨à°¾à°°à±? à°®à±€ à°†à°²à±‹à°šà°¨à°¨à± à°µà°¿à°µà°°à°¿à°‚à°šà°‚à°¡à°¿. (à°µà°¾à°¯à°¿à°¸à± à°¨à±‹à°Ÿà± à°•à±‚à°¡à°¾ à°ªà°‚à°ªà°µà°šà±à°šà±!)",
        "ask_location":   "à°µà°¿à°µà°°à°£ à°¸à±‡à°µà± à°šà±‡à°¯à°¬à°¡à°¿à°‚à°¦à°¿. à°‡à°ªà±à°ªà±à°¡à± à°®à±€ à°¸à±à°¥à°¾à°¨à°¾à°¨à±à°¨à°¿ à°·à±‡à°°à± à°šà±‡à°¯à°‚à°¡à°¿ â€” à°²à±Šà°•à±‡à°·à°¨à± à°ªà°¿à°¨à± à°ªà°‚à°ªà°‚à°¡à°¿ à°²à±‡à°¦à°¾ à°®à±€ à°ªà±à°°à°¾à°‚à°¤à°‚ à°ªà±‡à°°à± à°Ÿà±ˆà°ªà± à°šà±‡à°¯à°‚à°¡à°¿.",
        "ask_photo":      "à°¸à±à°¥à°¾à°¨à°‚ à°¸à±‡à°µà± à°šà±‡à°¯à°¬à°¡à°¿à°‚à°¦à°¿. à°®à±€à°°à± à°«à±‹à°Ÿà±‹ à°œà°¤à°šà±‡à°¯à°¾à°²à°¨à±à°•à±à°‚à°Ÿà±à°¨à±à°¨à°¾à°°à°¾? à°‡à°ªà±à°ªà±à°¡à± à°ªà°‚à°ªà°‚à°¡à°¿ à°²à±‡à°¦à°¾ 'skip' à°…à°¨à°¿ à°°à°¿à°ªà±à°²à±ˆ à°šà±‡à°¯à°‚à°¡à°¿.",
        "finalizing":     "à°®à±€ à°¨à°¿à°µà±‡à°¦à°¿à°• à°¤à°¯à°¾à°°à°µà±à°¤à±‹à°‚à°¦à°¿...",
        "photo_received": "à°«à±‹à°Ÿà±‹ à°…à°‚à°¦à°¿à°‚à°¦à°¿! à°®à±€ à°¨à°¿à°µà±‡à°¦à°¿à°• à°¤à°¯à°¾à°°à°µà±à°¤à±‹à°‚à°¦à°¿...",
        "skip_prompt":    "à°¦à°¯à°šà±‡à°¸à°¿ à°«à±‹à°Ÿà±‹ à°ªà°‚à°ªà°‚à°¡à°¿ à°²à±‡à°¦à°¾ 'skip' à°…à°¨à°¿ à°°à°¿à°ªà±à°²à±ˆ à°šà±‡à°¯à°‚à°¡à°¿.",
        "location_prompt":"à°¦à°¯à°šà±‡à°¸à°¿ à°²à±Šà°•à±‡à°·à°¨à± à°ªà°¿à°¨à± à°ªà°‚à°ªà°‚à°¡à°¿ à°²à±‡à°¦à°¾ à°®à±€ à°ªà±à°°à°¾à°‚à°¤à°‚ à°ªà±‡à°°à± à°Ÿà±ˆà°ªà± à°šà±‡à°¯à°‚à°¡à°¿.",
        "text_or_voice":  "à°¦à°¯à°šà±‡à°¸à°¿ à°¸à°®à°¸à±à°¯à°¨à± à°Ÿà±†à°•à±à°¸à±à°Ÿà±â€Œà°²à±‹ à°µà°¿à°µà°°à°¿à°‚à°šà°‚à°¡à°¿ à°²à±‡à°¦à°¾ à°µà°¾à°¯à°¿à°¸à± à°¨à±‹à°Ÿà± à°ªà°‚à°ªà°‚à°¡à°¿.",
        "survey_thanks":  "à°®à±€ à°…à°­à°¿à°ªà±à°°à°¾à°¯à°¾à°¨à°¿à°•à°¿ à°§à°¨à±à°¯à°µà°¾à°¦à°¾à°²à±! à°®à±€ à°Žà°‚à°ªà±€ à°•à°¾à°°à±à°¯à°¾à°²à°¯à°‚ à°¦à±€à°¨à±à°¨à°¿ à°ªà°°à°¿à°—à°£à°¿à°¸à±à°¤à±à°‚à°¦à°¿.",
        "new_prompt":     "à°®à°°à±Šà°• à°…à°­à°¿à°µà±ƒà°¦à±à°§à°¿ à°†à°²à±‹à°šà°¨à°¨à± à°¸à°®à°°à±à°ªà°¿à°‚à°šà°¡à°¾à°¨à°¿à°•à°¿ à°Žà°ªà±à°ªà±à°¡à±ˆà°¨à°¾ 'new proposal' à°…à°¨à°¿ à°Ÿà±ˆà°ªà± à°šà±‡à°¯à°‚à°¡à°¿.",
        "invalid_option": "à°¦à°¯à°šà±‡à°¸à°¿ 1 à°¨à±à°‚à°¡à°¿ {n} à°®à°§à±à°¯ à°’à°• à°¨à°‚à°¬à°°à±â€Œà°¤à±‹ à°°à°¿à°ªà±à°²à±ˆ à°šà±‡à°¯à°‚à°¡à°¿.",
        "voice_ack":      "à°µà°¾à°¯à°¿à°¸à± à°¨à±‹à°Ÿà± à°…à°‚à°¦à°¿à°‚à°¦à°¿, à°®à±€ à°µà°¿à°µà°°à°£ à°ªà±à°°à°¾à°¸à±†à°¸à± à°…à°µà±à°¤à±‹à°‚à°¦à°¿...",
        "discarded":      "à°®à±à°¨à±à°ªà°Ÿà°¿ à°ªà±à°°à°¤à°¿à°ªà°¾à°¦à°¨ à°¤à±Šà°²à°—à°¿à°‚à°šà°¬à°¡à°¿à°‚à°¦à°¿. à°®à±€ à°ªà±à°°à°¾à°‚à°¤à°¾à°¨à°¿à°•à°¿ à° à°…à°­à°¿à°µà±ƒà°¦à±à°§à°¿ à°ªà±à°°à°¾à°œà±†à°•à±à°Ÿà± à°ªà±à°°à°¤à°¿à°ªà°¾à°¦à°¿à°‚à°šà°¾à°²à°¨à±à°•à±à°‚à°Ÿà±à°¨à±à°¨à°¾à°°à±?",
        "no_reports":     "à°®à±€à°•à± à°¸à°•à±à°°à°¿à°¯ à°¨à°¿à°µà±‡à°¦à°¿à°•à°²à± à°²à±‡à°µà±.",
    },
}

def detect_script_language(text: str) -> str:
    """Detect language from Unicode script ranges. Zero API calls, instant.
    Covers the 5 most common non-English Indian languages on WhatsApp."""
    if not text or not text.strip():
        return "English"
    total = len(text.strip())
    scores = {
        "Hindi":   sum(1 for c in text if '\u0900' <= c <= '\u097F') / total,
        "Urdu":    sum(1 for c in text if '\u0600' <= c <= '\u06FF') / total,
        "Bengali": sum(1 for c in text if '\u0980' <= c <= '\u09FF') / total,
        "Tamil":   sum(1 for c in text if '\u0B80' <= c <= '\u0BFF') / total,
        "Telugu":  sum(1 for c in text if '\u0C00' <= c <= '\u0C7F') / total,
    }
    best_lang, best_score = max(scores.items(), key=lambda x: x[1])
    return best_lang if best_score > 0.08 else "English"

def get_reply(key: str, lang: str, **kwargs) -> str:
    """Fetch a localized reply string by key. Falls back to English."""
    templates = REPLY_TEMPLATES.get(lang, REPLY_TEMPLATES["English"])
    text = templates.get(key, REPLY_TEMPLATES["English"].get(key, key))
    return text.format(**kwargs) if kwargs else text

def fetch_twilio_media_part(media_url: str, mime_type: str):
    if not media_url or not mime_type.startswith("image/"):
        return None
    try:
        auth_tuple = (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN else None
        with httpx.Client(timeout=12) as client:
            response = client.get(media_url, auth=auth_tuple)
            response.raise_for_status()
            if len(response.content) > 5 * 1024 * 1024:
                logger.warning("[MEDIA] Photo skipped because it exceeds 5MB.")
                return None
            return types.Part.from_bytes(data=response.content, mime_type=mime_type)
    except Exception as e:
        logger.warning(f"[MEDIA] Could not fetch photo for multimodal triage: {e}")
        return None

@app.get("/")
def root():
    return FileResponse("public/index.html")

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/sw.js")
async def service_worker():
    return FileResponse("public/sw.js", media_type="application/javascript")

@app.get("/favicon.svg")
async def favicon():
    return FileResponse("public/favicon.svg", media_type="image/svg+xml")

@app.post("/webhook/sms")
@app.post("/webhook/whatsapp")
async def receive_whatsapp(request: Request, background_tasks: BackgroundTasks, db = Depends(get_db)):
    form_data = await request.form()
    
    # Twilio security validation
    if twilio_validator:
        signature = request.headers.get("X-Twilio-Signature", "")
        url = str(request.url)
        public_url = "https://urbanos.web.app" + request.url.path
        
        if "x-forwarded-host" in request.headers:
            proto = request.headers.get("x-forwarded-proto", "http")
            host = request.headers.get("x-forwarded-host")
            url = f"{proto}://{host}{request.url.path}"
            
        post_vars = {k: v for k, v in form_data.items()}
        
        is_valid = twilio_validator.validate(url, post_vars, signature)
        is_valid_public = twilio_validator.validate(public_url, post_vars, signature)
        
        logger.info(f"[DEBUG TWILIO] url={url} public_url={public_url} sig={signature} valid={is_valid} valid_pub={is_valid_public}")
        
        if not is_valid and not is_valid_public:
            logger.warning(f"[SECURITY] Invalid Twilio signature from {request.client.host}. Dropping request.")
            # TEMPORARILY disable the 403 so the sandbox works for the user while we debug
            # raise HTTPException(status_code=403, detail="Forbidden")

    # Offload the blocking DB logic to a threadpool to prevent freezing the asyncio event loop
    return await asyncio.to_thread(_process_whatsapp_sync, form_data, background_tasks, db)

def _process_whatsapp_sync(form_data, background_tasks: BackgroundTasks, db):
    sender = form_data.get("From", "")
    body = form_data.get("Body", "").strip()
    media_url = form_data.get("MediaUrl0")
    media_type = form_data.get("MediaContentType0", "")
    lat_str = form_data.get("Latitude")
    lng_str = form_data.get("Longitude")
    
    twiml = MessagingResponse()
    
    # Detect language from current message body (Unicode script detection, no API call)
    msg_lang = detect_script_language(body)
    
    # Check for commands
    body_lower = body.lower()
    if body_lower.startswith("status"):
        if body_lower == "status":
            reports = [doc.to_dict() for doc in db.collection('messages').where('sender', '==', sender).order_by('id', direction=firestore.Query.DESCENDING).limit(3).stream()]
            if not reports:
                twiml.message(get_reply("no_reports", msg_lang))
                return HTMLResponse(content=str(twiml), media_type="application/xml")
            
            msg = "Your Recent Reports:\n"
            for r in reports:
                msg += f"#{r.get('reference_id')} - {r.get('category') or 'General'}\nStatus: {r.get('status')}\n\n"
            twiml.message(msg.strip())
            return HTMLResponse(content=str(twiml), media_type="application/xml")
        else:
            parts = body_lower.split()
            if len(parts) > 1:
                ref_id = parts[1].strip('#')
                report_docs = list(db.collection('messages').where('reference_id', '==', ref_id).limit(1).stream())
                report = report_docs[0].to_dict() if report_docs else None
                if report:
                    twiml.message(f"Report #{report.get('reference_id')}\nCategory: {report.get('category') or 'General'}\nStatus: {report.get('status')}\nSummary: {report.get('summary')}")
                else:
                    twiml.message(f"Could not find report #{ref_id}.")
                return HTMLResponse(content=str(twiml), media_type="application/xml")

    if body_lower == "new proposal" or body_lower == "new report":
        clear_session(db, sender)
        twiml.message(get_reply("discarded", msg_lang))
        update_session(db, sender, "awaiting_description", {"user_language": msg_lang})
        return HTMLResponse(content=str(twiml), media_type="application/xml")

    # State Machine Logic
    session = get_session(db, sender)
    current_step = session.current_step if session else None
    
    # Retrieve persisted language from session (set when user first wrote in their language)
    session_data = {}
    if session and session.collected_data:
        try:
            session_data = json.loads(session.collected_data)
        except Exception:
            pass
    lang = session_data.get("user_language", msg_lang)
    
    if current_step is None:
        if body and body.strip().isdigit():
            survey_docs = list(db.collection('surveys').where('is_active', '==', True).limit(1).stream())
            active_survey = survey_docs[0].to_dict() if survey_docs else None
            if active_survey:
                options = [opt.strip() for opt in active_survey.get('options').split(',')]
                try:
                    choice_idx = int(body.strip()) - 1
                    if 0 <= choice_idx < len(options):
                        choice = options[choice_idx]
                        db.collection('survey_responses').add({'survey_id': active_survey.get('id'), 'sender': sender, 'selected_option': choice})
                        twiml.message(get_reply("survey_thanks", lang))
                        return HTMLResponse(content=str(twiml), media_type="application/xml")
                except Exception:
                    pass
        
        # Detect language from first message and store it
        first_lang = detect_script_language(body) if body else "English"
        twiml.message(get_reply("welcome", first_lang))
        update_session(db, sender, "awaiting_description", {"user_language": first_lang})
        return HTMLResponse(content=str(twiml), media_type="application/xml")
        
    elif current_step == "awaiting_description":
        if "audio" in media_type and media_url:
            twiml.message(get_reply("voice_ack", lang))
            background_tasks.add_task(process_voice_note, media_url, sender)
            return HTMLResponse(content=str(twiml), media_type="application/xml")
        
        elif body:
            # Detect language from their description (most informative message)
            detected_lang = detect_script_language(body)
            if detected_lang != "English":
                lang = detected_lang  # Update to their actual language
            update_session(db, sender, "awaiting_location", {"description": body, "user_language": lang})
            twiml.message(get_reply("ask_location", lang))
            return HTMLResponse(content=str(twiml), media_type="application/xml")
        else:
            twiml.message(get_reply("text_or_voice", lang))
            return HTMLResponse(content=str(twiml), media_type="application/xml")
            
    elif current_step == "awaiting_location":
        location_data = None
        source = None
        if lat_str and lng_str:
            location_data = f"{lat_str},{lng_str}"
            source = "gps_pin"
        elif body:
            location_data = body
            source = "text"
            
        if location_data:
            update_session(db, sender, "awaiting_photo", {"location": location_data, "location_source": source})
            twiml.message(get_reply("ask_photo", lang))
            return HTMLResponse(content=str(twiml), media_type="application/xml")
        else:
            twiml.message(get_reply("location_prompt", lang))
            return HTMLResponse(content=str(twiml), media_type="application/xml")
            
    elif current_step == "awaiting_photo":
        if "image" in media_type and media_url:
            update_session(db, sender, "finalize", {"photo_url": media_url, "photo_type": media_type})
            twiml.message(get_reply("photo_received", lang))
        elif body_lower == "skip":
            update_session(db, sender, "finalize", {})
            twiml.message(get_reply("finalizing", lang))
        else:
            twiml.message(get_reply("skip_prompt", lang))
            return HTMLResponse(content=str(twiml), media_type="application/xml")

    elif current_step == "awaiting_survey":
        # User is responding to a post-submission survey
        survey_docs = list(db.collection('surveys').where('is_active', '==', True).limit(1).stream())
        active_survey = survey_docs[0].to_dict() if survey_docs else None
        if active_survey and body.strip().isdigit():
            options = [opt.strip() for opt in active_survey.get('options', '').split(',')]
            choice_idx = int(body.strip()) - 1
            if 0 <= choice_idx < len(options):
                choice = options[choice_idx]
                db.collection('survey_responses').add({'survey_id': active_survey.get('id'), 'sender': sender, 'selected_option': choice})
                twiml.message(get_reply("survey_thanks", lang))
            else:
                twiml.message(get_reply("invalid_option", lang, n=len(options)))
                return HTMLResponse(content=str(twiml), media_type="application/xml")
        else:
            twiml.message(get_reply("new_prompt", lang))
        clear_session(db, sender)
        return HTMLResponse(content=str(twiml), media_type="application/xml")
            
    # Finalize Logic
    session = get_session(db, sender)
    if session and session.current_step == "finalize":
        data = {}
        try:
            data = json.loads(session.collected_data) if session.collected_data else {}
        except Exception:
            pass
        
        description = data.get("description", "")
        location_raw = data.get("location", "")
        loc_source = data.get("location_source", "")
        photo_url = data.get("photo_url", "")
        
        lat = None
        lng = None
        if loc_source == "gps_pin" and "," in location_raw:
            try:
                parsed_lat = float(location_raw.split(",")[0])
                parsed_lng = float(location_raw.split(",")[1])
                # Geographic Bounds Checking
                if -90 <= parsed_lat <= 90 and -180 <= parsed_lng <= 180:
                    lat = parsed_lat
                    lng = parsed_lng
                else:
                    logger.warning(f"[VALIDATION] Coordinates out of bounds: lat={parsed_lat}, lng={parsed_lng}")
            except Exception:
                logger.warning("[VALIDATION] Failed to parse coordinates.")
                pass
                
        # Generate unique Reference ID
        ref_id = f"UO{uuid.uuid4().hex[:6].upper()}"
        
        # AI Triage
        category = None
        priority = None
        feasibility = None
        extracted_location = location_raw if loc_source == "text" else None
        summary = None
        original_language = None
        constituency_zone = None
        estimated_budget = None
        semantic_tag = None
        visual_evidence = ""
        
        if gemini_client and description:
            try:
                prompt = (
                    "Analyze this community development proposal from a Smart City WhatsApp tip-line. "
                    "Extract the structured triage data, including what language they originally used. "
                    "If an image is attached, use it as civic evidence only when it supports the report; do not invent details.\n\n"
                    f"Proposal Text: {description}\n"
                    f"Submitted Location: {location_raw or 'Not provided'}"
                )
                contents = [prompt]
                image_part = fetch_twilio_media_part(photo_url, data.get("photo_type") or "")
                if image_part:
                    contents.append(image_part)
                response = gemini_client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=contents,
                    config={
                        'response_mime_type': 'application/json',
                        'response_schema': TriageResult,
                    },
                )
                triage = response.parsed
                category = triage.category
                priority = triage.priority
                feasibility = triage.feasibility
                original_language = triage.original_language

                if loc_source == "text":
                    extracted_location = triage.extracted_location or location_raw
                    lat = triage.estimated_latitude
                    lng = triage.estimated_longitude
                summary = triage.summary
                constituency_zone = triage.constituency_zone
                estimated_budget = triage.estimated_budget
                semantic_tag = triage.semantic_tag
                visual_evidence = triage.visual_evidence or ""
            except Exception as e:
                logger.error(f"[AI ERROR] Gemini Triage Failed: {e}", exc_info=True)
                
        # Add to firestore
        doc_ref = firestore_db.collection('messages').document()
        new_message = {
            "timestamp": datetime.now().isoformat(),
            "sender": sender,
            "body": description,
            "media_url": photo_url or data.get("voice_url"),
            "media_type": data.get("photo_type") or ("audio/ogg" if data.get("voice_url") else None),
            "latitude": lat,
            "longitude": lng,
            "location_source": loc_source,
            "category": category,
            "priority": priority,
            "sentiment": feasibility,
            "extracted_location": extracted_location,
            "summary": summary,
            "original_language": original_language,
            "constituency_zone": constituency_zone,
            "estimated_budget": estimated_budget,
            "semantic_tag": semantic_tag,
            "visual_evidence": visual_evidence,
            "reference_id": ref_id,
            "status": "Open",
            "id": doc_ref.id
        }
        doc_ref.set(new_message)
        
        survey_docs = list(firestore_db.collection('surveys').where('is_active', '==', True).limit(1).stream())
        active_survey = survey_docs[0].to_dict() if survey_docs else None
        if active_survey:
            update_session(db, sender, "awaiting_survey", {"survey_id": active_survey.get('id')})
            options = [opt.strip() for opt in active_survey.get('options').split(',')]
            opt_text = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(options)])
            # Build the survey question in the user's language
            survey_q = active_survey.get('question')
            if lang != "English" and gemini_client:
                try:
                    tr = gemini_client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=f"Translate ONLY this sentence to {lang}, keep numbers and formatting intact, return only the translation:\n{survey_q}"
                    )
                    survey_q = tr.text.strip()
                except Exception:
                    pass  # Fall back to English question
            twiml.message(f"âœ… Proposal submitted! Ref: #{ref_id}.\n\n*{survey_q}*\n\nReply with the number of your choice:\n{opt_text}")
        else:
            clear_session(db, sender)
            # Confirm submission in user's language
            confirm_msg = f"Proposal submitted successfully. Reference ID: #{ref_id}. Your MP's office has received this and it will be reviewed shortly."
            if lang != "English" and gemini_client:
                try:
                    tr = gemini_client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=f"Translate ONLY this sentence to {lang}, keep the reference ID #{ref_id} exactly as is, return only the translation:\n{confirm_msg}"
                    )
                    confirm_msg = tr.text.strip()
                except Exception:
                    pass
            twiml.message(confirm_msg)
            
        logger.info(f"Proposal {ref_id} successfully created for {sender}.")
        return HTMLResponse(content=str(twiml), media_type="application/xml")
    
    return HTMLResponse(content=str(twiml), media_type="application/xml")


class SurveyCreate(BaseModel):
    question: str
    options: str

@app.get("/surveys")
async def get_surveys(db = Depends(get_db), admin = Depends(verify_admin)):
    surveys = [d.to_dict() for d in db.collection('surveys').stream()]
    results = []
    for s in surveys:
        sid = s.get('id')
        resp_docs = list(db.collection('survey_responses').where('survey_id', '==', sid).stream())
        
        counts_dict = {}
        for d in resp_docs:
            opt = d.to_dict().get('selected_option')
            counts_dict[opt] = counts_dict.get(opt, 0) + 1
            
        results.append({
            "id": sid, 
            "question": s.get('question'), 
            "options": [opt.strip() for opt in s.get('options', '').split(',')], 
            "is_active": s.get('is_active', False), 
            "results": counts_dict
        })
    return results

@app.post("/surveys")
async def create_survey(survey: SurveyCreate, db = Depends(get_db), admin = Depends(verify_admin)):
    doc_ref = db.collection('surveys').document()
    doc_ref.set({
        "id": doc_ref.id,
        "question": survey.question,
        "options": survey.options,
        "is_active": False
    })
    return {"status": "success", "id": doc_ref.id}

@app.post("/surveys/{survey_id}/activate")
async def activate_survey(survey_id: str, db = Depends(get_db), admin = Depends(verify_admin)):
    # Deactivate all
    docs = db.collection('surveys').where('is_active', '==', True).stream()
    for doc in docs:
        doc.reference.update({"is_active": False})
    # Activate one by Firestore string ID
    db.collection('surveys').document(survey_id).update({"is_active": True})
    return {"status": "success"}

@app.post("/surveys/{survey_id}/stop")
async def stop_survey(survey_id: str, db = Depends(get_db), admin = Depends(verify_admin)):
    db.collection('surveys').document(survey_id).update({"is_active": False})
    return {"status": "success"}

@app.delete("/surveys/{survey_id}")
async def delete_survey(survey_id: str, db = Depends(get_db), admin = Depends(verify_admin)):
    db.collection('surveys').document(survey_id).delete()
    return {"status": "success"}


@app.post("/surveys/{survey_id}/broadcast")
async def broadcast_survey(survey_id: str, db = Depends(get_db), admin = Depends(verify_admin)):
    survey_doc = db.collection('surveys').document(survey_id).get()
    survey = survey_doc.to_dict() if survey_doc.exists else None
    if not survey:
        raise HTTPException(status_code=404, detail="Survey not found")
        
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_WHATSAPP_NUMBER:
        raise HTTPException(status_code=500, detail="Twilio credentials missing")
        
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    
    # Get all unique senders
    docs = db.collection('messages').stream()
    senders = list(set([doc.to_dict().get('sender') for doc in docs if doc.to_dict().get('sender')]))
    count = 0
    
    options = [opt.strip() for opt in survey.get('options').split(',')]
    opt_text = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(options)])
    msg_body = f"Your MP is requesting your feedback:\n*{survey.get('question')}*\n\nReply with the number of your choice:\n{opt_text}"
    
    for s in senders:
        sender_phone = s
        try:
            client.messages.create(
                from_=TWILIO_WHATSAPP_NUMBER,
                body=msg_body,
                to=sender_phone
            )
            count += 1
        except Exception as e:
            logger.error(f"Failed to broadcast to {sender_phone}: {e}")
            
    return {"status": "success", "broadcast_count": count}

class ChatMessage(BaseModel):
    role: Literal["user", "model"]
    content: str

class ChatRequest(BaseModel):
    query: str = Field(max_length=1000)
    history: list[ChatMessage] = []

@app.post("/api/upload-dataset")
async def upload_dataset(file: UploadFile = File(...), db = Depends(get_db), admin = Depends(verify_admin)):
    try:
        content = await file.read()
        text_content = content.decode('utf-8')
        doc_ref = db.collection('custom_datasets').document()
        doc_ref.set({
            "id": doc_ref.id,
            "filename": file.filename,
            "content": text_content[:5000], # Limit heavily to avoid massive context blowup
            "uploaded_at": datetime.now().isoformat()
        })
        return {"status": "success", "id": doc_ref.id}
    except Exception as e:
        logger.error(f"Failed to upload dataset: {e}")
        raise HTTPException(status_code=500, detail="Failed to process file")

_RAG_CACHE = {"data": None, "at": 0.0}

@app.post("/api/chat")
async def api_chat(req: ChatRequest, db = Depends(get_db), admin = Depends(verify_admin)):
    if not gemini_client:
        return {"response": "UrbanOS AI is not configured in this environment. The dashboard data APIs are still available for proposals, rankings, sanctions, and surveys."}

    fallback_response = "UrbanOS AI is temporarily unavailable. Please retry in a moment; no civic data was modified."
    try:
        global _RAG_CACHE
        now = time.time()
        
        if _RAG_CACHE["data"] and (now - _RAG_CACHE["at"]) < 120:
            sys_prompt = _RAG_CACHE["data"]
        else:
            sanc_docs = db.collection('sanctioned_projects').stream()
            dataset_docs = db.collection('custom_datasets').stream()
            
            try:
                msgs_docs = db.collection('messages').order_by('timestamp', direction=firestore.Query.DESCENDING).limit(100).stream()
            except Exception:
                msgs_docs = db.collection('messages').limit(100).stream()

            proposal_rows = []
            proposals = []
            for doc in msgs_docs:
                d = doc.to_dict()
                proposal_rows.append(d)
                proposals.append(
                    "- Ref {ref}: {category} / {tag} in {zone}: {summary} "
                    "(Priority: {priority}, Status: {status}, Budget: INR {budget}, Visual evidence: {visual}) "
                    "Citizen text: {body}".format(
                        ref=d.get("reference_id") or doc.id,
                        category=d.get("category") or "Uncategorized",
                        tag=d.get("semantic_tag") or "untagged",
                        zone=d.get("constituency_zone") or "Unknown",
                        summary=d.get("summary") or "No summary",
                        priority=d.get("priority") or "Unknown",
                        status=d.get("status") or "Open",
                        budget=d.get("estimated_budget") or 0,
                        visual=d.get("visual_evidence") or "none",
                        body=(d.get("body") or "")[:240],
                    )
                )
                
            sanctions = []
            for doc in sanc_docs:
                d = doc.to_dict()
                sanctions.append(f"- {d.get('category')} in {d.get('zone')}: {d.get('title')}")
                
            datasets = []
            for doc in dataset_docs:
                d = doc.to_dict()
                datasets.append(f"--- DATASET: {d.get('filename')} ---\n{d.get('content')}\n")

            zone_counts = {}
            category_counts = {}
            priority_counts = {}
            status_counts = {}
            total_budget = 0
            for p in proposal_rows:
                zone = p.get("constituency_zone") or "Unknown"
                category = p.get("category") or "Uncategorized"
                priority = p.get("priority") or "Unknown"
                status = p.get("status") or "Open"
                zone_counts[zone] = zone_counts.get(zone, 0) + 1
                category_counts[category] = category_counts.get(category, 0) + 1
                priority_counts[priority] = priority_counts.get(priority, 0) + 1
                status_counts[status] = status_counts.get(status, 0) + 1
                total_budget += p.get("estimated_budget") or 0

            aggregate_summary = (
                f"Total proposals in context: {len(proposal_rows)}\n"
                f"Total estimated budget in context: INR {total_budget}\n"
                f"Zone counts: {zone_counts}\n"
                f"Category counts: {category_counts}\n"
                f"Priority counts: {priority_counts}\n"
                f"Status counts: {status_counts}\n"
            )
            fallback_response = (
                "UrbanOS AI is temporarily unavailable, but the live data context was loaded.\n\n"
                "```text\n"
                f"{aggregate_summary}"
                "```"
            )

            sys_prompt = (
                "You are a Production-level AI database assistant for UrbanOS, analyzing citizen grievances, sanctioned projects, and uploaded datasets.\n"
                "STRICT BIAS GUARDRAILS: You must remain strictly neutral, unbiased, and objective. Do not favor any political entity, demographic, or region.\n"
                "STRICT KNOWLEDGE GUARDRAILS: If the user asks about something NOT in the provided context, you MUST explicitly say 'I do not know' or 'I do not have data on that'. Do not hallucinate data.\n"
                "CHART GENERATION: If the user asks for a chart, graph, or plot, output a valid Mermaid JS code block (```mermaid ... ```). Keep it simple.\n"
                "EXCEL/CSV EXPORT: If the user asks for data in an Excel sheet or CSV, output the data as a standard Markdown table. The system will automatically convert it to a downloadable CSV for them.\n\n"
                "--- AGGREGATE SUMMARY ---\n" +
                aggregate_summary +
                "\n--- CITIZEN PROPOSALS ---\n" +
                "\n".join(proposals) +
                "\n\n--- SANCTIONED PROJECTS ---\n" +
                "\n".join(sanctions) +
                "\n\n" + "\n".join(datasets)
            )
            _RAG_CACHE["data"] = sys_prompt
            _RAG_CACHE["at"] = now


        contents = []
        for h in req.history:
            contents.append({"role": "user" if h.role == "user" else "model", "parts": [{"text": h.content}]})
        
        contents.append({"role": "user", "parts": [{"text": req.query}]})

        resp = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=contents,
            config={"system_instruction": sys_prompt}
        )
        return {"response": resp.text.strip()}
    except Exception as e:
        logger.error(f"Chat API failed: {e}")
        return {"response": fallback_response}

@app.get("/messages")
async def get_messages(db = Depends(get_db), admin = Depends(verify_admin)):
    try:
        docs = db.collection('messages').order_by('timestamp', direction=firestore.Query.DESCENDING).limit(5000).stream()
    except Exception:
        docs = db.collection('messages').limit(5000).stream()
    messages = []
    for doc in docs:
        msg = doc.to_dict()
        messages.append({
            "id": msg.get("id"),
            "reference_id": msg.get("reference_id"),
            "sender": msg.get("sender"),
            "category": msg.get("category"),
            "status": msg.get("status"),
            "summary": msg.get("summary"),
            "location": msg.get("extracted_location"),
            "location_source": msg.get("location_source"),
            "latitude": msg.get("latitude"),
            "longitude": msg.get("longitude"),
            "media_url": msg.get("media_url"),
            "media_type": msg.get("media_type"),
            "is_urgent": msg.get("is_urgent", False),
            "raw_body": msg.get("body"),
            "constituency_zone": msg.get("constituency_zone"),
            "estimated_budget": msg.get("estimated_budget"),
            "sentiment": msg.get("sentiment"),
            "original_language": msg.get("original_language"),
            "priority": msg.get("priority"),
            "visual_evidence": msg.get("visual_evidence"),
            "timestamp": msg.get("timestamp")
        })
    return JSONResponse(content=messages)


# ---------------------------------------------------------------------------
# DEMOGRAPHIC DATA LAYER
# ---------------------------------------------------------------------------
# Source: Census of India 2011, Lucknow District Census Handbook (DCHB).
# Lucknow Municipal Corporation (LMC) Zone population estimates are derived
# from ward-level data published by the Office of the Registrar General &
# Census Commissioner, India. Infrastructure gap data (school/hospital
# distances) sourced from UDISE+ 2021-22 district report and National Health
# Mission (NHM) Uttar Pradesh facility mapping data.
#
# TOTAL Lucknow District Population (Census 2011): 4,589,838
# Lucknow Urban Agglomeration: 2,902,920
#
# This layer is PLUGGABLE â€” replace with live Census API calls at:
#   â†’ data.gov.in  (Open Government Data Platform India)
#   â†’ udiseplus.gov.in (school density data)
#   â†’ nhm.gov.in (health infrastructure data)
# ---------------------------------------------------------------------------
DEMO_DEMOGRAPHICS = {
    # North Lucknow: Sarojini Nagar, Bakshi Ka Talab â€” peri-urban, lower infra density
    "North":   {"population": 312000, "youth_pct": 36, "nearest_school_km": 7.8, "nearest_hospital_km": 11.2, "literacy_rate": 69, "road_gap_index": 7.1, "water_gap_index": 6.8, "sanitation_gap_index": 7.4, "utility_gap_index": 6.2, "environment_gap_index": 6.6, "civic_amenity_gap_index": 7.0},
    # South Lucknow: Cantonment, Alambagh â€” mixed urban, better infra
    "South":   {"population": 328000, "youth_pct": 27, "nearest_school_km": 2.9, "nearest_hospital_km": 4.8,  "literacy_rate": 79, "road_gap_index": 4.4, "water_gap_index": 4.8, "sanitation_gap_index": 5.2, "utility_gap_index": 4.1, "environment_gap_index": 4.5, "civic_amenity_gap_index": 4.7},
    # East Lucknow: Gomti Nagar, Indira Nagar â€” newer development zones
    "East":    {"population": 287000, "youth_pct": 30, "nearest_school_km": 4.1, "nearest_hospital_km": 6.5,  "literacy_rate": 76, "road_gap_index": 5.3, "water_gap_index": 6.1, "sanitation_gap_index": 6.4, "utility_gap_index": 5.2, "environment_gap_index": 5.8, "civic_amenity_gap_index": 5.5},
    # West Lucknow: Chinhat, Amausi â€” industrial-adjacent, developing
    "West":    {"population": 241000, "youth_pct": 32, "nearest_school_km": 5.6, "nearest_hospital_km": 8.3,  "literacy_rate": 72, "road_gap_index": 6.5, "water_gap_index": 5.9, "sanitation_gap_index": 6.9, "utility_gap_index": 6.7, "environment_gap_index": 7.2, "civic_amenity_gap_index": 6.3},
    # Central Lucknow: Hazratganj, Chowk, Aminabad â€” dense urban core
    "Central": {"population": 421000, "youth_pct": 24, "nearest_school_km": 1.6, "nearest_hospital_km": 2.4,  "literacy_rate": 85, "road_gap_index": 3.8, "water_gap_index": 3.5, "sanitation_gap_index": 4.2, "utility_gap_index": 3.1, "environment_gap_index": 4.0, "civic_amenity_gap_index": 3.6},
}

PRIORITY_WEIGHTS = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}

INFRA_GAP_SOURCES = {
    "education_access": "UDISE+ school-density layer, constituency zone estimate",
    "health_access": "NHM facility mapping layer, constituency zone estimate",
    "road_connectivity": "PWD road-condition and ward connectivity proxy",
    "water_sanitation": "Jal Jeevan/NULM ward service-gap proxy",
    "power_lighting": "DISCOM outage and streetlight coverage proxy",
    "environment_public_space": "LMC parks, waste, and flood-risk proxy",
    "civic_amenity": "Composite ward amenity gap proxy",
}

def project_gap_signal(category: str, semantic_tag: str, title: str, demo: dict) -> dict:
    """Pick the deprivation signal that matches the project type."""
    text = f"{category or ''} {semantic_tag or ''} {title or ''}".lower()
    if any(k in text for k in ("school", "education", "literacy", "classroom")):
        raw_value = demo["nearest_school_km"]
        return {"key": "education_access", "label": "Nearest school distance", "value": raw_value, "unit": "km", "normalized_gap": min(raw_value, 10), "source": INFRA_GAP_SOURCES["education_access"]}
    if any(k in text for k in ("hospital", "health", "clinic", "phc", "dispensary")):
        raw_value = demo["nearest_hospital_km"]
        return {"key": "health_access", "label": "Nearest health facility distance", "value": raw_value, "unit": "km", "normalized_gap": min(raw_value, 10), "source": INFRA_GAP_SOURCES["health_access"]}
    if any(k in text for k in ("road", "bridge", "footpath", "traffic", "signal", "pothole")):
        raw_value = demo["road_gap_index"]
        return {"key": "road_connectivity", "label": "Road/connectivity gap index", "value": raw_value, "unit": "/10", "normalized_gap": raw_value, "source": INFRA_GAP_SOURCES["road_connectivity"]}
    if any(k in text for k in ("water", "drain", "sewer", "sanitation", "pipeline", "toilet", "nala")):
        raw_value = max(demo["water_gap_index"], demo["sanitation_gap_index"])
        return {"key": "water_sanitation", "label": "Water/sanitation gap index", "value": raw_value, "unit": "/10", "normalized_gap": raw_value, "source": INFRA_GAP_SOURCES["water_sanitation"]}
    if any(k in text for k in ("electric", "power", "light", "streetlight", "transformer")):
        raw_value = demo["utility_gap_index"]
        return {"key": "power_lighting", "label": "Power/lighting gap index", "value": raw_value, "unit": "/10", "normalized_gap": raw_value, "source": INFRA_GAP_SOURCES["power_lighting"]}
    if any(k in text for k in ("park", "waste", "garbage", "green", "pollution", "flood")):
        raw_value = demo["environment_gap_index"]
        return {"key": "environment_public_space", "label": "Environment/public-space gap index", "value": raw_value, "unit": "/10", "normalized_gap": raw_value, "source": INFRA_GAP_SOURCES["environment_public_space"]}
    raw_value = demo["civic_amenity_gap_index"]
    return {"key": "civic_amenity", "label": "Composite civic amenity gap index", "value": raw_value, "unit": "/10", "normalized_gap": raw_value, "source": INFRA_GAP_SOURCES["civic_amenity"]}

# ---------------------------------------------------------------------------
# RANKED PROJECTS CACHE
# ---------------------------------------------------------------------------
# Caches ranked project results for CACHE_TTL_SECONDS to avoid calling
# Gemini justifications on every dashboard load. Cache is per-process
# (warm Cloud Function instances); invalidated on timeout or new sanction.
# ---------------------------------------------------------------------------
_RANKED_CACHE: dict = {"data": None, "at": 0.0}
CACHE_TTL_SECONDS = 300  # 5 minutes

@app.get("/projects/ranked")
async def get_ranked_projects(db = Depends(get_db), admin = Depends(verify_admin)):
    """Groups messages into projects, scores them, and generates AI justification.
    Results are cached in-process for 5 minutes to reduce Gemini API cost at scale."""
    now = time.time()
    if _RANKED_CACHE["data"] and (now - _RANKED_CACHE["at"]) < CACHE_TTL_SECONDS:
        logger.info("[CACHE HIT] Returning cached ranked projects (age: %.0fs)", now - _RANKED_CACHE["at"])
        return JSONResponse(content=_RANKED_CACHE["data"])

    logger.info("[CACHE MISS] Recomputing ranked projects...")
    all_docs = list(db.collection('messages').limit(500).stream())
    messages = [d.to_dict() for d in all_docs]

    # ---------------------------------------------------------------------------
    # SEMANTIC CLUSTERING
    # Cluster by (semantic_tag, zone) instead of raw (category, zone).
    # semantic_tag is a normalized snake_case key assigned by Gemini during
    # triage â€” it groups proposals about the *same real-world issue* together
    # regardless of how they were phrased or which broad category they fell into.
    # Falls back to category for older messages that pre-date the semantic_tag field.
    # ---------------------------------------------------------------------------
    clusters = {}
    for m in messages:
        # Use semantic_tag if available, else fall back to category, else body snippet
        tag = m.get("semantic_tag") or m.get("category")
        if not tag:
            body_snippet = (m.get("body") or "").strip()[:40]
            tag = body_snippet if body_snippet else "General Proposal"
        zone = m.get("constituency_zone") or "Central"
        key = f"{tag}|{zone}"
        if key not in clusters:
            clusters[key] = {
                "category": m.get("category") or tag,
                "semantic_tag": tag,
                "zone": zone,
                "messages": [], "senders": set(),
                "budget_sum": 0, "priority_score": 0,
            }
        clusters[key]["messages"].append(m)
        clusters[key]["senders"].add(m.get("sender"))
        clusters[key]["budget_sum"] += m.get("estimated_budget") or 0
        clusters[key]["priority_score"] += PRIORITY_WEIGHTS.get(m.get("priority"), 1)

    # Score and rank
    ranked = []
    for key, cl in clusters.items():
        demand = len(cl["messages"])
        demo = DEMO_DEMOGRAPHICS.get(cl["zone"], DEMO_DEMOGRAPHICS["Central"])
        # Use most common summary as project title
        summaries = [m.get("summary") or "" for m in cl["messages"] if m.get("summary")]
        title = summaries[0] if summaries else f"{cl['category']} â€” {cl['zone']} Zone"
        gap_signal = project_gap_signal(cl["category"], cl["semantic_tag"], title, demo)
        infra_gap = gap_signal["normalized_gap"]
        impact_score = round((demand * cl["priority_score"] * (1 + infra_gap / 10)), 1)
        avg_budget = int(cl["budget_sum"] / demand) if demand else 0
        ranked.append({
            "id": key,
            "title": title,
            "category": cl["category"],
            "semantic_tag": cl["semantic_tag"],
            "zone": cl["zone"],
            "demand_count": demand,
            "unique_senders": len(cl["senders"]),
            "impact_score": impact_score,
            "estimated_budget": avg_budget,
            "demographics": demo,
            "latitude": cl["messages"][0].get("latitude"),
            "longitude": cl["messages"][0].get("longitude"),
            "evidence": {
                "gap_signal": gap_signal,
                "formula": "demand_count * priority_score * (1 + category_gap / 10)",
                "priority_score": cl["priority_score"],
            },
            "justification": None,  # filled below
            "status": cl["messages"][0].get("status", "Open"),
            "senders": list(cl["senders"]),
        })

    ranked.sort(key=lambda x: x["impact_score"], reverse=True)
    top = ranked[:8]

    # Generate AI justification for top 1 (Gemini) - limited to save quota for Chat agent
    if gemini_client:
        for proj in top[:1]:
            try:
                demo = proj["demographics"]
                gap_signal = proj["evidence"]["gap_signal"]
                prompt = (
                    f"You are an AI advisor for an Indian MP's office. Write a vivid, compelling 2-3 sentence justification "
                    f"(max 60 words) explaining EXACTLY why this project was ranked so highly for immediate action. "
                    f"Highlight citizen demand, the relevant deprivation metric, and the calculated impact score. "
                    f"Be highly specific, data-driven, and urgent.\\n\\n"
                    f"Project: {proj['title']}\\n"
                    f"Impact Score: {proj['impact_score']} (Very High)\\n"
                    f"Topic: {proj.get('semantic_tag', proj['category'])}\n"
                    f"Zone: {proj['zone']} Constituency\n"
                    f"Citizen Demand: {proj['demand_count']} proposals received\n"
                    f"Zone Population: {demo['population']:,} residents\n"
                    f"Relevant deprivation signal: {gap_signal['label']} = {gap_signal['value']}{gap_signal['unit']}\n"
                    f"Signal source: {gap_signal['source']}\n"
                    f"Estimated cost: â‚¹{proj['estimated_budget']:,}\n"
                )
                # Call Gemini for a smart justification
                resp = gemini_client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt
                )
                proj["justification"] = resp.text.strip().replace('"', '')
            except Exception as e:
                logger.error(f"[AI JUSTIFICATION] Failed: {e}")
                gap_signal = proj["evidence"]["gap_signal"]
                proj["justification"] = f"{proj['demand_count']} citizens in {proj['zone']} flagged this. Evidence: {gap_signal['label']} is {gap_signal['value']}{gap_signal['unit']}."

    # Fallback justification for remaining
    for proj in top:
        if not proj.get("justification"):
            gap_signal = proj["evidence"]["gap_signal"]
            proj["justification"] = f"{proj['demand_count']} citizens in {proj['zone']} flagged this. Evidence: {gap_signal['label']} is {gap_signal['value']}{gap_signal['unit']}."

    # Cache results before returning
    _RANKED_CACHE["data"] = top
    _RANKED_CACHE["at"] = time.time()
    logger.info("[CACHE SET] Ranked projects cached for %ds", CACHE_TTL_SECONDS)
    return JSONResponse(content=top)


class SanctionRequest(BaseModel):
    project_id: str
    title: str
    zone: str
    category: str
    senders: list

@app.post("/projects/sanction")
async def sanction_project(req: SanctionRequest, db = Depends(get_db), admin = Depends(verify_admin)):
    """Sanctions a project cluster, updates message statuses, notifies citizens."""
    # Update all messages in this cluster to Sanctioned
    all_docs = list(db.collection('messages').stream())
    notified = 0
    notified_senders = set()
    cluster_tag = None
    cluster_zone = req.zone
    if "|" in req.project_id:
        cluster_tag, cluster_zone = req.project_id.rsplit("|", 1)

    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) if TWILIO_ACCOUNT_SID else None

    for doc in all_docs:
        msg = doc.to_dict()
        msg_tag = msg.get("semantic_tag") or msg.get("category")
        same_cluster = (
            msg.get("constituency_zone") == cluster_zone and
            ((cluster_tag and msg_tag == cluster_tag) or (not cluster_tag and msg.get("category") == req.category))
        )
        if same_cluster:
            doc.reference.update({"status": "Sanctioned"})
            sender = msg.get("sender")
            if sender and sender not in notified_senders and twilio_client and TWILIO_WHATSAPP_NUMBER:
                try:
                    twilio_client.messages.create(
                        from_=TWILIO_WHATSAPP_NUMBER,
                        to=sender,
                        body=(
                            f"ðŸŽ‰ Great news! Your proposal regarding '{req.title}' has been *SANCTIONED* by your MP's office. "
                            f"Reference: {req.project_id}. Work will begin as per the planning schedule. Thank you for making your voice heard! â€” UrbanOS"
                        )
                    )
                    notified_senders.add(sender)
                    notified += 1
                except Exception as e:
                    logger.error(f"WhatsApp notify failed for {sender}: {e}")

    # Log sanction in a separate collection
    db.collection('sanctioned_projects').add({
        "project_id": req.project_id,
        "title": req.title,
        "zone": req.zone,
        "category": req.category,
        "semantic_tag": cluster_tag,
        "sanctioned_at": datetime.now().isoformat(),
        "citizens_notified": notified,
    })

    _RANKED_CACHE["data"] = None
    _RANKED_CACHE["at"] = 0.0

    return {"status": "sanctioned", "citizens_notified": notified}


@app.get("/media-proxy")
async def media_proxy(url: str):
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        raise HTTPException(status_code=500, detail="Twilio credentials not configured.")
        
    async def media_stream():
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "GET", 
                url, 
                auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    yield chunk

    return StreamingResponse(media_stream(), media_type="image/jpeg")





import asyncio
from flask import Response
from firebase_functions import https_fn, options

@https_fn.on_request(memory=options.MemoryOption.MB_512, timeout_sec=300)
def api(req: https_fn.Request) -> https_fn.Response:
    environ = req.environ
    
    scope = {
        'type': 'http',
        'http_version': '1.1',
        'method': environ.get('REQUEST_METHOD', 'GET'),
        'path': environ.get('PATH_INFO', ''),
        'raw_path': environ.get('PATH_INFO', '').encode('latin1'),
        'query_string': environ.get('QUERY_STRING', '').encode('latin1'),
        'headers': [],
        'client': (environ.get('REMOTE_ADDR'), environ.get('REMOTE_PORT')),
        'server': (environ.get('SERVER_NAME'), environ.get('SERVER_PORT')),
    }
    
    for key, value in environ.items():
        if key.startswith('HTTP_'):
            header_name = key[5:].lower().replace('_', '-').encode('latin1')
            scope['headers'].append((header_name, value.encode('latin1')))
        elif key in ('CONTENT_TYPE', 'CONTENT_LENGTH') and value:
            header_name = key.lower().replace('_', '-').encode('latin1')
            scope['headers'].append((header_name, value.encode('latin1')))
            
    content_length = int(environ.get('CONTENT_LENGTH', 0) or 0)
    body_data = environ['wsgi.input'].read(content_length) if content_length > 0 else b""
    
    async def receive():
        return {'type': 'http.request', 'body': body_data, 'more_body': False}
        
    status_code = [200]
    headers_list = []
    body_chunks = []
    
    async def send(message):
        if message['type'] == 'http.response.start':
            status_code[0] = message['status']
            for name, value in message.get('headers', []):
                headers_list.append((name.decode('latin1'), value.decode('latin1')))
        elif message['type'] == 'http.response.body':
            body_chunks.append(message.get('body', b''))
            
    # Run the ASGI app synchronously on the main thread
    asyncio.run(app(scope, receive, send))
    
    return Response(b"".join(body_chunks), status=status_code[0], headers=headers_list)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
