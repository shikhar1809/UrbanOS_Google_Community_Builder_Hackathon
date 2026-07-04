import re
import os

with open("main.py", "r", encoding="utf-8") as f:
    code = f.read()

# 1. Replace SQLAlchemy imports with Firebase
code = re.sub(r'from sqlalchemy .*?\n', '', code)
code = re.sub(r'from sqlalchemy\.orm .*?\n', '', code)

firebase_imports = """import firebase_admin
from firebase_admin import credentials, firestore

# Initialize Firebase
try:
    cred = credentials.Certificate("firebase-key.json")
    firebase_admin.initialize_app(cred)
    firestore_db = firestore.client()
except Exception as e:
    print("WARNING: Could not initialize Firebase. Please ensure firebase-key.json exists.")
    firestore_db = None

def get_db():
    yield firestore_db
"""

# Replace old SQLite init and get_db
code = re.sub(r'# SQLite Database Setup.*?def get_db\(\):\n    db = SessionLocal\(\)\n    try:\n        yield db\n    finally:\n        db\.close\(\)', firebase_imports, code, flags=re.DOTALL)

# 2. Remove SQLAlchemy Models
code = re.sub(r'class Message\(Base\):.*?class SurveyResponse\(Base\):.*?\n\n', '', code, flags=re.DOTALL)
code = re.sub(r'class Survey\(Base\):.*?is_active = Column\(Boolean, default=False\)\n', '', code, flags=re.DOTALL)
code = re.sub(r'class SurveyResponse\(Base\):.*?selected_option = Column\(Text, nullable=False\)\n', '', code, flags=re.DOTALL)
code = re.sub(r'class ConversationSession\(Base\):.*?collected_data = Column\(Text\)\n', '', code, flags=re.DOTALL)
code = re.sub(r'Base\.metadata\.create_all\(bind=engine\)', '', code)

# 3. Update WhatsApp Webhook db references
code = code.replace("db.query(Message).filter(Message.sender == sender, Message.reference_id != None).order_by(Message.id.desc()).limit(3).all()", 
                    "[doc.to_dict() for doc in db.collection('messages').where('sender', '==', sender).order_by('id', direction=firestore.Query.DESCENDING).limit(3).stream()]")

code = code.replace("report = db.query(Message).filter(Message.reference_id == ref_id).first()",
                    "report_docs = list(db.collection('messages').where('reference_id', '==', ref_id).limit(1).stream())\n                report = report_docs[0].to_dict() if report_docs else None")

code = code.replace("active_survey = db.query(Survey).filter(Survey.is_active == True).first()",
                    "survey_docs = list(db.collection('surveys').where('is_active', '==', True).limit(1).stream())\n            active_survey = survey_docs[0].to_dict() if survey_docs else None")

code = code.replace("survey = db.query(Survey).filter(Survey.id == survey_id).first()",
                    "survey_doc = db.collection('surveys').document(str(survey_id)).get()\n            survey = survey_doc.to_dict() if survey_doc.exists else None")

code = code.replace("survey = db.query(Survey).filter(Survey.id == survey_id).first()",
                    "survey_doc = db.collection('surveys').document(str(survey_id)).get()\n    survey = survey_doc.to_dict() if survey_doc.exists else None")

# Replace object notation with dict notation for the webhook
code = code.replace("r.reference_id", "r.get('reference_id')")
code = code.replace("r.category", "r.get('category')")
code = code.replace("r.status", "r.get('status')")
code = code.replace("report.reference_id", "report.get('reference_id')")
code = code.replace("report.category", "report.get('category')")
code = code.replace("report.status", "report.get('status')")
code = code.replace("report.summary", "report.get('summary')")
code = code.replace("active_survey.id", "active_survey.get('id')")
code = code.replace("active_survey.options", "active_survey.get('options')")
code = code.replace("active_survey.question", "active_survey.get('question')")
code = code.replace("survey.options", "survey.get('options')")
code = code.replace("survey.question", "survey.get('question')")

# Finalize new_message insertion
old_insert = """        new_message = Message(
            reference_id=ref_id,
            sender=sender,
            status="Pending Review",
            raw_body=description,
            location=location_raw,
            location_source=loc_source,
            latitude=lat,
            longitude=lng,
            media_url=photo_url,
            media_type=data.get("photo_type", "")
        )
        db.add(new_message)
        db.commit()"""

new_insert = """        new_message = {
            "reference_id": ref_id,
            "sender": sender,
            "status": "Pending Review",
            "raw_body": description,
            "location": location_raw,
            "location_source": loc_source,
            "latitude": lat,
            "longitude": lng,
            "media_url": photo_url,
            "media_type": data.get("photo_type", "")
        }
        # Add to firestore
        doc_ref = db.collection('messages').document()
        new_message['id'] = doc_ref.id
        doc_ref.set(new_message)"""
code = code.replace(old_insert, new_insert)

# Survey response insertion
old_s_insert = """db.add(SurveyResponse(survey_id=survey_id, sender=sender, selected_option=choice))
                        db.commit()"""
new_s_insert = """db.collection('survey_responses').add({
                            "survey_id": survey_id,
                            "sender": sender,
                            "selected_option": choice
                        })"""
code = code.replace(old_s_insert, new_s_insert)

# 4. GET /messages endpoint
old_get_msg = """    messages = db.query(Message).order_by(Message.id.desc()).all()
    results = []
    for msg in messages:
        results.append({
            "id": msg.id,
            "reference_id": msg.reference_id,
            "sender": msg.sender,
            "category": msg.category,
            "status": msg.status,
            "summary": msg.summary,
            "location": msg.location,
            "location_source": msg.location_source,
            "latitude": msg.latitude,
            "longitude": msg.longitude,
            "media_url": msg.media_url,
            "media_type": msg.media_type,
            "is_urgent": msg.is_urgent,
            "raw_body": msg.raw_body,
            "constituency_zone": msg.constituency_zone,
            "estimated_budget": msg.estimated_budget,
            "sentiment": msg.sentiment
        })
    return results"""

new_get_msg = """    docs = db.collection('messages').order_by('id', direction=firestore.Query.DESCENDING).stream()
    results = []
    for doc in docs:
        msg = doc.to_dict()
        results.append({
            "id": msg.get("id"),
            "reference_id": msg.get("reference_id"),
            "sender": msg.get("sender"),
            "category": msg.get("category"),
            "status": msg.get("status"),
            "summary": msg.get("summary"),
            "location": msg.get("location"),
            "location_source": msg.get("location_source"),
            "latitude": msg.get("latitude"),
            "longitude": msg.get("longitude"),
            "media_url": msg.get("media_url"),
            "media_type": msg.get("media_type"),
            "is_urgent": msg.get("is_urgent"),
            "raw_body": msg.get("raw_body"),
            "constituency_zone": msg.get("constituency_zone"),
            "estimated_budget": msg.get("estimated_budget"),
            "sentiment": msg.get("sentiment")
        })
    return results"""
code = code.replace(old_get_msg, new_get_msg)


# 5. Survey Endpoints
code = re.sub(r'surveys = db\.query\(Survey\)\.all\(\)\n    results = \[\]\n    for s in surveys:.*?\n        results\.append\(\{.*?\}\)\n    return results', 
              """surveys = [d.to_dict() for d in db.collection('surveys').stream()]
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
    return results""", code, flags=re.DOTALL)

old_survey_create = """    new_survey = Survey(question=survey.question, options=survey.options, is_active=False)
    db.add(new_survey)
    db.commit()"""
new_survey_create = """    doc_ref = db.collection('surveys').document()
    doc_ref.set({
        "id": doc_ref.id,
        "question": survey.question,
        "options": survey.options,
        "is_active": False
    })"""
code = code.replace(old_survey_create, new_survey_create)

old_survey_act = """    db.query(Survey).update({Survey.is_active: False})
    db.query(Survey).filter(Survey.id == survey_id).update({Survey.is_active: True})
    db.commit()"""
new_survey_act = """    # Deactivate all
    docs = db.collection('surveys').where('is_active', '==', True).stream()
    for doc in docs:
        doc.reference.update({"is_active": False})
    # Activate one
    db.collection('surveys').document(str(survey_id)).update({"is_active": True})"""
code = code.replace(old_survey_act, new_survey_act)

# Broadcast endpoint fix
code = code.replace("senders = db.query(Message.sender).distinct().all()", 
                    "docs = db.collection('messages').stream()\n    senders = list(set([doc.to_dict().get('sender') for doc in docs if doc.to_dict().get('sender')]))")
code = code.replace("sender_phone = s[0]", "sender_phone = s")

with open("main.py", "w", encoding="utf-8") as f:
    f.write(code)
