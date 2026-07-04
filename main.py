import os
import httpx
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException, Depends, BackgroundTasks
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

class ConversationSession(Base):
    __tablename__ = "sessions"
    
    id = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String, unique=True, index=True)
    current_step = Column(String)
    collected_data = Column(Text) # JSON string

# Ensure all models are created


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

TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
twilio_validator = RequestValidator(TWILIO_AUTH_TOKEN) if TWILIO_AUTH_TOKEN else None

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

class TriageResult(BaseModel):
    category: str = Field(description="Must be one of: Construction Development, Land Development, Infrastructure Upgrade, Public Utility Works, Environmental Project, Maintenance & Repair, Other")
    priority: str = Field(description="Must be one of: Low, Medium, High, Critical")
    feasibility: str = Field(alias="sentiment", description="Must be one of: High, Moderate, Complex, Unknown. Maps to the old sentiment DB column.")
    extracted_location: str = Field(description="Street name, landmark, or area extracted from text. Empty string if none.")
    summary: str = Field(description="Short 4-5 word summary title of the project proposal.")
    original_language: str = Field(description="The language the user originally submitted their request in (e.g., Hindi, English, Spanish).")
    constituency_zone: str = Field(description="Must be one of: North, South, East, West, Central. Infer this constituency zone from the location context. Default to Central if unknown.")
    estimated_budget: int = Field(description="An integer representing the estimated cost in INR based on the project scale (e.g., 500000 for small, 50000000 for large capital works).")

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
            reports = [doc.to_dict() for doc in db.collection('messages').where('sender', '==', sender).order_by('id', direction=firestore.Query.DESCENDING).limit(3).stream()]
            if not reports:
                twiml.message("You have no active reports.")
                return HTMLResponse(content=str(twiml), media_type="application/xml")
            
            msg = "Your Recent Reports:\\n"
            for r in reports:
                msg += f"#{r.get('reference_id')} - {r.get('category') or 'General'}\\nStatus: {r.get('status')}\\n\\n"
            twiml.message(msg.strip())
            return HTMLResponse(content=str(twiml), media_type="application/xml")
        else:
            parts = body_lower.split()
            if len(parts) > 1:
                ref_id = parts[1].strip('#')
                report_docs = list(db.collection('messages').where('reference_id', '==', ref_id).limit(1).stream())
                report = report_docs[0].to_dict() if report_docs else None
                if report:
                    twiml.message(f"Report #{report.get('reference_id')}\\nCategory: {report.get('category') or 'General'}\\nStatus: {report.get('status')}\\nSummary: {report.get('summary')}")
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
        if body and body.strip().isdigit():
            survey_docs = list(db.collection('surveys').where('is_active', '==', True).limit(1).stream())
            active_survey = survey_docs[0].to_dict() if survey_docs else None
            if active_survey:
                options = [opt.strip() for opt in active_survey.get('options').split(',')]
                try:
                    choice_idx = int(body.strip()) - 1
                    if 0 <= choice_idx < len(options):
                        choice = options[choice_idx]
                        db.add(SurveyResponse(survey_id=active_survey.get('id'), sender=sender, selected_option=choice))
                        db.commit()
                        twiml.message("Thank you! Your feedback has been recorded.")
                        return HTMLResponse(content=str(twiml), media_type="application/xml")
                except Exception:
                    pass
        
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
        constituency_zone = None
        estimated_budget = None
        
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
                feasibility = triage.feasibility
                original_language = triage.original_language

                if loc_source == "text":
                    extracted_location = triage.extracted_location or location_raw
                summary = triage.summary
                constituency_zone = triage.constituency_zone
                estimated_budget = triage.estimated_budget
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
            sentiment=feasibility,
            extracted_location=extracted_location,
            summary=summary,
            original_language=original_language,
            constituency_zone=constituency_zone,
            estimated_budget=estimated_budget,
            reference_id=ref_id,
            status="Open"
        )
        
        db.add(new_message)
        db.commit()
        
        survey_docs = list(db.collection('surveys').where('is_active', '==', True).limit(1).stream())
            active_survey = survey_docs[0].to_dict() if survey_docs else None
        if active_survey:
            update_session(db, sender, "awaiting_survey", {"survey_id": active_survey.get('id')})
            options = [opt.strip() for opt in active_survey.get('options').split(',')]
            opt_text = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(options)])
            twiml.message(f"Proposal submitted successfully! Ref: #{ref_id}.\n\nBy the way, your MP wants to know:\n*{active_survey.get('question')}*\n\nReply with the number of your choice:\n{opt_text}")
        else:
            clear_session(db, sender)
            twiml.message(f"Proposal submitted successfully. Reference ID: #{ref_id}. Your MP's office has received this and it will be reviewed shortly.")
            
        logger.info(f"Proposal {ref_id} successfully created for {sender}.")
        return HTMLResponse(content=str(twiml), media_type="application/xml")
    
    return HTMLResponse(content=str(twiml), media_type="application/xml")


class SurveyCreate(BaseModel):
    question: str
    options: str

@app.get("/surveys")
async def get_surveys(db: Session = Depends(get_db)):
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
async def create_survey(survey: SurveyCreate, db: Session = Depends(get_db)):
    new_survey = Survey(question=survey.get('question'), options=survey.get('options'), is_active=False)
    db.add(new_survey)
    db.commit()
    return {"status": "success"}

@app.post("/surveys/{survey_id}/activate")
async def activate_survey(survey_id: int, db: Session = Depends(get_db)):
    # Deactivate all
    docs = db.collection('surveys').where('is_active', '==', True).stream()
    for doc in docs:
        doc.reference.update({"is_active": False})
    # Activate one
    db.collection('surveys').document(str(survey_id)).update({"is_active": True})
    return {"status": "success"}


@app.post("/surveys/{survey_id}/broadcast")
async def broadcast_survey(survey_id: int, db: Session = Depends(get_db)):
    survey_doc = db.collection('surveys').document(str(survey_id)).get()
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
            "category": r.get('category'),
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
