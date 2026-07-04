import os

def refactor_main():
    with open('main.py', 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Update imports
    imports_to_add = """from fastapi import FastAPI, Request, HTTPException, Depends, BackgroundTasks
from twilio.twiml.messaging_response import MessagingResponse
from state import get_session, update_session, clear_session
from worker import process_voice_note
import json"""
    
    content = content.replace("from fastapi import FastAPI, Request, HTTPException, Depends", imports_to_add)

    # 2. Update Message model
    old_model_end = """    extracted_location = Column(String, nullable=True)
    summary = Column(String, nullable=True)

Base.metadata.create_all(bind=engine)"""

    new_model_end = """    extracted_location = Column(String, nullable=True)
    summary = Column(String, nullable=True)
    reference_id = Column(String, nullable=True)
    status = Column(String, default="Open")

# Ensure all models including state are created
import state
Base.metadata.create_all(bind=engine)"""
    
    content = content.replace(old_model_end, new_model_end)

    # 3. Replace the receive_whatsapp endpoint
    # Find the start of the endpoint and the end (which is just before get_messages)
    endpoint_start = "@app.post(\"/webhook/whatsapp\")"
    endpoint_end = "@app.get(\"/messages\")"
    
    if endpoint_start in content and endpoint_end in content:
        start_idx = content.find(endpoint_start)
        end_idx = content.find(endpoint_end)
        
        old_endpoint_code = content[start_idx:end_idx]
        
        new_endpoint_code = """@app.post("/webhook/whatsapp")
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
            print(f"[SECURITY ALERT] Invalid Twilio signature from {request.client.host}. Dropping request.")

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
            # Lookup all active for this sender
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
            # Lookup specific ID
            parts = body_lower.split()
            if len(parts) > 1:
                ref_id = parts[1].strip('#')
                report = db.query(Message).filter(Message.reference_id == ref_id).first()
                if report:
                    twiml.message(f"Report #{report.reference_id}\\nCategory: {report.category or 'General'}\\nStatus: {report.status}\\nSummary: {report.summary}")
                else:
                    twiml.message(f"Could not find report #{ref_id}.")
                return HTMLResponse(content=str(twiml), media_type="application/xml")

    if body_lower == "new report":
        clear_session(db, sender)
        twiml.message("Previous report discarded. New report started. What issue would you like to report? Please describe it in your own words.")
        update_session(db, sender, "awaiting_description", {})
        return HTMLResponse(content=str(twiml), media_type="application/xml")

    # State Machine Logic
    session = get_session(db, sender)
    current_step = session.current_step if session else None
    
    if current_step is None:
        # Start new report
        twiml.message("New report started. What issue would you like to report? Please describe it in your own words. (You can also send a voice note!)")
        update_session(db, sender, "awaiting_description", {})
        return HTMLResponse(content=str(twiml), media_type="application/xml")
        
    elif current_step == "awaiting_description":
        if "audio" in media_type and media_url:
            twiml.message("Voice note received, processing your description now...")
            background_tasks.add_task(process_voice_note, media_url, sender, db)
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
            
    # Finalize Logic (Run if step moved to finalize above)
    session = get_session(db, sender) # refresh session
    if session and session.current_step == "finalize":
        data = json.loads(session.collected_data)
        
        description = data.get("description", "")
        location_raw = data.get("location", "")
        loc_source = data.get("location_source", "")
        photo_url = data.get("photo_url", "")
        
        lat = None
        lng = None
        if loc_source == "gps_pin" and "," in location_raw:
            try:
                lat = float(location_raw.split(",")[0])
                lng = float(location_raw.split(",")[1])
            except:
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
        
        if gemini_client and description:
            try:
                prompt = f"Analyze this citizen grievance report from a Smart City WhatsApp tip-line. Extract the structured triage data.\\n\\nReport Text: {description}"
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
                if loc_source == "text":
                    extracted_location = triage.extracted_location or location_raw
                summary = triage.summary
            except Exception as e:
                print(f"[AI ERROR] Gemini Triage Failed: {e}")
                
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
            reference_id=ref_id,
            status="Open"
        )
        
        db.add(new_message)
        clear_session(db, sender)
        db.commit()
        
        # We append to the existing twiml response created above
        twiml.message(f"Report submitted successfully. Reference ID: #{ref_id}. Your MP's office has received this and it will be reviewed shortly.")
        return HTMLResponse(content=str(twiml), media_type="application/xml")

"""
        content = content.replace(old_endpoint_code, new_endpoint_code)
        
        with open('main.py', 'w', encoding='utf-8') as f:
            f.write(content)
            
        print("Refactored main.py successfully!")
    else:
        print("Could not find the endpoint boundaries.")

if __name__ == "__main__":
    refactor_main()
