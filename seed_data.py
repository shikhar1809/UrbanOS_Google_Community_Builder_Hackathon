"""
seed_data.py — Injects 20 realistic constituency development proposals into Firestore.
Run once locally: python seed_data.py
"""
import os
import uuid
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta
import random

# Initialize Firebase
if os.path.exists("firebase-key.json"):
    cred = credentials.Certificate("firebase-key.json")
else:
    cred = credentials.ApplicationDefault()

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)

db = firestore.client()

PROPOSALS = [
    # --- North Zone ---
    {
        "body": "Hamare ward mein primary school ki bahut zaroorat hai. Bacche 8 km door padhne jaate hain. Kripya ek school banwayein.",
        "summary": "New Primary School Ward 4",
        "category": "Construction Development",
        "priority": "Critical",
        "sentiment": "High",
        "constituency_zone": "North",
        "extracted_location": "Ward 4, Aliganj",
        "original_language": "Hindi",
        "estimated_budget": 24000000,
        "latitude": 26.8970, "longitude": 80.9862,
        "sender": "whatsapp:+919876543210",
    },
    {
        "body": "The road connecting Indira Nagar sector 12 to the main highway is completely broken. Heavy rains have made it impassable. Urgent repair needed.",
        "summary": "Indira Nagar Road Repair",
        "category": "Infrastructure Upgrade",
        "priority": "High",
        "sentiment": "High",
        "constituency_zone": "North",
        "extracted_location": "Indira Nagar Sector 12",
        "original_language": "English",
        "estimated_budget": 3500000,
        "latitude": 26.9010, "longitude": 80.9920,
        "sender": "whatsapp:+919876543211",
    },
    {
        "body": "Humara nala poori colony mein bhar gaya hai. Paani ghar mein ghus raha hai. Please jald se jald safai karwayein.",
        "summary": "Drainage Overflow Aliganj Colony",
        "category": "Public Utility Works",
        "priority": "Critical",
        "sentiment": "High",
        "constituency_zone": "North",
        "extracted_location": "Aliganj Colony",
        "original_language": "Hindi",
        "estimated_budget": 800000,
        "latitude": 26.9001, "longitude": 80.9905,
        "sender": "whatsapp:+919876543212",
    },
    {
        "body": "We need a community health centre in our sector. People have to travel 12km to reach KGMU. At least a dispensary would help.",
        "summary": "Community Health Centre Sector 9",
        "category": "Construction Development",
        "priority": "High",
        "sentiment": "Moderate",
        "constituency_zone": "North",
        "extracted_location": "Sector 9, North Lucknow",
        "original_language": "English",
        "estimated_budget": 15000000,
        "latitude": 26.9100, "longitude": 80.9940,
        "sender": "whatsapp:+919876543213",
    },
    # --- South Zone ---
    {
        "body": "Aminabad market mein street lights nahi hain. Raat ko crime ka darr rehta hai. Please lights lagwayein.",
        "summary": "Aminabad Street Light Installation",
        "category": "Public Utility Works",
        "priority": "High",
        "sentiment": "High",
        "constituency_zone": "South",
        "extracted_location": "Aminabad Market",
        "original_language": "Hindi",
        "estimated_budget": 1200000,
        "latitude": 26.8467, "longitude": 80.9362,
        "sender": "whatsapp:+919876543214",
    },
    {
        "body": "The park near Hazratganj is in terrible condition. Benches broken, no greenery. Children have no place to play. Request renovation.",
        "summary": "Hazratganj Park Renovation",
        "category": "Environmental Project",
        "priority": "Medium",
        "sentiment": "Moderate",
        "constituency_zone": "South",
        "extracted_location": "Hazratganj Park",
        "original_language": "English",
        "estimated_budget": 2500000,
        "latitude": 26.8500, "longitude": 80.9400,
        "sender": "whatsapp:+919876543215",
    },
    {
        "body": "Charbagh railway station ke paas sewer line bahut purani hai aur toot gayi hai. Sadak pe ganda paani bah raha hai.",
        "summary": "Charbagh Sewer Line Replacement",
        "category": "Infrastructure Upgrade",
        "priority": "Critical",
        "sentiment": "High",
        "constituency_zone": "South",
        "extracted_location": "Charbagh Station Road",
        "original_language": "Hindi",
        "estimated_budget": 5000000,
        "latitude": 26.8430, "longitude": 80.9150,
        "sender": "whatsapp:+919876543216",
    },
    {
        "body": "We need a vocational training center in Mahanagar. Unemployed youth have nowhere to learn skills. Government ITI is 15km away.",
        "summary": "Vocational Center Mahanagar",
        "category": "Construction Development",
        "priority": "High",
        "sentiment": "Moderate",
        "constituency_zone": "South",
        "extracted_location": "Mahanagar Colony",
        "original_language": "English",
        "estimated_budget": 18000000,
        "latitude": 26.8550, "longitude": 80.9450,
        "sender": "whatsapp:+919876543217",
    },
    # --- East Zone ---
    {
        "body": "Gomtinagar extension mein drinking water supply nahi aati. Tanker pe depend rehna padta hai. Please water pipeline lagwayein.",
        "summary": "Gomtinagar Water Pipeline Extension",
        "category": "Infrastructure Upgrade",
        "priority": "Critical",
        "sentiment": "High",
        "constituency_zone": "East",
        "extracted_location": "Gomtinagar Extension",
        "original_language": "Hindi",
        "estimated_budget": 9000000,
        "latitude": 26.8610, "longitude": 81.0050,
        "sender": "whatsapp:+919876543218",
    },
    {
        "body": "The bridge over Gomti river near Shaheed Path is showing cracks. This is a major safety hazard. Structural inspection and repair needed urgently.",
        "summary": "Gomti Bridge Structural Repair",
        "category": "Maintenance & Repair",
        "priority": "Critical",
        "sentiment": "Complex",
        "constituency_zone": "East",
        "extracted_location": "Shaheed Path Bridge",
        "original_language": "English",
        "estimated_budget": 12000000,
        "latitude": 26.8700, "longitude": 81.0100,
        "sender": "whatsapp:+919876543219",
    },
    {
        "body": "Vibhuti Khand mein koi playground nahi hai. Bacchon ko gali mein khelna padta hai. Ek chhota sa park banwa dijiye.",
        "summary": "Children's Playground Vibhuti Khand",
        "category": "Construction Development",
        "priority": "Medium",
        "sentiment": "High",
        "constituency_zone": "East",
        "extracted_location": "Vibhuti Khand",
        "original_language": "Hindi",
        "estimated_budget": 1800000,
        "latitude": 26.8650, "longitude": 81.0000,
        "sender": "whatsapp:+919876543220",
    },
    {
        "body": "Kanpur road widening project has been pending for 3 years. Traffic jams cause daily commute issues and business losses.",
        "summary": "Kanpur Road Widening Phase 2",
        "category": "Infrastructure Upgrade",
        "priority": "High",
        "sentiment": "Complex",
        "constituency_zone": "East",
        "extracted_location": "Kanpur Road, East Lucknow",
        "original_language": "English",
        "estimated_budget": 35000000,
        "latitude": 26.8600, "longitude": 80.9980,
        "sender": "whatsapp:+919876543221",
    },
    # --- West Zone ---
    {
        "body": "Alambagh mein government school ka building bahut kharab hai. Chhat se paani tapakta hai. Renovation ki zaroorat hai.",
        "summary": "Alambagh School Renovation",
        "category": "Maintenance & Repair",
        "priority": "High",
        "sentiment": "High",
        "constituency_zone": "West",
        "extracted_location": "Alambagh Government School",
        "original_language": "Hindi",
        "estimated_budget": 4500000,
        "latitude": 26.8200, "longitude": 80.8990,
        "sender": "whatsapp:+919876543222",
    },
    {
        "body": "We need proper waste management in Chinhat industrial area. Factories dump waste openly. Request a waste processing unit.",
        "summary": "Chinhat Waste Processing Unit",
        "category": "Environmental Project",
        "priority": "High",
        "sentiment": "Complex",
        "constituency_zone": "West",
        "extracted_location": "Chinhat Industrial Area",
        "original_language": "English",
        "estimated_budget": 22000000,
        "latitude": 26.8300, "longitude": 80.8800,
        "sender": "whatsapp:+919876543223",
    },
    {
        "body": "Naka Hindola area mein bijli ki problem bahut zyada hai. Din mein 6-8 ghante light nahi rehti. Transformer upgrade karna hoga.",
        "summary": "Naka Hindola Transformer Upgrade",
        "category": "Public Utility Works",
        "priority": "High",
        "sentiment": "High",
        "constituency_zone": "West",
        "extracted_location": "Naka Hindola",
        "original_language": "Hindi",
        "estimated_budget": 3200000,
        "latitude": 26.8250, "longitude": 80.8950,
        "sender": "whatsapp:+919876543224",
    },
    # --- Central Zone ---
    {
        "body": "Requesting a digital literacy center in Husainganj. Senior citizens and homemakers want to learn computer basics and digital payments.",
        "summary": "Digital Literacy Centre Husainganj",
        "category": "Construction Development",
        "priority": "Medium",
        "sentiment": "High",
        "constituency_zone": "Central",
        "extracted_location": "Husainganj",
        "original_language": "English",
        "estimated_budget": 2800000,
        "latitude": 26.8610, "longitude": 80.9480,
        "sender": "whatsapp:+919876543225",
    },
    {
        "body": "Kaiserbagh ke paas footpath bilkul nahi hai. Pedestrians sadak pe chalna padta hai. Bahut dangerous hai. Please footpath banwayein.",
        "summary": "Kaiserbagh Footpath Construction",
        "category": "Infrastructure Upgrade",
        "priority": "High",
        "sentiment": "High",
        "constituency_zone": "Central",
        "extracted_location": "Kaiserbagh",
        "original_language": "Hindi",
        "estimated_budget": 1500000,
        "latitude": 26.8560, "longitude": 80.9390,
        "sender": "whatsapp:+919876543226",
    },
    {
        "body": "The old age home in Nishatganj needs major renovation. Roof is leaking and facilities are inadequate for 80 residents.",
        "summary": "Nishatganj Old Age Home Renovation",
        "category": "Maintenance & Repair",
        "priority": "High",
        "sentiment": "High",
        "constituency_zone": "Central",
        "extracted_location": "Nishatganj",
        "original_language": "English",
        "estimated_budget": 6000000,
        "latitude": 26.8670, "longitude": 80.9510,
        "sender": "whatsapp:+919876543227",
    },
    {
        "body": "Lucknow zoo ke paas traffic signal nahi hai. Hafte mein 2-3 accidents hote hain. Please signal lagwayein ya speed breaker daalen.",
        "summary": "Zoo Chowk Traffic Signal",
        "category": "Infrastructure Upgrade",
        "priority": "Critical",
        "sentiment": "High",
        "constituency_zone": "Central",
        "extracted_location": "Lucknow Zoo Crossing",
        "original_language": "Hindi",
        "estimated_budget": 450000,
        "latitude": 26.8640, "longitude": 80.9460,
        "sender": "whatsapp:+919876543228",
    },
    {
        "body": "Request for solar street lights in Lalbagh colony. Current lights are broken and area is very dark at night. Solar is cost effective.",
        "summary": "Lalbagh Solar Street Lights",
        "category": "Public Utility Works",
        "priority": "Medium",
        "sentiment": "High",
        "constituency_zone": "Central",
        "extracted_location": "Lalbagh Colony",
        "original_language": "English",
        "estimated_budget": 900000,
        "latitude": 26.8590, "longitude": 80.9430,
        "sender": "whatsapp:+919876543229",
    },
]

def seed():
    print("Seeding Firestore with demo proposals...")
    base_time = datetime.now() - timedelta(days=7)
    
    for i, p in enumerate(PROPOSALS):
        doc_ref = db.collection('messages').document()
        ref_id = f"UO{uuid.uuid4().hex[:6].upper()}"
        ts = base_time + timedelta(hours=random.randint(i*6, i*6+5))
        
        doc_ref.set({
            "id": doc_ref.id,
            "reference_id": ref_id,
            "timestamp": ts.isoformat(),
            "sender": p["sender"],
            "body": p["body"],
            "summary": p["summary"],
            "category": p["category"],
            "priority": p["priority"],
            "sentiment": p["sentiment"],
            "constituency_zone": p["constituency_zone"],
            "extracted_location": p["extracted_location"],
            "original_language": p["original_language"],
            "estimated_budget": p["estimated_budget"],
            "latitude": p.get("latitude"),
            "longitude": p.get("longitude"),
            "location_source": "text",
            "status": random.choice(["Open", "Open", "Open", "In Review", "Sanctioned"]),
            "is_urgent": p["priority"] == "Critical",
            "media_url": None,
            "media_type": None,
        })
        print(f"  [{i+1}/20] {p['summary']} ({p['constituency_zone']})")
    
    print("Seeding complete! 20 proposals added to Firestore.")

if __name__ == "__main__":
    seed()
