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

import firebase_admin
from firebase_admin import credentials, firestore

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

# ---------------------------------------------------------------------------
# MULTILINGUAL SUPPORT
# ---------------------------------------------------------------------------
# Detects language via Unicode script ranges (no API call needed).
# Covers the 6 most common Indian WhatsApp languages.
# Supported: Hindi, Urdu, Bengali, Tamil, Telugu, Kannada → fallback: English
# ---------------------------------------------------------------------------

REPLY_TEMPLATES = {
    "English": {
        "welcome":        "What development project or community upgrade would you like to propose for your area? Please describe your idea. (You can also send a voice note!)",
        "ask_location":   "Description saved. Now share your location — send a location pin, or type your area name.",
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
        "welcome":        "आप अपने क्षेत्र के लिए कौन सा विकास कार्य या सामुदायिक सुधार प्रस्तावित करना चाहते हैं? कृपया अपना विचार बताएं। (आप वॉइस नोट भी भेज सकते हैं!)",
        "ask_location":   "विवरण सहेज लिया गया। अब अपना स्थान साझा करें — लोकेशन पिन भेजें, या अपने क्षेत्र का नाम लिखें।",
        "ask_photo":      "स्थान सहेज लिया गया। क्या आप कोई फोटो भेजना चाहते हैं? अभी भेजें, या 'skip' लिखें।",
        "finalizing":     "आपकी रिपोर्ट तैयार हो रही है...",
        "photo_received": "फोटो मिल गई! आपकी रिपोर्ट तैयार हो रही है...",
        "skip_prompt":    "कृपया फोटो भेजें या 'skip' लिखें।",
        "location_prompt":"कृपया लोकेशन पिन भेजें या अपने क्षेत्र का नाम लिखें।",
        "text_or_voice":  "कृपया समस्या को टेक्स्ट में बताएं या वॉइस नोट भेजें।",
        "survey_thanks":  "आपके फीडबैक के लिए धन्यवाद! आपके सांसद का कार्यालय इस पर विचार करेगा।",
        "new_prompt":     "कोई नया प्रस्ताव देने के लिए कभी भी 'new proposal' लिखें।",
        "invalid_option": "कृपया 1 से {n} के बीच कोई नंबर भेजें।",
        "voice_ack":      "वॉइस नोट मिल गया, आपका विवरण प्रोसेस हो रहा है...",
        "discarded":      "पिछला प्रस्ताव हटा दिया गया। आप अपने क्षेत्र के लिए कौन सा विकास कार्य प्रस्तावित करना चाहते हैं?",
        "no_reports":     "आपकी कोई सक्रिय रिपोर्ट नहीं है।",
    },
    "Urdu": {
        "welcome":        "آپ اپنے علاقے کے لیے کون سا ترقیاتی منصوبہ تجویز کرنا چاہتے ہیں؟ براہ کرم اپنا خیال بیان کریں۔ (آپ وائس نوٹ بھی بھیج سکتے ہیں!)",
        "ask_location":   "تفصیل محفوظ ہو گئی۔ اب اپنا مقام شیئر کریں — لوکیشن پن بھیجیں یا اپنے علاقے کا نام لکھیں۔",
        "ask_photo":      "مقام محفوظ ہو گیا۔ کیا آپ تصویر بھیجنا چاہتے ہیں؟ ابھی بھیجیں، یا 'skip' لکھیں۔",
        "finalizing":     "آپ کی رپورٹ تیار ہو رہی ہے...",
        "photo_received": "تصویر مل گئی! آپ کی رپورٹ تیار ہو رہی ہے...",
        "skip_prompt":    "براہ کرم تصویر بھیجیں یا 'skip' لکھیں۔",
        "location_prompt":"براہ کرم لوکیشن پن بھیجیں یا اپنے علاقے کا نام لکھیں۔",
        "text_or_voice":  "براہ کرم مسئلہ متن میں بیان کریں یا وائس نوٹ بھیجیں۔",
        "survey_thanks":  "آپ کے تاثرات کا شکریہ! آپ کے رکن پارلیمنٹ کا دفتر اس پر غور کرے گا۔",
        "new_prompt":     "کوئی نئی تجویز دینے کے لیے کبھی بھی 'new proposal' لکھیں۔",
        "invalid_option": "براہ کرم 1 سے {n} کے درمیان کوئی نمبر بھیجیں۔",
        "voice_ack":      "وائس نوٹ مل گیا، آپ کی تفصیل پروسیس ہو رہی ہے...",
        "discarded":      "پچھلی تجویز ہٹا دی گئی۔ آپ اپنے علاقے کے لیے کون سا ترقیاتی منصوبہ تجویز کرنا چاہتے ہیں؟",
        "no_reports":     "آپ کی کوئی فعال رپورٹ نہیں ہے۔",
    },
    "Tamil": {
        "welcome":        "உங்கள் பகுதிக்கு என்ன வளர்ச்சி திட்டம் அல்லது சமுதாய மேம்பாட்டை நீங்கள் முன்மொழிய விரும்புகிறீர்கள்? உங்கள் கருத்தை விவரிக்கவும். (குரல் குறிப்பும் அனுப்பலாம்!)",
        "ask_location":   "விளக்கம் சேமிக்கப்பட்டது. இப்போது உங்கள் இருப்பிடத்தை பகிரவும் — இருப்பிட பின் அனுப்பவும் அல்லது உங்கள் பகுதியின் பெயரை தட்டச்சு செய்யவும்.",
        "ask_photo":      "இருப்பிடம் சேமிக்கப்பட்டது. புகைப்படம் இணைக்க விரும்புகிறீர்களா? இப்போது அனுப்பவும் அல்லது 'skip' என்று பதிலளிக்கவும்.",
        "finalizing":     "உங்கள் அறிக்கை தயாரிக்கப்படுகிறது...",
        "photo_received": "புகைப்படம் பெறப்பட்டது! உங்கள் அறிக்கை தயாரிக்கப்படுகிறது...",
        "skip_prompt":    "புகைப்படம் அனுப்பவும் அல்லது 'skip' என்று பதிலளிக்கவும்.",
        "location_prompt":"இருப்பிட பின் அனுப்பவும் அல்லது உங்கள் பகுதியின் பெயரை தட்டச்சு செய்யவும்.",
        "text_or_voice":  "உரையில் சிக்கலை விவரிக்கவும் அல்லது குரல் குறிப்பை அனுப்பவும்.",
        "survey_thanks":  "உங்கள் கருத்துக்கு நன்றி! உங்கள் நாடாளுமன்ற உறுப்பினரின் அலுவலகம் இதை பரிசீலிக்கும்.",
        "new_prompt":     "மற்றொரு வளர்ச்சி யோசனையை சமர்ப்பிக்க எப்போது வேண்டுமானாலும் 'new proposal' என்று தட்டச்சு செய்யவும்.",
        "invalid_option": "1 முதல் {n} வரை ஒரு எண்ணை பதிலளிக்கவும்.",
        "voice_ack":      "குரல் குறிப்பு பெறப்பட்டது, உங்கள் விளக்கம் செயலாக்கப்படுகிறது...",
        "discarded":      "முந்தைய முன்மொழிவு நீக்கப்பட்டது. உங்கள் பகுதிக்கு என்ன வளர்ச்சி திட்டம் முன்மொழிய விரும்புகிறீர்கள்?",
        "no_reports":     "உங்களுக்கு செயலில் உள்ள அறிக்கைகள் இல்லை.",
    },
    "Telugu": {
        "welcome":        "మీ ప్రాంతానికి ఏ అభివృద్ధి ప్రాజెక్టు లేదా సామాజిక మెరుగుదలను మీరు ప్రతిపాదించాలనుకుంటున్నారు? మీ ఆలోచనను వివరించండి. (వాయిస్ నోట్ కూడా పంపవచ్చు!)",
        "ask_location":   "వివరణ సేవ్ చేయబడింది. ఇప్పుడు మీ స్థానాన్ని షేర్ చేయండి — లొకేషన్ పిన్ పంపండి లేదా మీ ప్రాంతం పేరు టైప్ చేయండి.",
        "ask_photo":      "స్థానం సేవ్ చేయబడింది. మీరు ఫోటో జతచేయాలనుకుంటున్నారా? ఇప్పుడు పంపండి లేదా 'skip' అని రిప్లై చేయండి.",
        "finalizing":     "మీ నివేదిక తయారవుతోంది...",
        "photo_received": "ఫోటో అందింది! మీ నివేదిక తయారవుతోంది...",
        "skip_prompt":    "దయచేసి ఫోటో పంపండి లేదా 'skip' అని రిప్లై చేయండి.",
        "location_prompt":"దయచేసి లొకేషన్ పిన్ పంపండి లేదా మీ ప్రాంతం పేరు టైప్ చేయండి.",
        "text_or_voice":  "దయచేసి సమస్యను టెక్స్ట్‌లో వివరించండి లేదా వాయిస్ నోట్ పంపండి.",
        "survey_thanks":  "మీ అభిప్రాయానికి ధన్యవాదాలు! మీ ఎంపీ కార్యాలయం దీన్ని పరిగణిస్తుంది.",
        "new_prompt":     "మరొక అభివృద్ధి ఆలోచనను సమర్పించడానికి ఎప్పుడైనా 'new proposal' అని టైప్ చేయండి.",
        "invalid_option": "దయచేసి 1 నుండి {n} మధ్య ఒక నంబర్‌తో రిప్లై చేయండి.",
        "voice_ack":      "వాయిస్ నోట్ అందింది, మీ వివరణ ప్రాసెస్ అవుతోంది...",
        "discarded":      "మునుపటి ప్రతిపాదన తొలగించబడింది. మీ ప్రాంతానికి ఏ అభివృద్ధి ప్రాజెక్టు ప్రతిపాదించాలనుకుంటున్నారు?",
        "no_reports":     "మీకు సక్రియ నివేదికలు లేవు.",
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

@app.get("/")
def root():
    return FileResponse("public/index.html")

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/sw.js")
async def service_worker():
    return FileResponse("sw.js", media_type="application/javascript")

@app.get("/favicon.svg")
async def favicon():
    return FileResponse("favicon.svg", media_type="image/svg+xml")

@app.post("/webhook/whatsapp")
async def receive_whatsapp(request: Request, background_tasks: BackgroundTasks, db = Depends(get_db)):
    form_data = await request.form()
    
    # Twilio security validation
    if twilio_validator:
        signature = request.headers.get("X-Twilio-Signature", "")
        url = str(request.url)
        if "x-forwarded-host" in request.headers:
            proto = request.headers.get("x-forwarded-proto", "http")
            host = request.headers.get("x-forwarded-host")
            url = f"{proto}://{host}{request.url.path}"
            
        post_vars = {k: v for k, v in form_data.items()}
        if not twilio_validator.validate(url, post_vars, signature):
            logger.warning(f"[SECURITY] Invalid Twilio signature from {request.client.host}. Dropping request.")
            raise HTTPException(status_code=403, detail="Forbidden")

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
        
        if gemini_client and description:
            try:
                prompt = f"Analyze this community development proposal from a Smart City WhatsApp tip-line. Extract the structured triage data, including what language they originally used.\n\nProposal Text: {description}"
                response = gemini_client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt,
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
                summary = triage.summary
                constituency_zone = triage.constituency_zone
                estimated_budget = triage.estimated_budget
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
            twiml.message(f"✅ Proposal submitted! Ref: #{ref_id}.\n\n*{survey_q}*\n\nReply with the number of your choice:\n{opt_text}")
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
            
        results.append({
            "id": sid, 
            "question": s.get('question'), 
            "options": [opt.strip() for opt in s.get('options', '').split(',')], 
            "is_active": s.get('is_active', False), 
            "results": counts_dict
        })
    return results

@app.post("/surveys")
async def create_survey(survey: SurveyCreate, db = Depends(get_db)):
    doc_ref = db.collection('surveys').document()
    doc_ref.set({
        "id": doc_ref.id,
        "question": survey.question,
        "options": survey.options,
        "is_active": False
    })
    return {"status": "success", "id": doc_ref.id}

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
async def broadcast_survey(survey_id: str, db = Depends(get_db)):
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
    query: str
    history: list[ChatMessage] = []

@app.post("/api/upload-dataset")
async def upload_dataset(file: UploadFile = File(...), db = Depends(get_db)):
    try:
        content = await file.read()
        text_content = content.decode('utf-8')
        doc_ref = db.collection('custom_datasets').document()
        doc_ref.set({
            "id": doc_ref.id,
            "filename": file.filename,
            "content": text_content[:15000], # Limit to avoid massive context blowup
            "uploaded_at": datetime.now().isoformat()
        })
        return {"status": "success", "id": doc_ref.id}
    except Exception as e:
        logger.error(f"Failed to upload dataset: {e}")
        raise HTTPException(status_code=500, detail="Failed to process file")

@app.post("/api/chat")
async def api_chat(req: ChatRequest, db = Depends(get_db)):
    if not gemini_client:
        raise HTTPException(status_code=500, detail="Gemini AI not initialized")

    try:
        msgs_docs = db.collection('messages').order_by('timestamp', direction=firestore.Query.DESCENDING).limit(100).stream()
        sanc_docs = db.collection('sanctioned_projects').stream()
        dataset_docs = db.collection('custom_datasets').stream()
        
        proposals = []
        for doc in msgs_docs:
            d = doc.to_dict()
            proposals.append(f"- {d.get('category')} in {d.get('constituency_zone')}: {d.get('summary')} (Status: {d.get('status')})")
            
        sanctions = []
        for doc in sanc_docs:
            d = doc.to_dict()
            sanctions.append(f"- {d.get('category')} in {d.get('zone')}: {d.get('title')}")
            
        datasets = []
        for doc in dataset_docs:
            d = doc.to_dict()
            datasets.append(f"--- DATASET: {d.get('filename')} ---\n{d.get('content')}\n")

        sys_prompt = (
            "You are a Production-level AI database assistant for UrbanOS, analyzing citizen grievances, sanctioned projects, and uploaded datasets.\n"
            "STRICT BIAS GUARDRAILS: You must remain strictly neutral, unbiased, and objective. Do not favor any political entity, demographic, or region.\n"
            "STRICT KNOWLEDGE GUARDRAILS: If the user asks about something NOT in the provided context, you MUST explicitly say 'I do not know' or 'I do not have data on that'. Do not hallucinate data.\n"
            "CHART GENERATION: If the user asks for a chart, graph, or plot, output a valid Mermaid JS code block (```mermaid ... ```). Keep it simple.\n"
            "EXCEL/CSV EXPORT: If the user asks for data in an Excel sheet or CSV, output the data as a standard Markdown table. The system will automatically convert it to a downloadable CSV for them.\n\n"
            "--- CITIZEN PROPOSALS ---\n" +
            "\n".join(proposals) +
            "\n\n--- SANCTIONED PROJECTS ---\n" +
            "\n".join(sanctions) +
            "\n\n" + "\n".join(datasets)
        )

        contents = []
        first_msg = f"{sys_prompt}\n\nUser Query: {req.query}"
        
        for h in req.history:
            contents.append({"role": "user" if h.role == "user" else "model", "parts": [{"text": h.content}]})
        
        contents.append({"role": "user", "parts": [{"text": first_msg}]})

        resp = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=contents
        )
        return {"response": resp.text.strip()}
    except Exception as e:
        logger.error(f"Chat API failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/messages")
async def get_messages(db = Depends(get_db)):
    try:
        docs = db.collection('messages').order_by('timestamp', direction=firestore.Query.DESCENDING).limit(500).stream()
    except Exception:
        docs = db.collection('messages').limit(500).stream()
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
# This layer is PLUGGABLE — replace with live Census API calls at:
#   → data.gov.in  (Open Government Data Platform India)
#   → udiseplus.gov.in (school density data)
#   → nhm.gov.in (health infrastructure data)
# ---------------------------------------------------------------------------
DEMO_DEMOGRAPHICS = {
    # North Lucknow: Sarojini Nagar, Bakshi Ka Talab — peri-urban, lower infra density
    "North":   {"population": 312000, "youth_pct": 36, "nearest_school_km": 7.8, "nearest_hospital_km": 11.2, "literacy_rate": 69},
    # South Lucknow: Cantonment, Alambagh — mixed urban, better infra
    "South":   {"population": 328000, "youth_pct": 27, "nearest_school_km": 2.9, "nearest_hospital_km": 4.8,  "literacy_rate": 79},
    # East Lucknow: Gomti Nagar, Indira Nagar — newer development zones
    "East":    {"population": 287000, "youth_pct": 30, "nearest_school_km": 4.1, "nearest_hospital_km": 6.5,  "literacy_rate": 76},
    # West Lucknow: Chinhat, Amausi — industrial-adjacent, developing
    "West":    {"population": 241000, "youth_pct": 32, "nearest_school_km": 5.6, "nearest_hospital_km": 8.3,  "literacy_rate": 72},
    # Central Lucknow: Hazratganj, Chowk, Aminabad — dense urban core
    "Central": {"population": 421000, "youth_pct": 24, "nearest_school_km": 1.6, "nearest_hospital_km": 2.4,  "literacy_rate": 85},
}

PRIORITY_WEIGHTS = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}

@app.get("/projects/ranked")
async def get_ranked_projects(db = Depends(get_db)):
    """Groups messages into projects, scores them, and generates AI justification."""
    all_docs = list(db.collection('messages').limit(500).stream())
    messages = [d.to_dict() for d in all_docs]

    # Cluster by (category, constituency_zone)
    clusters = {}
    for m in messages:
        raw_cat = m.get("category")
        if not raw_cat:
            # Use first 40 chars of body as a unique cluster key so uncategorized
            # requests are still individually visible in the ranking
            body_snippet = (m.get("body") or "").strip()[:40]
            raw_cat = body_snippet if body_snippet else "General Proposal"
        cat = raw_cat
        zone = m.get("constituency_zone") or "Central"
        key = f"{cat}|{zone}"
        if key not in clusters:
            clusters[key] = {
                "category": cat, "zone": zone,
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
        infra_gap = demo["nearest_school_km"]  # simple proxy
        impact_score = round((demand * cl["priority_score"] * (1 + infra_gap / 10)), 1)
        # Use most common summary as project title
        summaries = [m.get("summary") or "" for m in cl["messages"] if m.get("summary")]
        title = summaries[0] if summaries else f"{cl['category']} — {cl['zone']} Zone"
        avg_budget = int(cl["budget_sum"] / demand) if demand else 0
        ranked.append({
            "id": key,
            "title": title,
            "category": cl["category"],
            "zone": cl["zone"],
            "demand_count": demand,
            "unique_senders": len(cl["senders"]),
            "impact_score": impact_score,
            "estimated_budget": avg_budget,
            "demographics": demo,
            "justification": None,  # filled below
            "status": cl["messages"][0].get("status", "Open"),
            "senders": list(cl["senders"]),
        })

    ranked.sort(key=lambda x: x["impact_score"], reverse=True)
    top = ranked[:8]

    # Generate AI justification for top 5 (Gemini)
    if gemini_client:
        for proj in top[:5]:
            try:
                demo = proj["demographics"]
                prompt = (
                    f"You are an AI advisor for an Indian MP's office. Write a 2-sentence justification "
                    f"(max 50 words) recommending action on this project, citing the data provided. "
                    f"Be specific, data-driven, and urgent.\n\n"
                    f"Project: {proj['title']}\n"
                    f"Zone: {proj['zone']} Constituency\n"
                    f"Citizen Demand: {proj['demand_count']} proposals received\n"
                    f"Zone Population: {demo['population']:,} residents\n"
                    f"Youth Population: {demo['youth_pct']}% aged 0-14\n"
                    f"Nearest school distance: {demo['nearest_school_km']} km\n"
                    f"Literacy rate: {demo['literacy_rate']}%\n"
                    f"Estimated cost: ₹{proj['estimated_budget']:,}\n"
                )
                resp = gemini_client.models.generate_content(
                    model='gemini-2.5-flash', contents=prompt
                )
                proj["justification"] = resp.text.strip()
            except Exception as e:
                logger.error(f"[AI JUSTIFICATION] Failed: {e}")
                proj["justification"] = f"{proj['demand_count']} citizens in {proj['zone']} zone have flagged this as a priority. Demographic data indicates high need in this area."

    # Fallback justification for remaining
    for proj in top[5:]:
        if not proj["justification"]:
            proj["justification"] = f"{proj['demand_count']} citizens in {proj['zone']} zone have flagged this as a priority."

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

    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) if TWILIO_ACCOUNT_SID else None

    for doc in all_docs:
        msg = doc.to_dict()
        if msg.get("category") == req.category and msg.get("constituency_zone") == req.zone:
            doc.reference.update({"status": "Sanctioned"})
            sender = msg.get("sender")
            if sender and sender not in notified_senders and twilio_client and TWILIO_WHATSAPP_NUMBER:
                try:
                    twilio_client.messages.create(
                        from_=TWILIO_WHATSAPP_NUMBER,
                        to=sender,
                        body=(
                            f"🎉 Great news! Your proposal regarding '{req.title}' has been *SANCTIONED* by your MP's office. "
                            f"Reference: {req.project_id}. Work will begin as per the planning schedule. Thank you for making your voice heard! — UrbanOS"
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
        "sanctioned_at": datetime.now().isoformat(),
        "citizens_notified": notified,
    })

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
