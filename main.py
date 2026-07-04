import os
import httpx
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException, Depends, BackgroundTasks
from twilio.twiml.messaging_response import MessagingResponse
from state import get_session, update_session, clear_session
from worker import process_voice_note
import json
import logging
import asyncio
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from twilio.request_validator import RequestValidator

from sqlalchemy import create_engine, Column, Integer, String, Text, Float
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from google import genai
from pydantic import BaseModel, Field

load_dotenv()

# --- LOGGING SETUP ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# --- DATABASE SETUP ---
DATABASE_URL = "sqlite:///./urbanos.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Message(Base):
    __tablename__ = "messages"
    
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(String, index=True)
    sender = Column(String, index=True)
    body = Column(Text, nullable=True)
    media_url = Column(String, nullable=True)
    media_content_type = Column(String, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    location_source = Column(String, nullable=True)
    
    # AI Triage Fields
    category = Column(String, nullable=True)
    priority = Column(String, nullable=True)
    sentiment = Column(String, nullable=True)
    extracted_location = Column(String, nullable=True)
    summary = Column(String, nullable=True)
    original_language = Column(String, nullable=True)
    reference_id = Column(String, nullable=True)
    status = Column(String, default="Open")

class ConversationSession(Base):
    __tablename__ = "sessions"
    
    id = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String, unique=True, index=True)
    current_step = Column(String)
    collected_data = Column(Text) # JSON string

# Ensure all models are created
Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
# ----------------------

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
twilio_validator = RequestValidator(TWILIO_AUTH_TOKEN) if TWILIO_AUTH_TOKEN else None

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

class TriageResult(BaseModel):
    category: str = Field(description="Must be one of: Education, Healthcare, Public Transport, Community Spaces, Infrastructure Upgrade, Other")
    priority: str = Field(description="Must be one of: Low, Medium, High, Critical")
    sentiment: str = Field(description="Must be one of: Neutral, Frustrated, Angry, Urgent")
    extracted_location: str = Field(description="Street name, landmark, or area extracted from text. Empty string if none.")
    summary: str = Field(description="Short 4-5 word summary title of the project proposal.")
    original_language: str = Field(description="The language the user originally submitted their request in (e.g., Hindi, English, Spanish).")

@app.get("/")
async def root():
    return FileResponse("index.html")

@app.get("/sw.js")
async def service_worker():
    return FileResponse("sw.js", media_type="application/javascript")

@app.get("/favicon.svg")
async def favicon():
    return FileResponse("favicon.svg", media_type="image/svg+xml")

@app.post("/webhook/whatsapp")
async def receive_whatsapp(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
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

def _process_whatsapp_sync(form_data, background_tasks: BackgroundTasks, db: Session):
    sender = form_data.get("From", "")
    body = form_data.get("Body", "").strip()
    media_url = form_data.get("MediaUrl0")
    media_type = form_data.get("MediaContentType0", "")
    lat_str = form_data.get("Latitude")
    lng_str = form_data.get("Longitude")
    
    twiml = MessagingResponse()
    
    # Check for commands
    body_lower = body.lower()
    if body_lower.startswith("status"):
        if body_lower == "status":
            reports = db.query(Message).filter(Message.sender == sender, Message.reference_id != None).order_by(Message.id.desc()).limit(3).all()
            if not reports:
                twiml.message("You have no active reports.")
                return HTMLResponse(content=str(twiml), media_type="application/xml")
            
            msg = "Your Recent Reports:\\n"
            for r in reports:
                msg += f"#{r.reference_id} - {r.category or 'General'}\\nStatus: {r.status}\\n\\n"
            twiml.message(msg.strip())
            return HTMLResponse(content=str(twiml), media_type="application/xml")
        else:
            parts = body_lower.split()
            if len(parts) > 1:
                ref_id = parts[1].strip('#')
                report = db.query(Message).filter(Message.reference_id == ref_id).first()
                if report:
                    twiml.message(f"Report #{report.reference_id}\\nCategory: {report.category or 'General'}\\nStatus: {report.status}\\nSummary: {report.summary}")
                else:
                    twiml.message(f"Could not find report #{ref_id}.")
                return HTMLResponse(content=str(twiml), media_type="application/xml")

    if body_lower == "new proposal" or body_lower == "new report":
        clear_session(db, sender)
        twiml.message("Previous proposal discarded. What development project or community upgrade would you like to propose for your area? Please describe your idea.")
        update_session(db, sender, "awaiting_description", {})
        return HTMLResponse(content=str(twiml), media_type="application/xml")

    # State Machine Logic
    session = get_session(db, sender)
    current_step = session.current_step if session else None
    
    if current_step is None:
        twiml.message("What development project or community upgrade would you like to propose for your area? Please describe your idea. (You can also send a voice note!)")
        update_session(db, sender, "awaiting_description", {})
        return HTMLResponse(content=str(twiml), media_type="application/xml")
        
    elif current_step == "awaiting_description":
        if "audio" in media_type and media_url:
            twiml.message("Voice note received, processing your description now...")
            background_tasks.add_task(process_voice_note, media_url, sender)
            return HTMLResponse(content=str(twiml), media_type="application/xml")
        
        elif body:
            update_session(db, sender, "awaiting_location", {"description": body})
            twiml.message("Description saved. Now share your location — send a location pin, or type your area name.")
            return HTMLResponse(content=str(twiml), media_type="application/xml")
        else:
            twiml.message("Please describe the issue in text or send a voice note.")
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
            twiml.message("Location saved. Would you like to attach a photo? Send it now, or reply 'skip' if not.")
            return HTMLResponse(content=str(twiml), media_type="application/xml")
        else:
            twiml.message("Please send a location pin or type your area name.")
            return HTMLResponse(content=str(twiml), media_type="application/xml")
            
    elif current_step == "awaiting_photo":
        if "image" in media_type and media_url:
            update_session(db, sender, "finalize", {"photo_url": media_url, "photo_type": media_type})
            twiml.message("Photo received! Finalizing your report...")
        elif body_lower == "skip":
            update_session(db, sender, "finalize", {})
            twiml.message("Finalizing your report...")
        else:
            twiml.message("Please send a photo or reply 'skip'.")
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
                
        # Generate Reference ID
        count = db.query(Message).count() + 1
        ref_id = f"UO{count:04d}"
        
        # AI Triage
        category = None
        priority = None
        sentiment = None
        extracted_location = location_raw if loc_source == "text" else None
        summary = None
        original_language = None
        
        if gemini_client and description:
            try:
                prompt = f"Analyze this community development proposal from a Smart City WhatsApp tip-line. Extract the structured triage data, including what language they originally used.\\n\\nProposal Text: {description}"
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
                sentiment = triage.sentiment
                original_language = triage.original_language

                if loc_source == "text":
                    extracted_location = triage.extracted_location or location_raw
                summary = triage.summary
            except Exception as e:
                logger.error(f"[AI ERROR] Gemini Triage Failed: {e}", exc_info=True)
                
        new_message = Message(
            timestamp=datetime.now().isoformat(),
            sender=sender,
            body=description,
            media_url=photo_url or data.get("voice_url"),
            media_content_type=data.get("photo_type") or ("audio/ogg" if data.get("voice_url") else None),
            latitude=lat,
            longitude=lng,
            location_source=loc_source,
            category=category,
            priority=priority,
            sentiment=sentiment,
            extracted_location=extracted_location,
            summary=summary,
            original_language=original_language,
            reference_id=ref_id,
            status="Open"
        )
        
        db.add(new_message)
        clear_session(db, sender)
        db.commit()
        
        twiml.message(f"Proposal submitted successfully. Reference ID: #{ref_id}. Your MP's office has received this and it will be reviewed shortly.")
        logger.info(f"Proposal {ref_id} successfully created for {sender}.")
        return HTMLResponse(content=str(twiml), media_type="application/xml")
    
    return HTMLResponse(content=str(twiml), media_type="application/xml")

@app.get("/messages")
async def get_messages(db: Session = Depends(get_db)):
    records = db.query(Message).order_by(Message.id.desc()).limit(500).all()
    
    messages = [
        {
            "id": r.id,
            "timestamp": r.timestamp,
            "from": r.sender,
            "body": r.body,
            "media_url": r.media_url,
            "media_content_type": r.media_content_type,
            "latitude": r.latitude,
            "longitude": r.longitude,
            "location_source": r.location_source,
            "category": r.category,
            "priority": r.priority,
            "sentiment": r.sentiment,
            "extracted_location": r.extracted_location,
            "summary": r.summary,
            "original_language": r.original_language
        }
        for r in records
    ]
    return JSONResponse(content=messages)

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
