import time

import pytest

import idempotency
from tests.fakes import BrokenFirestore, FakeFirestore

SID = "SM1234567890abcdef"


def test_first_delivery_is_granted_and_claim_is_recorded():
    db = FakeFirestore()

    ticket = idempotency.claim(db, SID)

    assert ticket.granted is True
    stored = db.data[idempotency.COLLECTION][SID]
    assert stored["status"] == idempotency.STATUS_PROCESSING


def test_redelivery_is_refused_while_the_first_is_still_processing():
    db = FakeFirestore()
    idempotency.claim(db, SID)

    second = idempotency.claim(db, SID)

    assert second.granted is False
    # Nothing to replay yet: the first delivery has not produced a reply.
    assert second.reply is None


def test_redelivery_replays_the_stored_reply():
    db = FakeFirestore()
    idempotency.claim(db, SID)
    idempotency.record_reply(db, SID, "<Response><Message>Logged</Message></Response>")

    second = idempotency.claim(db, SID)

    assert second.granted is False
    assert second.reply == "<Response><Message>Logged</Message></Response>"


def test_distinct_messages_do_not_block_each_other():
    db = FakeFirestore()

    assert idempotency.claim(db, "SM-one").granted is True
    assert idempotency.claim(db, "SM-two").granted is True


def test_abandoned_claim_is_taken_over_after_the_ttl():
    db = FakeFirestore()
    idempotency.claim(db, SID)
    # Simulate the first instance dying mid-triage.
    db.data[idempotency.COLLECTION][SID]["claimed_at_epoch"] = time.time() - idempotency.CLAIM_TTL_SECONDS - 1

    retry = idempotency.claim(db, SID)

    assert retry.granted is True, "a crashed delivery must not wedge the sender forever"


def test_completed_claim_is_never_treated_as_abandoned():
    db = FakeFirestore()
    idempotency.claim(db, SID)
    idempotency.record_reply(db, SID, "<Response></Response>")
    db.data[idempotency.COLLECTION][SID]["claimed_at_epoch"] = time.time() - idempotency.CLAIM_TTL_SECONDS - 1

    again = idempotency.claim(db, SID)

    assert again.granted is False


def test_release_lets_a_retry_through_after_a_failure():
    db = FakeFirestore()
    idempotency.claim(db, SID)

    idempotency.release(db, SID)

    assert idempotency.claim(db, SID).granted is True


@pytest.mark.parametrize("message_id", [None, ""])
def test_messages_without_an_id_are_always_processed(message_id):
    db = FakeFirestore()

    assert idempotency.claim(db, message_id).granted is True
    assert db.data == {}, "no claim should be written when there is no id"


def test_claim_fails_open_when_the_store_is_unreachable():
    assert idempotency.claim(BrokenFirestore(), SID).granted is True
    assert idempotency.claim(None, SID).granted is True


def test_recording_a_reply_never_raises_when_the_store_is_unreachable():
    idempotency.record_reply(BrokenFirestore(), SID, "<Response></Response>")
    idempotency.release(BrokenFirestore(), SID)
