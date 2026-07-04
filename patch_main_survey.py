import sys

with open("main.py", "r", encoding="utf-8") as f:
    code = f.read()

# 1. Update SQLAlchemy Imports
code = code.replace(
    "from sqlalchemy import create_engine, Column, Integer, String, Text, Float",
    "from sqlalchemy import create_engine, Column, Integer, String, Text, Float, Boolean, ForeignKey, func"
)

# 2. Add Survey Models
models = """
class Survey(Base):
    __tablename__ = "surveys"
    id = Column(Integer, primary_key=True, index=True)
    question = Column(Text, nullable=False)
    options = Column(Text, nullable=False)
    is_active = Column(Boolean, default=False)

class SurveyResponse(Base):
    __tablename__ = "survey_responses"
    id = Column(Integer, primary_key=True, index=True)
    survey_id = Column(Integer, ForeignKey('surveys.id'), nullable=False)
    sender = Column(String, index=True)
    selected_option = Column(Text, nullable=False)
"""
if "class Survey(Base):" not in code:
    code = code.replace("class ConversationSession(Base):", models + "\nclass ConversationSession(Base):")

# 3. Update FastAPI Endpoints
endpoints = """
class SurveyCreate(BaseModel):
    question: str
    options: str

@app.get("/surveys")
async def get_surveys(db: Session = Depends(get_db)):
    surveys = db.query(Survey).all()
    results = []
    for s in surveys:
        resp_counts = db.query(SurveyResponse.selected_option, func.count(SurveyResponse.id)).filter(SurveyResponse.survey_id == s.id).group_by(SurveyResponse.selected_option).all()
        # Convert list of tuples to dict
        counts_dict = {row[0]: row[1] for row in resp_counts}
        results.append({
            "id": s.id, 
            "question": s.question, 
            "options": [opt.strip() for opt in s.options.split(',')], 
            "is_active": s.is_active, 
            "results": counts_dict
        })
    return results

@app.post("/surveys")
async def create_survey(survey: SurveyCreate, db: Session = Depends(get_db)):
    new_survey = Survey(question=survey.question, options=survey.options, is_active=False)
    db.add(new_survey)
    db.commit()
    return {"status": "success"}

@app.post("/surveys/{survey_id}/activate")
async def activate_survey(survey_id: int, db: Session = Depends(get_db)):
    db.query(Survey).update({Survey.is_active: False})
    db.query(Survey).filter(Survey.id == survey_id).update({Survey.is_active: True})
    db.commit()
    return {"status": "success"}
"""
if "@app.get(\"/surveys\")" not in code:
    code = code.replace("@app.get(\"/messages\")", endpoints + "\n@app.get(\"/messages\")")

# 4. Update WhatsApp State Machine Finalize -> Survey
old_finalize_end = """        db.add(new_message)
        clear_session(db, sender)
        db.commit()
        
        twiml.message(f"Proposal submitted successfully. Reference ID: #{ref_id}. Your MP's office has received this and it will be reviewed shortly.")
        logger.info(f"Proposal {ref_id} successfully created for {sender}.")
        return HTMLResponse(content=str(twiml), media_type="application/xml")"""

new_finalize_end = """        db.add(new_message)
        db.commit()
        
        active_survey = db.query(Survey).filter(Survey.is_active == True).first()
        if active_survey:
            update_session(db, sender, "awaiting_survey", {"survey_id": active_survey.id})
            options = [opt.strip() for opt in active_survey.options.split(',')]
            opt_text = "\\n".join([f"{i+1}. {opt}" for i, opt in enumerate(options)])
            twiml.message(f"Proposal submitted successfully! Ref: #{ref_id}.\\n\\nBy the way, your MP wants to know:\\n*{active_survey.question}*\\n\\nReply with the number of your choice:\\n{opt_text}")
        else:
            clear_session(db, sender)
            twiml.message(f"Proposal submitted successfully. Reference ID: #{ref_id}. Your MP's office has received this and it will be reviewed shortly.")
            
        logger.info(f"Proposal {ref_id} successfully created for {sender}.")
        return HTMLResponse(content=str(twiml), media_type="application/xml")"""
code = code.replace(old_finalize_end, new_finalize_end)

# 5. Add "awaiting_survey" state logic
survey_state = """    elif current_step == "awaiting_survey":
        try:
            data = json.loads(session.collected_data) if session.collected_data else {}
            survey_id = data.get("survey_id")
            survey = db.query(Survey).filter(Survey.id == survey_id).first()
            if survey:
                options = [opt.strip() for opt in survey.options.split(',')]
                try:
                    choice_idx = int(body.strip()) - 1
                    if 0 <= choice_idx < len(options):
                        choice = options[choice_idx]
                        db.add(SurveyResponse(survey_id=survey_id, sender=sender, selected_option=choice))
                        db.commit()
                        twiml.message("Thank you! Your feedback has been recorded.")
                    else:
                        twiml.message("Invalid option number. Please reply with a valid number from the list.")
                        return HTMLResponse(content=str(twiml), media_type="application/xml")
                except ValueError:
                    twiml.message("Please reply with a valid number from the list.")
                    return HTMLResponse(content=str(twiml), media_type="application/xml")
        except Exception as e:
            logger.error(f"Error processing survey response: {e}")
            twiml.message("An error occurred. Thank you anyway!")
            
        clear_session(db, sender)
        return HTMLResponse(content=str(twiml), media_type="application/xml")
        
    # Finalize Logic"""
if "awaiting_survey" not in code:
    code = code.replace("    # Finalize Logic", survey_state)

with open("main.py", "w", encoding="utf-8") as f:
    f.write(code)
