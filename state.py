import json
from sqlalchemy import Column, Integer, String, Text
from main import Base, SessionLocal

class ConversationSession(Base):
    __tablename__ = "sessions"
    
    id = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String, unique=True, index=True)
    current_step = Column(String)
    collected_data = Column(Text) # JSON string

def get_session(db_session, phone_number: str):
    return db_session.query(ConversationSession).filter_by(phone_number=phone_number).first()

def update_session(db_session, phone_number: str, step: str, data: dict = None):
    session = get_session(db_session, phone_number)
    
    # Merge existing data with new data if session exists
    new_data = {}
    if session and session.collected_data:
        try:
            new_data = json.loads(session.collected_data)
        except:
            pass
            
    if data:
        new_data.update(data)
        
    data_str = json.dumps(new_data)
    
    if session:
        session.current_step = step
        session.collected_data = data_str
    else:
        session = ConversationSession(
            phone_number=phone_number,
            current_step=step,
            collected_data=data_str
        )
        db_session.add(session)
        
    db_session.commit()
    db_session.refresh(session)
    return session

def clear_session(db_session, phone_number: str):
    session = get_session(db_session, phone_number)
    if session:
        db_session.delete(session)
        db_session.commit()
