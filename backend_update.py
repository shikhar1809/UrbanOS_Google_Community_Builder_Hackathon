import os

def update_main():
    code = """import os
import httpx
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
from dotenv import load_dotenv
from twilio.request_validator import RequestValidator

from sqlalchemy import create_engine, Column, Integer, String, Text, Float
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from google import genai
from pydantic import BaseModel, Field

load_dotenv()

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

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
# ----------------------

app = FastAPI()

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
twilio_validator = RequestValidator(TWILIO_AUTH_TOKEN) if TWILIO_AUTH_TOKEN else None

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

class TriageResult(BaseModel):
    category: str = Field(description="Must be one of: Cleanliness, Water, Power, Roads, Emergency, Other")
    priority: str = Field(description="Must be one of: Low, Medium, High, Critical")
    sentiment: str = Field(description="Must be one of: Neutral, Frustrated, Angry, Urgent")
    extracted_location: str = Field(description="Street name, landmark, or area extracted from text. Empty string if none.")
    summary: str = Field(description="Short 4-5 word summary title of the issue.")

@app.get("/")
async def root():
    return FileResponse("index.html")

@app.get("/sw.js")
async def service_worker():
    return FileResponse("sw.js", media_type="application/javascript")

@app.post("/webhook/whatsapp")
async def receive_whatsapp(request: Request, db: Session = Depends(get_db)):
    form_data = await request.form()
    
    if twilio_validator:
        signature = request.headers.get("X-Twilio-Signature", "")
        url = str(request.url)
        if "x-forwarded-host" in request.headers:
            proto = request.headers.get("x-forwarded-proto", "http")
            host = request.headers.get("x-forwarded-host")
            url = f"{proto}://{host}{request.url.path}"
            
        post_vars = {k: v for k, v in form_data.items()}
        if not twilio_validator.validate(url, post_vars, signature):
            print(f"[SECURITY ALERT] Invalid Twilio signature from {request.client.host}. Dropping request.")

    body = form_data.get("Body", "")
    media_content_type = form_data.get("MediaContentType0")
    lat_str = form_data.get("Latitude")
    lng_str = form_data.get("Longitude")
    
    lat = float(lat_str) if lat_str else None
    lng = float(lng_str) if lng_str else None
    
    location_source = "gps_pin" if (lat and lng) else ("needs_nlp_extraction" if body else None)
    
    # -- AI Triage Processing --
    category = None
    priority = None
    sentiment = None
    extracted_location = None
    summary = None
    
    if gemini_client and body:
        try:
            prompt = f"Analyze this citizen grievance report from a Smart City WhatsApp tip-line. Extract the structured triage data.\\n\\nReport Text: {body}"
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
            extracted_location = triage.extracted_location
            summary = triage.summary
            
            print(f"--- AI Triage Result ---\\nCategory: {category}\\nPriority: {priority}\\nSummary: {summary}\\n-----------------------")
        except Exception as e:
            print(f"[AI ERROR] Gemini Triage Failed: {e}")

    new_message = Message(
        timestamp=datetime.now().isoformat(),
        sender=form_data.get("From", "Unknown"),
        body=body,
        media_url=form_data.get("MediaUrl0"),
        media_content_type=media_content_type,
        latitude=lat,
        longitude=lng,
        location_source=location_source,
        category=category,
        priority=priority,
        sentiment=sentiment,
        extracted_location=extracted_location,
        summary=summary
    )
    
    db.add(new_message)
    db.commit()
    
    return HTMLResponse(content="", status_code=200)

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
            "summary": r.summary
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
"""
    with open('main.py', 'w', encoding='utf-8') as f:
        f.write(code)
    print("main.py updated!")

if __name__ == "__main__":
    update_main()
