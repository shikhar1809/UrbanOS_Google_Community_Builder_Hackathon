import json
import logging

logger = logging.getLogger(__name__)

SESSION_COLLECTION = 'sessions'


class SimpleSession:
    def __init__(self, phone_number, current_step, collected_data):
        self.phone_number = phone_number
        self.current_step = current_step
        self.collected_data = collected_data


def _loads(raw) -> dict:
    """Session payloads are stored as a JSON string. Anything unreadable is
    treated as an empty session rather than crashing the citizen's flow."""
    if isinstance(raw, dict):
        return dict(raw)
    try:
        parsed = json.loads(raw) if raw else {}
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def get_session(db, phone_number: str):
    doc = db.collection(SESSION_COLLECTION).document(phone_number).get()
    if doc.exists:
        data = doc.to_dict()
        return SimpleSession(phone_number, data.get('current_step'), data.get('collected_data'))
    return None


def update_session(db, phone_number: str, step: str, data: dict = None):
    """Advance a citizen's conversation state.

    Runs in a Firestore transaction. WhatsApp users routinely send two messages
    in quick succession (a description, then a photo), and those arrive as
    concurrent webhooks. The previous read-then-write would let the second
    delivery overwrite the first's answer, losing the earlier part of the
    report. Firestore retries the transaction on contention, so the second
    write merges onto the first rather than clobbering it.
    """
    doc_ref = db.collection(SESSION_COLLECTION).document(phone_number)

    try:
        transaction = db.transaction()
        return _update_in_transaction(transaction, doc_ref, phone_number, step, data)
    except Exception as e:
        # Fail open: a citizen mid-report must not be blocked because the
        # transaction could not run (older emulator, permissions, outage).
        logger.warning(f"[SESSION] transactional update failed, writing directly: {e}")
        return _update_directly(doc_ref, phone_number, step, data)


def _merged_payload(existing: dict, step: str, data: dict = None) -> dict:
    merged = _loads(existing.get('collected_data')) if existing else {}
    if data:
        merged.update(data)
    return {
        'phone_number': existing.get('phone_number') if existing else None,
        'current_step': step,
        'collected_data': json.dumps(merged),
    }


def _update_in_transaction(transaction, doc_ref, phone_number: str, step: str, data: dict = None):
    from google.cloud.firestore_v1 import transactional

    @transactional
    def run(tx):
        snap = doc_ref.get(transaction=tx)
        existing = (snap.to_dict() or {}) if snap.exists else {}
        payload = _merged_payload(existing, step, data)
        payload['phone_number'] = phone_number
        tx.set(doc_ref, payload)
        return payload

    payload = run(transaction)
    return SimpleSession(phone_number, step, payload['collected_data'])


def _update_directly(doc_ref, phone_number: str, step: str, data: dict = None):
    snap = doc_ref.get()
    existing = (snap.to_dict() or {}) if getattr(snap, 'exists', False) else {}
    payload = _merged_payload(existing, step, data)
    payload['phone_number'] = phone_number
    doc_ref.set(payload)
    return SimpleSession(phone_number, step, payload['collected_data'])


def clear_session(db, phone_number: str):
    db.collection(SESSION_COLLECTION).document(phone_number).delete()
