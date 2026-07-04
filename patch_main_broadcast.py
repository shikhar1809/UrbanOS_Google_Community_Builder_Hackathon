import sys

with open("main.py", "r", encoding="utf-8") as f:
    code = f.read()

# 1. Add Twilio Client import and TWILIO_WHATSAPP_NUMBER
if "from twilio.rest import Client" not in code:
    code = code.replace("from twilio.twiml.messaging_response import MessagingResponse", "from twilio.twiml.messaging_response import MessagingResponse\nfrom twilio.rest import Client")

if "TWILIO_WHATSAPP_NUMBER" not in code:
    code = code.replace("TWILIO_ACCOUNT_SID = os.getenv(\"TWILIO_ACCOUNT_SID\")", "TWILIO_WHATSAPP_NUMBER = os.getenv(\"TWILIO_WHATSAPP_NUMBER\")\nTWILIO_ACCOUNT_SID = os.getenv(\"TWILIO_ACCOUNT_SID\")")

# 2. Add Broadcast Endpoint
broadcast_ep = """
@app.post("/surveys/{survey_id}/broadcast")
async def broadcast_survey(survey_id: int, db: Session = Depends(get_db)):
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if not survey:
        raise HTTPException(status_code=404, detail="Survey not found")
        
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_WHATSAPP_NUMBER:
        raise HTTPException(status_code=500, detail="Twilio credentials missing")
        
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    
    # Get all unique senders
    senders = db.query(Message.sender).distinct().all()
    count = 0
    
    options = [opt.strip() for opt in survey.options.split(',')]
    opt_text = "\\n".join([f"{i+1}. {opt}" for i, opt in enumerate(options)])
    msg_body = f"Your MP is requesting your feedback:\\n*{survey.question}*\\n\\nReply with the number of your choice:\\n{opt_text}"
    
    for s in senders:
        sender_phone = s[0]
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
"""
if "@app.post(\"/surveys/{survey_id}/broadcast\")" not in code:
    code = code.replace("@app.get(\"/messages\")", broadcast_ep + "\n@app.get(\"/messages\")")

# 3. Intercept single digits in current_step is None
old_start = """    if current_step is None:
        twiml.message("What development project or community upgrade would you like to propose for your area? Please describe your idea. (You can also send a voice note!)")
        update_session(db, sender, "awaiting_description", {})
        return HTMLResponse(content=str(twiml), media_type="application/xml")"""

new_start = """    if current_step is None:
        if body and body.strip().isdigit():
            active_survey = db.query(Survey).filter(Survey.is_active == True).first()
            if active_survey:
                options = [opt.strip() for opt in active_survey.options.split(',')]
                try:
                    choice_idx = int(body.strip()) - 1
                    if 0 <= choice_idx < len(options):
                        choice = options[choice_idx]
                        db.add(SurveyResponse(survey_id=active_survey.id, sender=sender, selected_option=choice))
                        db.commit()
                        twiml.message("Thank you! Your feedback has been recorded.")
                        return HTMLResponse(content=str(twiml), media_type="application/xml")
                except Exception:
                    pass
        
        twiml.message("What development project or community upgrade would you like to propose for your area? Please describe your idea. (You can also send a voice note!)")
        update_session(db, sender, "awaiting_description", {})
        return HTMLResponse(content=str(twiml), media_type="application/xml")"""
code = code.replace(old_start, new_start)

with open("main.py", "w", encoding="utf-8") as f:
    f.write(code)
