"""Sanctioning a project read every report in the database and sent a WhatsApp
message per citizen inline. At scale that cannot finish inside the request
timeout, which would leave some citizens told and others not.
"""

import pytest

import main
from tests.fakes import FakeFirestore

TAG = "sewage_overflow"


def seed(db, specs):
    """specs: (zone, semantic_tag, category, sender)"""
    for i, (zone, tag, category, sender) in enumerate(specs):
        db.data.setdefault("messages", {})[f"m{i}"] = {
            "reference_id": f"UO{i:04d}",
            "constituency_zone": zone,
            "semantic_tag": tag,
            "category": category,
            "sender": sender,
            "status": "Open",
        }
    return db


def test_only_the_matching_cluster_is_selected():
    db = seed(FakeFirestore(), [
        ("North", TAG, "Infrastructure", "whatsapp:+911"),
        ("North", TAG, "Infrastructure", "whatsapp:+912"),
        ("South", TAG, "Infrastructure", "whatsapp:+913"),      # other zone
        ("North", "pothole", "Infrastructure", "whatsapp:+914"),  # other tag
    ])

    members = main.cluster_members(db, "North", TAG, "Infrastructure")

    assert {m.id for m in members} == {"m0", "m1"}


def test_falls_back_to_category_when_the_cluster_has_no_tag():
    db = seed(FakeFirestore(), [
        ("North", None, "Infrastructure", "whatsapp:+911"),
        ("North", None, "Environment", "whatsapp:+912"),
    ])

    members = main.cluster_members(db, "North", None, "Infrastructure")

    assert {m.id for m in members} == {"m0"}


def test_reports_outside_the_zone_are_never_read():
    """The zone filter is what keeps this off a full-collection scan."""
    db = seed(FakeFirestore(), [("North", TAG, "Infra", "whatsapp:+911")]
              + [("South", TAG, "Infra", f"whatsapp:+9{i}") for i in range(200)])

    members = main.cluster_members(db, "North", TAG, "Infra")

    assert len(members) == 1


def test_statuses_are_written_in_batches():
    db = seed(FakeFirestore(), [("North", TAG, "Infra", f"whatsapp:+9{i}") for i in range(1200)])
    members = main.cluster_members(db, "North", TAG, "Infra")

    main.mark_sanctioned(db, [m.reference for m in members])

    assert all(m["status"] == "Sanctioned" for m in db.data["messages"].values())
    # 1200 reports over a 500-op cap is 3 commits, not 1200 round trips.
    assert len(db.batches) == 3
    assert all(b.commits == 1 for b in db.batches)


def test_a_failed_batch_falls_back_to_individual_writes():
    db = seed(FakeFirestore(), [("North", TAG, "Infra", "whatsapp:+911")])
    members = main.cluster_members(db, "North", TAG, "Infra")

    def broken_batch():
        raise RuntimeError("batch unavailable")

    db.batch = broken_batch

    main.mark_sanctioned(db, [m.reference for m in members])

    assert db.data["messages"]["m0"]["status"] == "Sanctioned"


def test_notifications_are_deduplicated_and_sent_once_per_citizen(monkeypatch):
    sent = []

    class FakeMessages:
        def create(self, from_, to, body):
            sent.append((to, body))

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    monkeypatch.setattr(main, "Client", FakeClient)
    monkeypatch.setattr(main, "TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setattr(main, "TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setattr(main, "TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")

    count = main.notify_sanctioned(["whatsapp:+911", "whatsapp:+912"], "New drain", "tag|North")

    assert count == 2
    assert {to for to, _ in sent} == {"whatsapp:+911", "whatsapp:+912"}


def test_notification_text_is_not_mojibake(monkeypatch):
    sent = []

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = type("M", (), {"create": lambda _s, from_, to, body: sent.append(body)})()

    monkeypatch.setattr(main, "Client", FakeClient)
    monkeypatch.setattr(main, "TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setattr(main, "TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setattr(main, "TWILIO_WHATSAPP_NUMBER", "whatsapp:+1")

    main.notify_sanctioned(["whatsapp:+911"], "New drain", "tag|North")

    body = sent[0]
    assert "ðŸ" not in body and "â€" not in body, "citizen-facing text must not be mojibake"
    assert "🎉" in body and "—" in body


def test_one_failed_send_does_not_stop_the_rest(monkeypatch):
    attempted = []

    class FlakyMessages:
        def create(self, from_, to, body):
            attempted.append(to)
            if to == "whatsapp:+912":
                raise RuntimeError("twilio rejected this number")

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FlakyMessages()

    monkeypatch.setattr(main, "Client", FakeClient)
    monkeypatch.setattr(main, "TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setattr(main, "TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setattr(main, "TWILIO_WHATSAPP_NUMBER", "whatsapp:+1")

    count = main.notify_sanctioned(["whatsapp:+911", "whatsapp:+912", "whatsapp:+913"], "t", "p")

    assert attempted == ["whatsapp:+911", "whatsapp:+912", "whatsapp:+913"]
    assert count == 2


def test_nothing_is_sent_when_twilio_is_not_configured(monkeypatch):
    monkeypatch.setattr(main, "TWILIO_ACCOUNT_SID", "")
    assert main.notify_sanctioned(["whatsapp:+911"], "t", "p") == 0
