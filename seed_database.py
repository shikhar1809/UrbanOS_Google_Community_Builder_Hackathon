import os
import random
import uuid
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

load_dotenv()

if not firebase_admin._apps:
    if os.path.exists("firebase-key.json"):
        cred = credentials.Certificate("firebase-key.json")
        firebase_admin.initialize_app(cred)
    else:
        firebase_admin.initialize_app()
db = firestore.client()

zones = [
    "Central Lucknow", "Gomti Nagar", "Alambagh", "Hazratganj", 
    "Chowk", "Indira Nagar", "Mahanagar", "Aminabad", "Ashiyana", "Rajajipuram"
]

categories = {
    "Infrastructure": ["pothole", "broken road", "bridge repair", "footpath damage"],
    "Water & Sanitation": ["water logging", "broken pipe", "dirty drinking water", "no water supply"],
    "Waste Management": ["garbage dump", "overflowing bin", "irregular pickup", "dead animal"],
    "Electricity": ["streetlights not working", "frequent power cuts", "loose hanging wires", "transformer spark"],
    "Public Safety": ["stray dog menace", "no police patrolling", "unauthorized parking", "open manhole"],
    "Transport": ["traffic signal broken", "bus stop shed missing", "illegal auto stand"],
    "Parks & Rec": ["park maintenance needed", "broken swings", "overgrown weeds"]
}

priorities = ["Critical", "High", "Medium", "Low"]
sentiments = ["Frustrated", "Neutral", "Concerned", "Urgent", "Hopeful"]
languages = ["en", "hi"]

def generate_body(cat, tag, zone):
    templates = [
        f"There is a severe issue of {tag} in {zone}. Please fix it soon.",
        f"We have been facing {tag} problems near the main road in {zone} for weeks.",
        f"Urgent attention needed for {tag} at {zone}. It is causing a lot of trouble.",
        f"Please look into the {tag} situation in {zone}. The residents are very concerned.",
        f"{tag.capitalize()} issue reported in {zone}. Kindly resolve at the earliest."
    ]
    return random.choice(templates)

def get_random_date():
    end_date = datetime.now()
    start_date = end_date - timedelta(days=90) # Last 3 months
    random_days = random.randrange((end_date - start_date).days)
    return start_date + timedelta(days=random_days, hours=random.randrange(24), minutes=random.randrange(60))

def generate_proposals(count=1000):
    batch = db.batch()
    batch_count = 0
    total_inserted = 0
    
    print(f"Generating {count} realistic proposals...")
    
    for _ in range(count):
        cat = random.choice(list(categories.keys()))
        tag = random.choice(categories[cat])
        zone = random.choice(zones)
        lang = random.choices(["en", "hi"], weights=[0.7, 0.3])[0]
        
        priority = random.choices(["Critical", "High", "Medium", "Low"], weights=[0.1, 0.3, 0.4, 0.2])[0]
        
        lat = round(random.uniform(26.75, 26.95), 5) # Approx Lucknow bounds
        lng = round(random.uniform(80.85, 81.05), 5)
        
        doc_ref = db.collection('messages').document()
        ref_id = f"REF-{str(uuid.uuid4())[:8].upper()}"
        
        # Budget ranges based on category
        if cat in ["Infrastructure", "Water & Sanitation"]:
            budget = f"₹{random.randint(5, 50)},00,000"
        else:
            budget = f"₹{random.randint(10, 500)},000"
            
        data = {
            "timestamp": get_random_date().isoformat(),
            "sender": f"whatsapp:+9198{random.randint(10000000, 99999999)}",
            "body": generate_body(cat, tag, zone),
            "media_url": None,
            "media_type": None,
            "latitude": lat,
            "longitude": lng,
            "location_source": "text",
            "category": cat,
            "priority": priority,
            "sentiment": random.choice(sentiments),
            "extracted_location": zone,
            "summary": f"{tag.capitalize()} in {zone}",
            "original_language": lang,
            "constituency_zone": zone,
            "estimated_budget": budget,
            "semantic_tag": tag,
            "reference_id": ref_id,
            "status": random.choices(["Open", "In Progress", "Resolved"], weights=[0.6, 0.2, 0.2])[0],
            "id": doc_ref.id
        }
        
        batch.set(doc_ref, data)
        batch_count += 1
        total_inserted += 1
        
        if batch_count == 400: # Firestore batch limit is 500
            batch.commit()
            print(f"Committed {total_inserted} records...")
            batch = db.batch()
            batch_count = 0
            
    if batch_count > 0:
        batch.commit()
        print(f"Committed {total_inserted} records...")
        
    print(f"Successfully populated {total_inserted} realistic datasets into the database!")

if __name__ == "__main__":
    generate_proposals(1000)
