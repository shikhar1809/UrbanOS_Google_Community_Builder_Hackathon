from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import operations
from tests.fakes import FakeFirestore


RECIPIENT = "whatsapp:+919812345678"
DEMO_RECIPIENT = "whatsapp:+999000000001"


def report(reference, *, sender=RECIPIENT, **overrides):
    filed = datetime.now(timezone(timedelta(hours=5, minutes=30))) - timedelta(minutes=5)
    return {
        "reference_id": reference,
        "sender": sender,
        "timestamp": filed.isoformat(),
        "category": "Infrastructure",
        "summary": "Open drain near the school",
        "body": "An open drain near the school needs inspection.",
        "status": "Open",
        "priority": "Critical",
        "ward_no": "Ward 14",
        "constituency_zone": "North",
        "original_language": "English",
        "latitude": 26.91,
        "longitude": 80.94,
        "location_source": "gps_pin",
        **overrides,
    }


def dashboard_db():
    db = FakeFirestore()
    db.data["messages"] = {
        "real": report("QA-OPEN"),
        "resolved": report("QA-DONE", status="Completed"),
        "demo": report("QA-DEMO", sender=DEMO_RECIPIENT, simulated=True, priority="High", responsible_body="Jal Sansthan"),
    }
    db.data["surveys"] = {"qa": {
        "id": "qa", "question": "How do you rate street lighting?", "options": "Good,Poor,Unsure",
        "is_active": True, "sent_to": [RECIPIENT],
    }}
    db.data["survey_responses"] = {"r1": {"survey_id": "qa", "sender": RECIPIENT, "selected_option": "Good"}}
    return db


def use_local_desk_writes(db, monkeypatch):
    """Execute the real transition with an in-memory transaction adapter."""
    monkeypatch.setattr(db, "transaction", lambda: SimpleNamespace(update=lambda ref, fields: ref.update(fields)), raising=False)
    monkeypatch.setattr(operations, "transactional", lambda action: action)
