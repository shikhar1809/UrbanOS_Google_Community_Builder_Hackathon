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
from fastapi.middleware.gzip import GZipMiddleware
import analytics as AN
import guardrails as GR
import sim
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

# The feed ships the full 1000+ record dataset; gzip takes that from ~600KB to ~60KB.
app.add_middleware(GZipMiddleware, minimum_size=1024)

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
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
try:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
except Exception as _e:
    logger.warning(f"Gemini client init failed: {_e}")
    gemini_client = None

try:
    from groq import Groq as GroqClient
    _groq_client = GroqClient(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
    if _groq_client:
        logger.info("Groq (Llama-3) client initialized successfully.")
except Exception as _ge:
    logger.warning(f"Groq client init failed: {_ge}")
    _groq_client = None

class TriageResult(BaseModel):
    category: Literal["Construction Development", "Land Development", "Infrastructure Upgrade", "Public Utility Works", "Environmental Project", "Maintenance & Repair", "Other"] = Field(description="Must be one of the strictly provided categories.")
    priority: Literal["Low", "Medium", "High", "Critical"] = Field(description="Priority severity level.")
    feasibility: Literal["High", "Moderate", "Complex", "Unknown"] = Field(alias="sentiment", description="Maps to the old sentiment DB column.")
    extracted_location: str = Field(description="Street name, landmark, or area extracted from text. Empty string if none.")
    summary: str = Field(description="Short 4-5 word summary title of the project proposal.")
    original_language: str = Field(description="The language the user originally submitted their request in (e.g., Hindi, English, Spanish).")
    constituency_zone: Literal["North", "South", "East", "West", "Central"] = Field(description="Infer this constituency zone from the location context. Default to Central if unknown.")
    estimated_budget: int = Field(description="An integer representing the estimated cost in INR based on the project scale (e.g., 500000 for small, 50000000 for large capital works).")
    responsible_body: str = Field(default="Municipal Corporation", description="The city department responsible for handling this issue (e.g. 'Public Works Department (PWD)', 'Jal Sansthan (Water Board)', 'Municipal Corporation').")
    ward_no: str = Field(default="Ward 1", description="Assign a demo municipal ward number (e.g. 'Ward 14') appropriate for the location.")
    ward_officer: str = Field(default="Rajeev Singh", description="Assign a demo Indian name for the responsible ward officer.")
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
        # New greeting flow
        "greeting":       "👋 Hello! Welcome to *UrbanOS* — Your One-Stop Solution for All Urban Needs.\n\nMay I know your name?",
        "ask_intent":     "Hey {name}! 👋 Great to have you here.\n\nAre you here to:\n1️⃣ *Log a New Issue*\n2️⃣ *Track an Existing Issue*\n\nReply with 1 or 2.",
        "ask_track_ref":  "Sure! Please share your Report Reference ID (e.g. #UO7C9351) and I'll pull up the latest status for you.",
        "ask_issue":      "Please describe the issue you are facing. Is it a recurring problem?\n\nYou can type or send a 🎤 voice note!",
        "jurisdiction_fail": "⚠️ Sorry, this issue does not fall under the jurisdiction of UrbanOS or Lucknow Municipal services. Please contact the relevant authority directly.",
        "geo_fail": "\U0001F4CD This location appears to be outside Lucknow ({detail}). UrbanOS only handles grievances within Lucknow municipal limits. Please contact your local municipal body.",
        "image_reject": "\U0001F4F7 Thanks, but {detail}. Please send a clear photo of the problem itself (the road, drain, wire, garbage point and so on), or reply DONE to submit without a photo.",
        "rate_limited": "\u23F3 {detail}",
        "duplicate_report": "\u2139\uFE0F {detail}",
        "ask_location":   "📍 Description saved. Now share your location — send a *location pin* or type the area name.",
        "ask_photo":      "📸 Location saved!\n\nYou can now attach up to *3 photos* as evidence. Send them one by one.\nWhen done, reply *'done'* to proceed, or *'skip'* to skip photos.",
        "photo_received": "✅ Photo {n} received! Send more or reply *'done'* to finalize.",
        "max_photos":     "You've attached 3 photos (maximum). Finalizing your report...",
        "finalizing":     "⏳ Finalizing your report, please wait...",
        "skip_prompt":    "Please send a photo or reply *'skip'* to continue without one.",
        "location_prompt":"Please send a *location pin* or type your area name.",
        "text_or_voice":  "Please describe the issue in text or send a 🎤 voice note.",
        "survey_thanks":  "🙏 Thank you for your feedback! Your ward officer will consider this.",
        "new_prompt":     "Reply *'new'* anytime to submit another issue.",
        "invalid_option": "Please reply with a number between 1 and {n}.",
        "voice_ack":      "🎤 Voice note received, transcribing now...",
        "discarded":      "Previous session cleared. What issue would you like to report?",
        "no_reports":     "You have no active reports yet.",
        "ask_another":    "Would you like to submit *another issue*?\n\nReply *'yes'* to log a new one or *'no'* to exit.",
        "goodbye":        "Thank you for using UrbanOS! Have a great day. 🙏",
    },
    "Hindi": {
        "greeting":       "👋 नमस्ते! *UrbanOS* में आपका स्वागत है — आपकी सभी शहरी ज़रूरतों का एकमात्र समाधान।\n\nक्या आप अपना नाम बता सकते हैं?",
        "ask_intent":     "नमस्ते {name}! 👋\n\nआप यहाँ किसलिए आए हैं?\n1️⃣ *नई समस्या दर्ज करें*\n2️⃣ *मौजूदा समस्या ट्रैक करें*\n\n1 या 2 से जवाब दें।",
        "ask_track_ref":  "ज़रूर! कृपया अपना रिपोर्ट Reference ID (जैसे #UO7C9351) शेयर करें।",
        "ask_issue":      "कृपया अपनी समस्या का विवरण दें। क्या यह बार-बार हो रहा है?\n\nटाइप करें या 🎤 वॉइस नोट भेजें!",
        "jurisdiction_fail": "⚠️ माफ़ करें, यह समस्या UrbanOS के अधिकार क्षेत्र में नहीं आती।",
        "geo_fail": "\U0001F4CD \u092F\u0939 \u0938\u094D\u0925\u093E\u0928 \u0932\u0916\u0928\u090A \u0915\u0947 \u092C\u093E\u0939\u0930 \u0932\u0917\u0924\u093E \u0939\u0948 ({detail})\u0964 UrbanOS \u0915\u0947\u0935\u0932 \u0932\u0916\u0928\u090A \u0928\u0917\u0930 \u0928\u093F\u0917\u092E \u0915\u0940 \u0936\u093F\u0915\u093E\u092F\u0924\u0947\u0902 \u0926\u0947\u0916\u0924\u093E \u0939\u0948\u0964",
        "image_reject": "\U0001F4F7 \u0927\u0928\u094D\u092F\u0935\u093E\u0926, \u0932\u0947\u0915\u093F\u0928 {detail}\u0964 \u0915\u0943\u092A\u092F\u093E \u0938\u092E\u0938\u094D\u092F\u093E \u0915\u0940 \u0938\u094D\u092A\u0937\u094D\u091F \u092B\u094B\u091F\u094B \u092D\u0947\u091C\u0947\u0902, \u092F\u093E DONE \u0932\u093F\u0916\u0947\u0902\u0964",
        "rate_limited": "\u23F3 {detail}",
        "duplicate_report": "\u2139\uFE0F {detail}",
        "ask_location":   "📍 विवरण सहेजा गया। अब अपना स्थान साझा करें — *लोकेशन पिन* भेजें या क्षेत्र का नाम लिखें।",
        "ask_photo":      "📸 स्थान सहेजा गया!\n\nआप *3 फ़ोटो तक* भेज सकते हैं। एक-एक करके भेजें।\nजब हो जाए, *'done'* लिखें या *'skip'* लिखें।",
        "photo_received": "✅ फ़ोटो {n} मिली! और भेजें या *'done'* लिखें।",
        "max_photos":     "आपने 3 फ़ोटो (अधिकतम) भेज दी हैं। रिपोर्ट तैयार हो रही है...",
        "finalizing":     "⏳ आपकी रिपोर्ट तैयार हो रही है...",
        "skip_prompt":    "कृपया फ़ोटो भेजें या *'skip'* लिखें।",
        "location_prompt":"कृपया *लोकेशन पिन* भेजें या क्षेत्र का नाम लिखें।",
        "text_or_voice":  "कृपया समस्या को टेक्स्ट में बताएं या 🎤 वॉइस नोट भेजें।",
        "survey_thanks":  "🙏 आपकी प्रतिक्रिया के लिए धन्यवाद!",
        "new_prompt":     "नई समस्या दर्ज करने के लिए *'new'* लिखें।",
        "invalid_option": "कृपया 1 से {n} के बीच कोई नंबर भेजें।",
        "voice_ack":      "🎤 वॉइस नोट मिला, प्रोसेस हो रहा है...",
        "discarded":      "पिछला सत्र साफ़ किया गया। आप कौन सी समस्या रिपोर्ट करना चाहते हैं?",
        "no_reports":     "आपकी कोई सक्रिय रिपोर्ट नहीं है।",
        "ask_another":    "क्या आप *कोई और समस्या* दर्ज करना चाहते हैं?\n\n*'yes'* या *'no'* लिखें।",
        "goodbye":        "UrbanOS का उपयोग करने के लिए धन्यवाद! 🙏",
    },
    "Urdu": {
        "greeting":       "👋 السلام علیکم! *UrbanOS* میں خوش آمدید۔\n\nکیا آپ اپنا نام بتا سکتے ہیں؟",
        "ask_intent":     "ہیلو {name}! 👋\n\n1️⃣ *نئی شکایت درج کریں*\n2️⃣ *موجودہ شکایت ٹریک کریں*\n\n1 یا 2 جواب دیں۔",
        "ask_track_ref":  "براہ کرم اپنا Reference ID شیئر کریں (مثلاً #UO7C9351)۔",
        "ask_issue":      "براہ کرم مسئلہ بیان کریں۔ کیا یہ بار بار ہوتا ہے؟ 🎤",
        "jurisdiction_fail": "⚠️ معذرت، یہ مسئلہ UrbanOS کے دائرہ اختیار میں نہیں آتا۔",
        "geo_fail": "\U0001F4CD {detail} - UrbanOS Lucknow.",
        "image_reject": "\U0001F4F7 {detail}. DONE.",
        "rate_limited": "\u23F3 {detail}",
        "duplicate_report": "\u2139\uFE0F {detail}",
        "ask_location":   "📍 تفصیل محفوظ ہو گئی۔ اب اپنا مقام شیئر کریں۔",
        "ask_photo":      "📸 *3 تصاویر* تک بھیجیں۔ مکمل ہونے پر *'done'* لکھیں۔",
        "photo_received": "✅ تصویر {n} مل گئی!",
        "max_photos":     "3 تصاویر مل گئی ہیں۔ رپورٹ تیار ہو رہی ہے...",
        "finalizing":     "⏳ آپ کی رپورٹ تیار ہو رہی ہے...",
        "skip_prompt":    "تصویر بھیجیں یا *'skip'* لکھیں۔",
        "location_prompt":"لوکیشن پن بھیجیں یا علاقے کا نام لکھیں۔",
        "text_or_voice":  "مسئلہ متن میں بیان کریں یا 🎤 وائس نوٹ بھیجیں۔",
        "survey_thanks":  "🙏 آپ کے تاثرات کا شکریہ!",
        "new_prompt":     "نئی شکایت کے لیے *'new'* لکھیں۔",
        "invalid_option": "براہ کرم 1 سے {n} کے درمیان نمبر بھیجیں۔",
        "voice_ack":      "🎤 وائس نوٹ مل گیا، پروسیس ہو رہا ہے...",
        "discarded":      "پچھلا سیشن صاف کر دیا گیا۔",
        "no_reports":     "آپ کی کوئی فعال رپورٹ نہیں ہے۔",
        "ask_another":    "*'yes'* یا *'no'* — کوئی اور شکایت؟",
        "goodbye":        "UrbanOS استعمال کرنے کا شکریہ! 🙏",
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
        if sim.is_sim_media(media_url):
            loaded = sim.load_media(media_url)
            if not loaded:
                return None
            data = loaded[0]
        else:
            auth_tuple = (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN else None
            with httpx.Client(timeout=12) as client:
                response = client.get(media_url, auth=auth_tuple)
                response.raise_for_status()
                data = response.content
        if len(data) > 5 * 1024 * 1024:
            logger.warning("[MEDIA] Photo skipped because it exceeds 5MB.")
            return None
        return types.Part.from_bytes(data=data, mime_type=mime_type)
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

    # ─── GUARDRAIL 1: flood / rate limiting ──────────────────
    # Runs before any parsing so an abusive sender costs us nothing but one
    # Firestore read. Fails open if the guard itself errors.
    _spam = None
    try:
        _spam = GR.SpamGuard(db)
        _gate = _spam.check_message(sender)
        if not _gate["allow"]:
            logger.warning(f"[GUARDRAIL] rate limit hit for {sender}: {_gate['reason']}")
            twiml.message(get_reply("rate_limited", msg_lang, detail=_gate["message"]))
            return HTMLResponse(content=str(twiml), media_type="application/xml")
    except Exception as _se:
        logger.warning(f"[GUARDRAIL] spam guard unavailable: {_se}")
        _spam = None
    
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
    
    # Retrieve persisted language from session
    session_data = {}
    if session and session.collected_data:
        try:
            session_data = json.loads(session.collected_data)
        except Exception:
            pass
    lang = session_data.get("user_language", msg_lang)
    
    # ─── STEP 0: Fresh start — send greeting, ask name ───────────────────────
    if current_step is None:
        # Check if user is responding to a free-standing survey
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
        first_lang = detect_script_language(body) if body else "English"
        twiml.message(get_reply("greeting", first_lang))
        update_session(db, sender, "awaiting_name", {"user_language": first_lang})
        return HTMLResponse(content=str(twiml), media_type="application/xml")
    
    # ─── STEP 1: Capture name, ask intent ────────────────────────────────────
    elif current_step == "awaiting_name":
        name = body.strip().title() if body else "Friend"
        update_session(db, sender, "awaiting_intent", {"user_name": name, "user_language": lang})
        twiml.message(get_reply("ask_intent", lang, name=name))
        return HTMLResponse(content=str(twiml), media_type="application/xml")

    # ─── STEP 2: Intent — Log issue (1) or Track issue (2) ───────────────────
    elif current_step == "awaiting_intent":
        if body_lower in ["2", "track", "status"]:
            update_session(db, sender, "awaiting_track_ref", {})
            twiml.message(get_reply("ask_track_ref", lang))
            return HTMLResponse(content=str(twiml), media_type="application/xml")
        else:
            # Default to logging new issue (option 1)
            update_session(db, sender, "awaiting_description", {})
            twiml.message(get_reply("ask_issue", lang))
            return HTMLResponse(content=str(twiml), media_type="application/xml")

    # ─── STEP 2b: Track by reference ID ──────────────────────────────────────
    elif current_step == "awaiting_track_ref":
        ref_query = body.strip().lstrip('#').upper()
        try:
            report_docs = list(db.collection('messages').where('reference_id', '==', ref_query).limit(1).stream())
        except Exception:
            report_docs = list(db.collection('messages').where('reference_id', '==', ref_query).limit(1).stream())
        report = report_docs[0].to_dict() if report_docs else None
        if report:
            status_msg = (
                f"📋 *Report #{report.get('reference_id')}*\n"
                f"Category: {report.get('category', 'N/A')}\n"
                f"Status: {report.get('status', 'Open')}\n"
                f"Summary: {report.get('summary', 'N/A')}\n"
                f"Ward: {report.get('ward_no', 'N/A')}\n"
                f"Officer: {report.get('ward_officer', 'N/A')}\n"
                f"Dept: {report.get('responsible_body', 'N/A')}"
            )
            twiml.message(status_msg)
        else:
            twiml.message(f"❌ No report found with ID #{ref_query}. Please double-check your reference number (e.g. UO664607).")
        clear_session(db, sender)
        return HTMLResponse(content=str(twiml), media_type="application/xml")

    # ─── STEP 3: Capture issue description ───────────────────────────────────
    elif current_step == "awaiting_description":
        if "audio" in media_type and media_url:
            from worker import send_whatsapp_message
            send_whatsapp_message(sender, get_reply("voice_ack", lang))
            import asyncio
            asyncio.run(process_voice_note(media_url, sender, lang, db))
            return HTMLResponse(content="<Response></Response>", media_type="application/xml")
        elif body:
            detected_lang = detect_script_language(body)
            if detected_lang != "English":
                lang = detected_lang
            update_session(db, sender, "awaiting_location", {"description": body, "user_language": lang})
            twiml.message(get_reply("ask_location", lang))
            return HTMLResponse(content=str(twiml), media_type="application/xml")
        else:
            twiml.message(get_reply("text_or_voice", lang))
            return HTMLResponse(content=str(twiml), media_type="application/xml")
            
    # ─── STEP 4: Capture location ─────────────────────────────────────────────
    elif current_step == "awaiting_location":
        location_data = None
        source = None
        if lat_str and lng_str:
            # ─── GUARDRAIL 2a: hard geofence on a GPS pin ────────────────────
            # A dropped pin is unambiguous, so reject here rather than letting the
            # report reach triage and pollute ward statistics.
            _geo = GR.check_coordinates(lat_str, lng_str)
            if _geo["verdict"] == "reject":
                logger.warning(f"[GUARDRAIL] geofence reject for {sender}: {_geo['reason']}")
                if _spam:
                    _spam.record_rejection(sender, "out_of_area")
                clear_session(db, sender)
                twiml.message(get_reply("geo_fail", lang, detail=_geo["reason"]))
                return HTMLResponse(content=str(twiml), media_type="application/xml")
            if _geo["verdict"] == "accept_flagged":
                logger.info(f"[GUARDRAIL] outer-district pin accepted for {sender}: {_geo['reason']}")
            location_data = f"{lat_str},{lng_str}"
            source = "gps_pin"
        elif body:
            # ─── GUARDRAIL 2b: cheap text geofence ───────────────────────────
            # Only fires when another city is named outright. Anything ambiguous
            # is left to the combined classifier at finalize.
            _geo_txt = GR.check_location_text(body)
            if _geo_txt["verdict"] == "reject":
                logger.warning(f"[GUARDRAIL] text geofence reject for {sender}: {_geo_txt['reason']}")
                if _spam:
                    _spam.record_rejection(sender, "out_of_area")
                clear_session(db, sender)
                twiml.message(get_reply("geo_fail", lang, detail=_geo_txt["reason"]))
                return HTMLResponse(content=str(twiml), media_type="application/xml")
            location_data = body
            source = "text"
        if location_data:
            update_session(db, sender, "awaiting_photo", {"location": location_data, "location_source": source})
            twiml.message(get_reply("ask_photo", lang))
            return HTMLResponse(content=str(twiml), media_type="application/xml")
        else:
            twiml.message(get_reply("location_prompt", lang))
            return HTMLResponse(content=str(twiml), media_type="application/xml")
            
    # ─── STEP 5: Multi-photo (up to 3) ───────────────────────────────────────
    elif current_step == "awaiting_photo":
        photo_urls = session_data.get("photo_urls", [])
        if "image" in media_type and media_url:
            # ─── GUARDRAIL 3: is this photo actually evidence? ───────────────
            # Rejects selfies, screenshots and memes before they enter the
            # evidence store. Only acts on a confident rejection, so a dim photo
            # of a broken streetlight still gets through.
            _img_ok = True
            try:
                if gemini_client:
                    _part = fetch_twilio_media_part(media_url, media_type)
                    if _part:
                        _iresp = gemini_client.models.generate_content(
                            model='gemini-2.5-flash',
                            contents=[GR.IMAGE_CLASSIFIER_PROMPT, _part],
                        )
                        _verdict = GR.parse_image_verdict(_iresp.text)
                        logger.info(f"[GUARDRAIL] image verdict for {sender}: "
                                    f"{_verdict['label']}/{_verdict['confidence']} - {_verdict['reason']}")
                        if not _verdict["accept"]:
                            _img_ok = False
                            if _spam:
                                _spam.record_rejection(sender, "irrelevant_image")
                            twiml.message(get_reply("image_reject", lang, detail=_verdict["message"]))
            except Exception as _ie:
                logger.warning(f"[GUARDRAIL] image check failed, accepting: {_ie}")
            if not _img_ok:
                update_session(db, sender, "awaiting_photo", {"photo_urls": photo_urls})
                return HTMLResponse(content=str(twiml), media_type="application/xml")
            photo_urls.append(media_url)
            if len(photo_urls) >= 3:
                update_session(db, sender, "finalize", {"photo_urls": photo_urls, "photo_type": media_type})
                twiml.message(get_reply("max_photos", lang))
            else:
                update_session(db, sender, "awaiting_photo", {"photo_urls": photo_urls, "photo_type": media_type})
                twiml.message(get_reply("photo_received", lang, n=len(photo_urls)))
                return HTMLResponse(content=str(twiml), media_type="application/xml")
        elif body_lower in ["done", "skip", "next", "submit", "s"]:
            update_session(db, sender, "finalize", {"photo_urls": photo_urls, "photo_type": media_type})
            twiml.message(get_reply("finalizing", lang))
        else:
            twiml.message(get_reply("skip_prompt", lang))
            return HTMLResponse(content=str(twiml), media_type="application/xml")

    # ─── STEP 6: Ask another issue ───────────────────────────────────────────
    elif current_step == "awaiting_survey":
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
    
    elif current_step == "awaiting_another":
        if body_lower in ["yes", "y", "haan", "ha", "haa", "ok", "okay", "sure"]:
            update_session(db, sender, "awaiting_description", {"user_language": lang})
            twiml.message(get_reply("ask_issue", lang))
        else:
            clear_session(db, sender)
            twiml.message(get_reply("goodbye", lang))
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
        # Support both multi-photo (new) and single photo (legacy)
        photo_urls_list = data.get("photo_urls", [])
        photo_url = photo_urls_list[0] if photo_urls_list else data.get("photo_url", "")
        photo_type = data.get("photo_type", "")
        lang = data.get("user_language", lang)
        
        lat = None
        lng = None
        if loc_source == "gps_pin" and "," in location_raw:
            try:
                parsed_lat = float(location_raw.split(",")[0])
                parsed_lng = float(location_raw.split(",")[1])
                if -90 <= parsed_lat <= 90 and -180 <= parsed_lng <= 180:
                    lat = parsed_lat
                    lng = parsed_lng
                else:
                    logger.warning(f"[VALIDATION] Coordinates out of bounds: lat={parsed_lat}, lng={parsed_lng}")
            except Exception:
                logger.warning("[VALIDATION] Failed to parse coordinates.")
                pass
        
        # ─── GUARDRAIL 4: combined topic + geography screen ────────────────
        # One model call answers both "is this a municipal matter?" and "is it in
        # Lucknow?". The deterministic geofence above already caught obvious
        # cases; this catches the ones only a reader can judge, such as a
        # complaint whose text names a town the location field never mentioned.
        # Fails open - a classifier outage must not silence real complaints.
        gate_verdict = "accept"
        gate_reason = ""
        if description and (_groq_client or gemini_client):
            try:
                gate_prompt = GR.build_location_check_prompt(description, location_raw)
                if _groq_client:
                    gresp = _groq_client.chat.completions.create(
                        messages=[{"role": "user", "content": gate_prompt}],
                        model="llama-3.1-8b-instant",
                        max_tokens=40,
                    )
                    gate_raw = gresp.choices[0].message.content
                else:
                    gate_raw = gemini_client.models.generate_content(
                        model='gemini-2.5-flash', contents=gate_prompt
                    ).text
                parsed_gate = GR.parse_location_check(gate_raw)
                gate_verdict = parsed_gate["verdict"]
                gate_reason = parsed_gate["reason"]
            except Exception as je:
                logger.warning(f"[GUARDRAIL] intake screen failed: {je}. Defaulting to accept.")

        if gate_verdict == "reject_location":
            logger.warning(f"[GUARDRAIL] out-of-area report from {sender}: {gate_reason}")
            if _spam:
                _spam.record_rejection(sender, "out_of_area")
            clear_session(db, sender)
            twiml.message(get_reply("geo_fail", lang, detail=gate_reason or "location is not in Lucknow"))
            return HTMLResponse(content=str(twiml), media_type="application/xml")

        if gate_verdict == "reject_topic":
            logger.warning(f"[GUARDRAIL] off-topic report from {sender}: {gate_reason}")
            if _spam:
                _spam.record_rejection(sender, "off_topic")
            clear_session(db, sender)
            twiml.message(get_reply("jurisdiction_fail", lang))
            return HTMLResponse(content=str(twiml), media_type="application/xml")

        # ─── GUARDRAIL 5: quota + duplicate suppression ────────────────────
        # Checked here rather than at message time so a citizen is never blocked
        # mid-conversation, only at the point of actually filing.
        if _spam:
            sub_gate = _spam.check_submission(sender, description)
            if not sub_gate["allow"]:
                logger.info(f"[GUARDRAIL] submission blocked for {sender}: {sub_gate['reason']}")
                clear_session(db, sender)
                key = "duplicate_report" if sub_gate["reason"] == "duplicate" else "rate_limited"
                twiml.message(get_reply(key, lang, detail=sub_gate["message"]))
                return HTMLResponse(content=str(twiml), media_type="application/xml")

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
        responsible_body = "Municipal Corporation"
        ward_no = "Ward 1"
        ward_officer = "Duty Officer"
        
        if gemini_client and description:
            try:
                prompt = (
                    "You are an AI triage system for Lucknow Municipal civic grievances via WhatsApp.\n"
                    "Analyze this citizen complaint and extract all structured fields.\n"
                    "Context: Lucknow has Nagar Nigam, Jal Sansthan (Water Board), PWD, LDA, LESA, Traffic Police.\n"
                    "Potholes in gali/mohalla → Nagar Nigam. On highway → PWD.\n"
                    "Water/sewage → Jal Sansthan. Electricity → LESA. Building → LDA.\n"
                    "If an image is attached, use it as civic evidence to support the report.\n\n"
                    f"Complaint Text: {description}\n"
                    f"Submitted Location: {location_raw or 'Not provided'}"
                )
                contents = [prompt]
                image_part = fetch_twilio_media_part(photo_url, photo_type)
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
                responsible_body = triage.responsible_body
                ward_no = triage.ward_no
                ward_officer = triage.ward_officer
                
                # --- STRICT H3 HEX JURISDICTION OVERRIDE ---
                if lat and lng:
                    try:
                        import h3
                        import hashlib
                        hex_id = h3.latlng_to_cell(lat, lng, 8)
                        # Deterministic hash to one of the 110 wards
                        hash_val = int(hashlib.md5(hex_id.encode()).hexdigest(), 16)
                        w_num = (hash_val % 110) + 1
                        ward_no = f"Ward {w_num}"
                        if w_num <= 20: ward_officer = "Ramesh Yadav"
                        elif w_num <= 40: ward_officer = "Priya Sharma"
                        elif w_num <= 60: ward_officer = "Mohd. Farhan Siddiqui"
                        elif w_num <= 80: ward_officer = "Sunita Verma"
                        else: ward_officer = "Ajay Kumar Singh"
                        logger.info(f"[H3 MAPPING] Location {lat},{lng} falls in Hex {hex_id} -> {ward_no} ({ward_officer})")
                    except Exception as hex_err:
                        logger.error(f"[H3 ERROR] Could not map hex: {hex_err}")

                if loc_source == "text":
                    extracted_location = triage.extracted_location or location_raw
                summary = triage.summary
                constituency_zone = triage.constituency_zone
                estimated_budget = triage.estimated_budget
                semantic_tag = triage.semantic_tag
                visual_evidence = triage.visual_evidence or ""
            except Exception as e:
                logger.error(f"[AI ERROR] Gemini Triage Failed: {e}", exc_info=True)

                
        # Add to firestore
        doc_ref = db.collection('messages').document()
        new_message = {
            "timestamp": datetime.now().isoformat(),
            "sender": sender,
            "body": description,
            "media_url": photo_url or data.get("voice_url"),
            "media_urls": photo_urls_list,   # full list for multi-photo
            "media_type": photo_type or ("audio/ogg" if data.get("voice_url") else None),
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
            "responsible_body": responsible_body,
            "ward_no": ward_no,
            "ward_officer": ward_officer,
            "reference_id": ref_id,
            "status": "Open",
            "id": doc_ref.id
        }
        doc_ref.set(new_message)
        # Bust RAG + messages cache so new report is immediately visible
        _RAG_CACHE["data"] = None
        _RAG_CACHE["at"] = 0.0
        _MESSAGES_CACHE["data"] = None
        _MESSAGES_CACHE["at"] = 0.0
        AN.invalidate_snapshot()
        if _spam:
            _spam.record_submission(sender, description, ref_id)


        survey_docs = list(db.collection('surveys').where('is_active', '==', True).limit(1).stream())
        active_survey = survey_docs[0].to_dict() if survey_docs else None
        
        _ward_no = ward_no if 'ward_no' in locals() and ward_no else "Ward 1"
        _ward_officer = ward_officer if 'ward_officer' in locals() and ward_officer else "Duty Officer"
        _responsible_body = responsible_body if 'responsible_body' in locals() and responsible_body else "Municipal Corporation"
        user_name = session_data.get("user_name", "Citizen")
        
        # Build the rich receipt message
        receipt_msg = (
            f"✅ *Issue Logged Successfully!*\n\n"
            f"📌 Reference: *#{ref_id}*\n"
            f"👤 Reported by: {user_name}\n"
            f"📂 Category: {category or 'General'}\n"
            f"⚡ Priority: {priority or 'Normal'}\n\n"
            f"🏛️ *Assigned To:*\n"
            f"• Ward: *{_ward_no}*\n"
            f"• Department: *{_responsible_body}*\n"
            f"• Officer: *{_ward_officer}*\n\n"
            f"ℹ️ {_ward_officer} of {_responsible_body} has been notified. You will receive swift updates. Please await a follow-up survey."
        )
        
        if active_survey:
            update_session(db, sender, "awaiting_survey", {"survey_id": active_survey.get('id'), "user_name": user_name})
            options = [opt.strip() for opt in active_survey.get('options').split(',')]
            opt_text = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(options)])
            survey_q = active_survey.get('question')
            if lang != "English" and gemini_client:
                try:
                    tr = gemini_client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=f"Translate ONLY this sentence to {lang}, keep numbers and formatting intact, return only the translation:\n{survey_q}"
                    )
                    survey_q = tr.text.strip()
                except Exception:
                    pass
            twiml.message(f"{receipt_msg}\n\n*{survey_q}*\n\nReply with the number of your choice:\n{opt_text}")
        else:
            if lang != "English" and gemini_client:
                try:
                    tr = gemini_client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=f"Translate ONLY this text to {lang}, keep the reference ID #{ref_id} and names exactly as is. Preserve newlines:\n{receipt_msg}"
                    )
                    receipt_msg = tr.text.strip()
                except Exception:
                    pass
            twiml.message(receipt_msg)
            # Ask if they want to submit another issue
            update_session(db, sender, "awaiting_another", {"user_name": user_name, "user_language": lang})
            twiml.message(get_reply("ask_another", lang))
            
        logger.info(f"Issue {ref_id} successfully created for {sender}.")
        return HTMLResponse(content=str(twiml), media_type="application/xml")
    
    return HTMLResponse(content=str(twiml), media_type="application/xml")


class AudienceFilter(BaseModel):
    """Who should receive a survey.

    The audience is expressed as filters over the grievance history, because the
    only citizens reachable on WhatsApp are the ones who have written in. Values
    inside a field are OR'd, fields are AND'd.
    """
    wards: list[str] = []
    zones: list[str] = []
    categories: list[str] = []
    semantic_tags: list[str] = []
    priorities: list[str] = []
    statuses: list[str] = []
    sentiments: list[str] = []
    officers: list[str] = []
    departments: list[str] = []
    min_reports: int = 1
    range: str = "all"
    anchor: str = "now"


class SurveyCreate(BaseModel):
    question: str
    options: str
    audience: AudienceFilter | None = None


class BroadcastRequest(BaseModel):
    audience: AudienceFilter | None = None   # overrides the saved audience
    dry_run: bool = False


def _mask_phone(raw: str) -> str:
    """Show enough of a number to recognise a citizen you already know, and not
    enough to build a contact list from the preview."""
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    if len(digits) < 6:
        return "unknown"
    return f"+{digits[:2]} {digits[2:4]}••••{digits[-4:]}"


def _audience_window(snap, audience: dict):
    return AN.resolve_window((audience or {}).get("range", "all"),
                             (audience or {}).get("anchor", "now"),
                             snap.get("latest_ts"))


@app.get("/surveys")
async def get_surveys(db = Depends(get_db)):
    surveys = [d.to_dict() for d in db.collection('surveys').stream()]
    results = []
    for s in surveys:
        sid = s.get('id')
        resp_docs = list(db.collection('survey_responses').where('survey_id', '==', sid).stream())

        counts_dict = {}
        for d in resp_docs:
            opt = d.to_dict().get('selected_option')
            counts_dict[opt] = counts_dict.get(opt, 0) + 1

        sent_to = s.get('sent_to') or []
        results.append({
            "id": sid,
            "question": s.get('question'),
            "options": [opt.strip() for opt in s.get('options', '').split(',')],
            "is_active": s.get('is_active', False),
            "results": counts_dict,
            "audience": s.get('audience'),
            "audience_label": s.get('audience_label') or AN.describe_audience(s.get('audience')),
            "sent_count": len(sent_to),
            "sent_at": s.get('sent_at'),
            "response_count": len(resp_docs),
            "response_rate": round(len(resp_docs) / len(sent_to), 3) if sent_to else None,
        })
    return results


@app.post("/surveys")
async def create_survey(survey: SurveyCreate, db = Depends(get_db)):
    audience = survey.audience.model_dump() if survey.audience else {}
    doc_ref = db.collection('surveys').document()
    doc_ref.set({
        "id": doc_ref.id,
        "question": survey.question,
        "options": survey.options,
        "is_active": False,
        "audience": audience,
        "audience_label": AN.describe_audience(audience),
        "created_at": datetime.now().isoformat(),
    })
    return {"status": "success", "id": doc_ref.id, "audience_label": AN.describe_audience(audience)}


@app.post("/surveys/audience/preview")
async def preview_audience(audience: AudienceFilter, db = Depends(get_db)):
    """How many people would this reach, and who are they?

    Exists so targeting is verifiable before anyone's phone buzzes - a broadcast
    is not reversible.
    """
    snap = AN.get_snapshot(db)
    a = audience.model_dump()
    window = _audience_window(snap, a)
    res = AN.resolve_audience(snap["records"] or [], a, window)
    return JSONResponse(content={
        "label": AN.describe_audience(a),
        "range": {"key": window["key"], "label": window["label"]},
        "recipient_count": res["recipient_count"],
        "matched_reports": res["matched_reports"],
        "scoped_reports": res["scoped_reports"],
        "breakdown": res["breakdown"],
        # Phone numbers are masked: an operator needs to see the shape of the
        # audience, not a harvestable contact list.
        "sample": [
            {**r, "sender": _mask_phone(r["sender"])}
            for r in res["recipients"][:25]
        ],
    })


@app.get("/surveys/{survey_id}/report")
async def survey_report(survey_id: str, db = Depends(get_db)):
    """Detailed sentiment report: answers cross-tabulated against the grievance
    history of the people who gave them."""
    doc = db.collection('surveys').document(survey_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Survey not found")
    survey = doc.to_dict()
    responses = [d.to_dict() for d in db.collection('survey_responses').where('survey_id', '==', survey_id).stream()]
    snap = AN.get_snapshot(db)
    return JSONResponse(content=AN.survey_report(survey, responses, snap["records"] or []))


@app.post("/surveys/{survey_id}/activate")
async def activate_survey(survey_id: str, db = Depends(get_db)):
    # Deactivate all
    docs = db.collection('surveys').where('is_active', '==', True).stream()
    for doc in docs:
        doc.reference.update({"is_active": False})
    # Activate one by Firestore string ID
    db.collection('surveys').document(survey_id).update({"is_active": True})
    return {"status": "success"}


@app.post("/surveys/{survey_id}/stop")
async def stop_survey(survey_id: str, db = Depends(get_db)):
    db.collection('surveys').document(survey_id).update({"is_active": False})
    return {"status": "success"}


@app.delete("/surveys/{survey_id}")
async def delete_survey(survey_id: str, db = Depends(get_db)):
    db.collection('surveys').document(survey_id).delete()
    return {"status": "success"}


@app.post("/surveys/{survey_id}/broadcast")
async def broadcast_survey(survey_id: str, req: BroadcastRequest | None = None, db = Depends(get_db)):
    """Send a survey to its targeted audience.

    Recipients come from the survey's saved audience unless the request overrides
    it. `dry_run` resolves and returns the audience without sending anything.
    """
    survey_doc = db.collection('surveys').document(survey_id).get()
    survey = survey_doc.to_dict() if survey_doc.exists else None
    if not survey:
        raise HTTPException(status_code=404, detail="Survey not found")

    req = req or BroadcastRequest()
    audience = (req.audience.model_dump() if req.audience else None) or survey.get("audience") or {}

    snap = AN.get_snapshot(db)
    window = _audience_window(snap, audience)
    resolved = AN.resolve_audience(snap["records"] or [], audience, window)
    senders = [r["sender"] for r in resolved["recipients"] if r["sender"]]
    label = AN.describe_audience(audience)

    if req.dry_run:
        return JSONResponse(content={
            "status": "dry_run", "audience_label": label,
            "would_send_to": len(senders), "matched_reports": resolved["matched_reports"],
            "breakdown": resolved["breakdown"],
        })

    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_WHATSAPP_NUMBER:
        raise HTTPException(status_code=500, detail="Twilio credentials missing")

    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    options = [opt.strip() for opt in survey.get('options').split(',')]
    opt_text = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(options)])
    msg_body = (
        f"Your MP is requesting your feedback:\n*{survey.get('question')}*\n\n"
        f"Reply with the number of your choice:\n{opt_text}"
    )

    sent, failed = [], []
    for sender_phone in senders:
        try:
            client.messages.create(from_=TWILIO_WHATSAPP_NUMBER, body=msg_body, to=sender_phone)
            sent.append(sender_phone)
        except Exception as e:
            logger.error(f"Failed to broadcast to {sender_phone}: {e}")
            failed.append(sender_phone)

    # Persist who was contacted so the report can compute a real response rate
    # rather than guessing at the denominator.
    db.collection('surveys').document(survey_id).update({
        "audience": audience,
        "audience_label": label,
        "sent_to": sent,
        "sent_at": datetime.now().isoformat(),
    })

    return {"status": "success", "broadcast_count": len(sent), "failed_count": len(failed),
            "audience_label": label, "targeted": len(senders)}


class ChatMessage(BaseModel):
    role: Literal["user", "model"]
    content: str

class ChatRequest(BaseModel):
    query: str = Field(max_length=1000)
    history: list[ChatMessage] = []
    # Timeline scoping: when scope_to_timeline is true the agent may only reason
    # over reports inside the selected window, so the answer matches the map and
    # charts the operator is looking at rather than all history.
    range: str = "all"
    anchor: str = "now"
    scope_to_timeline: bool = True

@app.post("/api/upload-dataset")
async def upload_dataset(file: UploadFile = File(...), db = Depends(get_db)):
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
_MESSAGES_CACHE = {"data": None, "at": 0.0}
# Briefings are expensive to fold but identical for every user asking inside the
# same window, so key the cache on (snapshot version, window, anchor).
_BRIEFING_CACHE: dict = {}

GROQ_MODEL = os.getenv("GROQ_MODEL", "groq/compound")


def _static_reference() -> str:
    """Municipal reference that never changes. Kept separate from the computed
    facts so it is obvious which parts of the prompt are data and which are
    domain background."""
    return (
        "--- LUCKNOW MUNICIPAL REFERENCE ---\n"
        "City: Lucknow, Uttar Pradesh, India. Municipal body: Lucknow Nagar Nigam (LNN). "
        "Mayor: Sushma Kharakwal. Municipal Commissioner: Inderjit Singh. "
        "5 zones (North, South, East, West, Central), 110 municipal wards.\n"
        "DEPARTMENT JURISDICTION:\n"
        "  Nagar Nigam - garbage, street sweeping, street lighting, inner-lane potholes, stray animals, "
        "parks, encroachment, public toilets.\n"
        "  Jal Sansthan - drinking water, sewage network, drainage blockages, water tankers, water quality.\n"
        "  PWD - arterial roads, highways, bridges, flyovers, underpasses.\n"
        "  LDA - urban planning, building permits, unauthorised construction, colony development.\n"
        "  LESA / UPPCL - power outages, transformer failures, hanging wires, new connections (1912).\n"
        "  Traffic Police - signals, accidents, parking, wrong-side driving.\n"
        "  LMRC - metro. CMO / Health - hospitals, dengue spraying.\n"
        "WARD OFFICER ASSIGNMENT: Wards 1-20 Ramesh Yadav | 21-40 Priya Sharma | "
        "41-60 Mohd. Farhan Siddiqui | 61-80 Sunita Verma | 81-110 Ajay Kumar Singh.\n"
        "ESCALATION SLA: Critical 6 hours | High 48 hours | Medium 7 days | Low 15 days.\n"
        "WORKFLOW: Citizen WhatsApp -> AI triage (category + ward + dept) -> ward officer notified -> "
        "acknowledgement -> work order -> field team -> resolution -> citizen survey.\n"
    )


def _evidence_sample(records: list, ref_ids: list, limit: int = 40) -> str:
    """A handful of individual reports so the model can cite specifics.

    Deliberately capped: the aggregates already carry the counts, and an
    unbounded record dump is what pushes the prompt past the context window as
    the collection grows.
    """
    wanted = set(ref_ids or [])
    pool = [r for r in records if r["reference_id"] in wanted] if wanted else records
    pool = pool[:limit]
    if not pool:
        return "(no individual records in scope)"
    lines = []
    for r in pool:
        lines.append(
            "- {ref} | {date} | {ward} ({officer}) | {cat} / {tag} | {prio} | {status} | "
            "{zone} | sentiment {sent} | INR {budget} | {summary} :: {body}".format(
                ref=r["reference_id"],
                date=(r["ts_iso"] or "")[:10],
                ward=r["ward_no"] or "Unknown Ward",
                officer=r["ward_officer"],
                cat=r["category"],
                tag=r["semantic_tag"] or "untagged",
                prio=r["priority"],
                status=r["status"],
                zone=r["constituency_zone"],
                sent=r["sentiment"],
                budget=r["estimated_budget"],
                summary=r["summary"],
                body=(r["body"] or "")[:140],
            )
        )
    return "\n".join(lines)


def _cached_briefing(scoped: list, window: dict, version: int, extra_key: str = "") -> str:
    key = (version, window["key"], window.get("anchor"), extra_key)
    hit = _BRIEFING_CACHE.get(key)
    if hit is not None:
        return hit
    text = AN.build_briefing(scoped, window)
    if len(_BRIEFING_CACHE) > 24:
        _BRIEFING_CACHE.clear()
    _BRIEFING_CACHE[key] = text
    return text


@app.post("/api/chat")
async def api_chat(req: ChatRequest, db = Depends(get_db)):
    """Retrieval-augmented civic analyst.

    The important property here is that the model never does arithmetic. The
    query is routed to a deterministic analytics plan, the answer, chart and map
    filter are all computed in Python, and the model's only job is to narrate the
    computed FACTS block. That is what makes repeated asks of the same question
    return the same numbers.
    """
    snap = AN.get_snapshot(db)
    records = snap["records"] or []

    # The question's own wording wins over the UI selector: if someone types
    # "in the last 7 days", honour that even when the panel says All Time.
    plan = AN.route_query(req.query, records)
    effective_range = plan["timeframe"] or (req.range if req.scope_to_timeline else "all")
    window = AN.resolve_window(effective_range, req.anchor, snap.get("latest_ts"))
    scoped = AN.apply_window(records, window)

    computed = AN.compute_answer(plan, scoped, window)
    briefing = _cached_briefing(scoped, window, snap.get("version", 0))

    map_filter = {
        "ids": computed["map_ids"],
        "wards": [],
        "keywords": [],
        "category": plan.get("category") or "",
        "priority": "",
        "status": "",
        "zone": plan.get("zone") or "",
        "officer": plan.get("officer") or "",
        "range": window["key"],
        "label": computed["headline"] or window["label"],
    }
    range_payload = {
        "key": window["key"],
        "label": window["label"],
        "anchor": window.get("anchor"),
        "matched_timeframe": bool(plan["timeframe"]),
        "scoped_total": len(scoped),
        "grand_total": len(records),
    }

    # Deterministic fallback: even with every model unavailable the operator
    # still gets the real computed answer rather than an apology.
    fallback_response = (
        (("**" + computed["headline"] + "**\n\n") if computed["headline"] else "")
        + "UrbanOS AI narration is unavailable, but here is the computed answer from live data:\n\n"
        + "```text\n" + computed["facts"] + "\n```"
    )

    def _reply(text):
        return {
            "response": text,
            "chart": computed["chart"],
            "map_filter": map_filter,
            "headline": computed["headline"],
            "range": range_payload,
            "intent": plan["intent"],
        }

    if not gemini_client and not _groq_client:
        return _reply(fallback_response)

    sanctions, datasets = [], []
    try:
        for doc in db.collection('sanctioned_projects').stream():
            d = doc.to_dict()
            sanctions.append(f"- {d.get('category')} in {d.get('zone')}: {d.get('title')}")
        for doc in db.collection('custom_datasets').stream():
            d = doc.to_dict()
            datasets.append(f"--- DATASET: {d.get('filename')} ---\n{d.get('content')}\n")
    except Exception as e:
        logger.warning(f"Aux collections unavailable: {e}")

    sys_prompt = (
        "You are the UrbanOS civic intelligence analyst for Lucknow, Uttar Pradesh, India. "
        "You brief municipal administrators on citizen grievance data.\n\n"
        "=== HOW TO USE THIS PROMPT ===\n"
        "Every number you need has ALREADY BEEN COMPUTED for you from the live database and appears in "
        "COMPUTED FACTS and ANALYTICS BRIEFING below.\n"
        "1. NEVER calculate, estimate, re-count or re-rank anything yourself. Read the figures verbatim.\n"
        "2. COMPUTED FACTS is the direct answer to the question that was just asked. Lead with it.\n"
        "3. If COMPUTED FACTS says no records match, say exactly that. Never substitute a different window "
        "or invent a plausible number.\n"
        "4. When asked WHY, use the listed score drivers and their point contributions. The drivers are the "
        "real reason; do not invent causes beyond what the data shows.\n"
        "5. Quote ward names, officer names, dates and counts exactly as written.\n"
        "6. Anything not present in this prompt: reply 'I do not have data on that.'\n\n"
        "=== ANSWER FORMAT ===\n"
        "Open with a one-line direct answer in bold. Then a short evidence section: 3-6 bullets or a compact "
        "markdown table of the ranked figures (tables get an automatic CSV export button, so prefer a table "
        "whenever you are listing more than three rows). Close with one or two concrete recommended actions "
        "naming the responsible department. Keep it under 250 words unless asked for more.\n"
        "A CHART HAS ALREADY BEEN RENDERED beside the chat from these exact figures, and the matching "
        "complaints are ALREADY HIGHLIGHTED on the map. Refer to them ('see the chart on the right'); do NOT "
        "output chart code, ASCII charts or a JSON block. Use a ```mermaid``` block ONLY if the user explicitly "
        "asks for a process/flow diagram.\n\n"
        "=== NEUTRALITY ===\n"
        "Stay strictly factual and non-partisan. Officer 'dissatisfaction' and ward 'distress' are computed "
        "workload and outcome indicators, not personal or political judgements - say so when ranking people.\n\n"
        f"=== ACTIVE TIMELINE ===\n"
        f"{window['label']} | {range_payload['scoped_total']} of {range_payload['grand_total']} total reports "
        f"fall in this window. "
        + ("The user's question specified this timeframe explicitly.\n\n"
           if plan["timeframe"] else "This window comes from the operator's timeline selector.\n\n")
        + "=== COMPUTED FACTS (authoritative answer to this question) ===\n"
        + computed["facts"]
        + "\n\n=== ANALYTICS BRIEFING (whole window) ===\n"
        + briefing
        + "\n\n=== SAMPLE INDIVIDUAL REPORTS (evidence, not a complete list) ===\n"
        + _evidence_sample(scoped, computed["map_ids"])
        + "\n\n" + _static_reference()
        + ("\n--- SANCTIONED PROJECTS ---\n" + "\n".join(sanctions) if sanctions else "")
        + ("\n\n" + "\n".join(datasets) if datasets else "")
    )

    # Using Groq exclusively as requested
    if _groq_client:
        try:
            groq_msgs = [{"role": "system", "content": sys_prompt}]
            for h in req.history[-8:]:
                groq_msgs.append({"role": "user" if h.role == "user" else "assistant", "content": h.content})
            groq_msgs.append({"role": "user", "content": req.query})
            completion = _groq_client.chat.completions.create(
                messages=groq_msgs, model=GROQ_MODEL, max_tokens=2048, temperature=0.2,
            )
            return _reply(completion.choices[0].message.content.strip())
        except Exception as e:
            logger.error(f"Groq chat failed: {e}")
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=f"Groq API Error: {str(e)}")

    return _reply(fallback_response)


# ---------------------------------------------------------------------------
# FEED API
# ---------------------------------------------------------------------------
# Everything below reads from the shared analytics snapshot (analytics.py), so
# a burst of dashboard traffic costs one Firestore read per TTL rather than one
# per request. The feed is paginated and supports delta polling via `since`, so
# the client fetches the full history exactly once and then only receives new
# reports instead of re-downloading the whole collection every few seconds.

def _feed_item(r: dict) -> dict:
    """Slim projection for feed cards.

    Body text is trimmed for transport; full-text search still works because the
    search index in the snapshot is built over the untrimmed record.
    """
    return {
        "id": r["id"],
        "reference_id": r["reference_id"],
        "sender": r["sender"],
        "timestamp": r["ts_iso"],
        "category": r["category"],
        "semantic_tag": r["semantic_tag"],
        "summary": r["summary"],
        "raw_body": r["body"][:280],
        "status": r["status"],
        "priority": r["priority"],
        "sentiment": r["sentiment"],
        "polarity": r["polarity"],
        "location": r["location"],
        "location_source": r["location_source"],
        "latitude": r["latitude"],
        "longitude": r["longitude"],
        "constituency_zone": r["constituency_zone"],
        "ward_no": r["ward_no"],
        "ward_officer": r["ward_officer"],
        "responsible_body": r["responsible_body"],
        "estimated_budget": r["estimated_budget"],
        "media_urls": r["media_urls"],
        "media_type": r["media_type"],
        "original_language": r["original_language"],
        "is_urgent": r["is_urgent"],
        "visual_evidence": r["visual_evidence"],
    }


def _window_from_params(snap, range_key, anchor, start, end):
    return AN.resolve_window(range_key, anchor, snap.get("latest_ts"), start, end)


def _range_payload(window):
    return {
        "key": window["key"],
        "label": window["label"],
        "anchor": window.get("anchor"),
        "start": window["start"].isoformat() if window.get("start") else None,
        "end": window["end"].isoformat() if window.get("end") else None,
    }


@app.get("/api/feed")
async def api_feed(
    range: str = "all",
    anchor: str = "now",
    q: str = "",
    category: str = "",
    priority: str = "",
    status: str = "",
    ward: str = "",
    officer: str = "",
    zone: str = "",
    dept: str = "",
    since: str = "",
    limit: int = 80,
    offset: int = 0,
    start: str = "",
    end: str = "",
    db = Depends(get_db),
):
    """Paginated, filtered, timeline-scoped feed, newest first.

    Passing `since` turns this into a delta poll: hand it the newest timestamp
    you already hold and you get back only what arrived after it.
    """
    snap = AN.get_snapshot(db)
    records = snap["records"] or []
    window = _window_from_params(snap, range, anchor, start or None, end or None)

    scoped = AN.apply_window(records, window)
    filtered = AN.apply_filters(
        scoped,
        q=q or None,
        category=category or None,
        priority=priority or None,
        status=status or None,
        ward=ward or None,
        officer=officer or None,
        zone=zone or None,
        dept=dept or None,
    )

    if since:
        since_dt = AN.parse_ts(since)
        if since_dt:
            fresh = [r for r in filtered if r["timestamp"] and r["timestamp"] > since_dt]
            return JSONResponse(content={
                "mode": "delta",
                "items": [_feed_item(r) for r in fresh],
                "filtered_total": len(filtered),
                "new_count": len(fresh),
                "latest": snap["latest_ts"].isoformat() if snap.get("latest_ts") else None,
                "snapshot_version": snap.get("version"),
            })

    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    page = filtered[offset:offset + limit]

    return JSONResponse(content={
        "mode": "page",
        "items": [_feed_item(r) for r in page],
        "offset": offset,
        "limit": limit,
        "returned": len(page),
        "filtered_total": len(filtered),
        "window_total": len(scoped),
        "grand_total": len(records),
        "has_more": offset + len(page) < len(filtered),
        "next_offset": offset + len(page),
        "range": _range_payload(window),
        "latest": snap["latest_ts"].isoformat() if snap.get("latest_ts") else None,
        "earliest": snap["earliest_ts"].isoformat() if snap.get("earliest_ts") else None,
        "snapshot_version": snap.get("version"),
    })


@app.get("/api/feed/stats")
async def api_feed_stats(range: str = "all", anchor: str = "now", db = Depends(get_db)):
    """Header counts plus the size of every timeline window, so the control can
    show how much data sits behind each option before it is clicked."""
    snap = AN.get_snapshot(db)
    records = snap["records"] or []
    window = _window_from_params(snap, range, anchor, None, None)
    scoped = AN.apply_window(records, window)

    per_range = {}
    for key in ("7d", "month", "30d", "all"):
        w = AN.resolve_window(key, anchor, snap.get("latest_ts"))
        per_range[key] = len(AN.apply_window(records, w))

    return JSONResponse(content={
        "stats": AN.headline_stats(scoped),
        "range_counts": per_range,
        "range": _range_payload(window),
        "categories": AN.breakdown(scoped, "category"),
        "latest": snap["latest_ts"].isoformat() if snap.get("latest_ts") else None,
        "earliest": snap["earliest_ts"].isoformat() if snap.get("earliest_ts") else None,
    })


@app.get("/api/analytics")
async def api_analytics(
    range: str = "all",
    anchor: str = "now",
    category: str = "",
    ward: str = "",
    officer: str = "",
    zone: str = "",
    bucket: str = "day",
    db = Depends(get_db),
):
    """Deterministic analytics for the insights dashboards.

    These are the same numbers the RAG agent is briefed with, so the chat answer
    and the charts beside it can never disagree.
    """
    snap = AN.get_snapshot(db)
    records = snap["records"] or []
    window = _window_from_params(snap, range, anchor, None, None)
    scoped = AN.apply_filters(
        AN.apply_window(records, window),
        category=category or None,
        ward=ward or None,
        officer=officer or None,
        zone=zone or None,
    )
    return JSONResponse(content={
        "range": _range_payload(window),
        "headline": AN.headline_stats(scoped),
        "wards": AN.ward_performance(scoped)[:40],
        "officers": AN.officer_scorecard(scoped),
        "categories": AN.breakdown(scoped, "category"),
        "zones": AN.breakdown(scoped, "constituency_zone", limit=16),
        "departments": AN.breakdown(scoped, "responsible_body"),
        "priorities": AN.breakdown(scoped, "priority"),
        "statuses": AN.breakdown(scoped, "status"),
        "sentiments": AN.breakdown(scoped, "sentiment"),
        "trend": AN.trend_series(scoped, bucket if bucket in ("day", "week", "month") else "day"),
        "total": len(scoped),
    })


@app.get("/messages")
async def get_messages(
    range: str = "all",
    anchor: str = "now",
    limit: int = 0,
    db = Depends(get_db),
):
    """Compatibility endpoint: a plain array of reports, newest first.

    Kept array-shaped for existing callers (map markers, counters). Now backed by
    the shared snapshot and no longer capped at 200 records.
    """
    snap = AN.get_snapshot(db)
    records = snap["records"] or []
    window = _window_from_params(snap, range, anchor, None, None)
    scoped = AN.apply_window(records, window)
    if limit and limit > 0:
        scoped = scoped[:limit]
    return JSONResponse(content=[_feed_item(r) for r in scoped])


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
async def get_ranked_projects(db = Depends(get_db)):
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
async def sanction_project(req: SanctionRequest, db = Depends(get_db)):
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
        # Twilio answers media requests with a 307 to its CDN, so the redirect
        # has to be followed or every proxied image 500s.
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
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

def _run_asgi(asgi_app, req: https_fn.Request) -> https_fn.Response:
    """Bridge a Firebase Functions (WSGI-style) request into an ASGI app."""
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
    asyncio.run(asgi_app(scope, receive, send))

    return Response(b"".join(body_chunks), status=status_code[0], headers=headers_list)

@https_fn.on_request(memory=options.MemoryOption.GB_1, timeout_sec=300)
def api(req: https_fn.Request) -> https_fn.Response:
    return _run_asgi(app, req)

# WhatsApp sandbox: a Twilio-free replica of the intake flow for testing. It has
# its own function and hosting site, and only exposes /sim/* routes that write
# to sim_* collections. Deploy it alone with `firebase deploy --only functions:sandbox`.
@https_fn.on_request(memory=options.MemoryOption.GB_1, timeout_sec=300, secrets=["SIM_ACCESS_CODE"])
def sandbox(req: https_fn.Request) -> https_fn.Response:
    # Imported here: sim_api imports this module, so a top-level import would cycle.
    from sim_api import sim_app
    return _run_asgi(sim_app, req)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
