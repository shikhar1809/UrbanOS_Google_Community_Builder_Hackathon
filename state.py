import json

def get_session(db_session, phone_number: str):
    from main import ConversationSession
    return db_session.query(ConversationSession).filter_by(phone_number=phone_number).first()

def update_session(db_session, phone_number: str, step: str, data: dict = None):
    session = get_session(db_session, phone_number)
    
    # Merge existing data with new data if session exists
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
    
    if session:
        session.current_step = step
        session.collected_data = data_str
    else:
        from main import ConversationSession
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
