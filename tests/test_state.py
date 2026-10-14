"""Conversation state for one citizen.

Citizens routinely send two messages in quick succession, which arrive as
concurrent webhooks. The previous read-then-write let the second overwrite the
first, losing part of the report the citizen had already given.
"""

import json

import state
from tests.fakes import FakeFirestore

PHONE = "whatsapp:+919812345678"


def collected(db, phone=PHONE):
    return json.loads(db.data[state.SESSION_COLLECTION][phone]["collected_data"])


def test_first_write_creates_the_session():
    db = FakeFirestore()

    state.update_session(db, PHONE, "awaiting_location", {"description": "Broken drain"})

    stored = db.data[state.SESSION_COLLECTION][PHONE]
    assert stored["current_step"] == "awaiting_location"
    assert stored["phone_number"] == PHONE
    assert collected(db) == {"description": "Broken drain"}


def test_later_writes_merge_rather_than_replace():
    db = FakeFirestore()

    state.update_session(db, PHONE, "awaiting_location", {"description": "Broken drain"})
    state.update_session(db, PHONE, "awaiting_photo", {"location": "26.84,80.94"})

    assert collected(db) == {"description": "Broken drain", "location": "26.84,80.94"}, (
        "the description the citizen already gave must survive the next step"
    )


def test_a_field_can_be_overwritten_on_purpose():
    db = FakeFirestore()

    state.update_session(db, PHONE, "awaiting_location", {"description": "first"})
    state.update_session(db, PHONE, "awaiting_location", {"description": "corrected"})

    assert collected(db) == {"description": "corrected"}


def test_interleaved_updates_keep_both_answers():
    """Mimics two webhooks for the same citizen arriving together."""
    db = FakeFirestore()
    state.update_session(db, PHONE, "awaiting_location", {"description": "Broken drain"})

    state.update_session(db, PHONE, "awaiting_photo", {"location": "Hazratganj"})
    state.update_session(db, PHONE, "awaiting_photo", {"photo_urls": ["/sim/api/media/a"]})

    assert collected(db) == {
        "description": "Broken drain",
        "location": "Hazratganj",
        "photo_urls": ["/sim/api/media/a"],
    }


def test_unreadable_payload_does_not_break_the_flow():
    db = FakeFirestore()
    db.data.setdefault(state.SESSION_COLLECTION, {})[PHONE] = {
        "phone_number": PHONE,
        "current_step": "awaiting_location",
        "collected_data": "not json at all",
    }

    state.update_session(db, PHONE, "awaiting_photo", {"location": "Chowk"})

    assert collected(db) == {"location": "Chowk"}


def test_a_json_payload_that_is_not_an_object_is_discarded():
    db = FakeFirestore()
    db.data.setdefault(state.SESSION_COLLECTION, {})[PHONE] = {
        "phone_number": PHONE,
        "current_step": "x",
        "collected_data": json.dumps(["unexpected", "list"]),
    }

    state.update_session(db, PHONE, "awaiting_photo", {"location": "Chowk"})

    assert collected(db) == {"location": "Chowk"}


def test_get_session_returns_none_when_absent():
    assert state.get_session(FakeFirestore(), PHONE) is None


def test_get_session_reads_back_what_was_written():
    db = FakeFirestore()
    state.update_session(db, PHONE, "awaiting_photo", {"location": "Chowk"})

    session = state.get_session(db, PHONE)

    assert session.current_step == "awaiting_photo"
    assert json.loads(session.collected_data) == {"location": "Chowk"}


def test_clear_removes_the_session():
    db = FakeFirestore()
    state.update_session(db, PHONE, "awaiting_photo", {"location": "Chowk"})

    state.clear_session(db, PHONE)

    assert state.get_session(db, PHONE) is None


def test_sessions_are_isolated_per_citizen():
    db = FakeFirestore()
    other = "whatsapp:+919800000000"

    state.update_session(db, PHONE, "awaiting_photo", {"description": "mine"})
    state.update_session(db, other, "awaiting_location", {"description": "theirs"})

    assert collected(db, PHONE) == {"description": "mine"}
    assert collected(db, other) == {"description": "theirs"}
