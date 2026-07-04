import json

class SimpleSession:
    def __init__(self, phone_number, current_step, collected_data):
        self.phone_number = phone_number
        self.current_step = current_step
        self.collected_data = collected_data

def get_session(db, phone_number: str):
    doc = db.collection('sessions').document(phone_number).get()
    if doc.exists:
        data = doc.to_dict()
        return SimpleSession(phone_number, data.get('current_step'), data.get('collected_data'))
    return None

def update_session(db, phone_number: str, step: str, data: dict = None):
    session = get_session(db, phone_number)
    
    new_data = {}
    if session and session.collected_data:
        try:
            new_data = json.loads(session.collected_data)
            if not isinstance(new_data, dict):
                new_data = {}
        except Exception:
            pass
            
    if data:
        new_data.update(data)
        
    data_str = json.dumps(new_data)
    
    db.collection('sessions').document(phone_number).set({
        'phone_number': phone_number,
        'current_step': step,
        'collected_data': data_str
    })
    
    return SimpleSession(phone_number, step, data_str)

def clear_session(db, phone_number: str):
    db.collection('sessions').document(phone_number).delete()
