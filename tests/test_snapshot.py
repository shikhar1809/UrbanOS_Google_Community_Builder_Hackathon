"""The analytics snapshot used to read the entire `messages` collection into
memory on every rebuild, in a 1GB function, once per instance per TTL. These
tests pin the bound that replaced it, and the correctness traps that come with
reading only a window.
"""

from datetime import datetime, timedelta

import pytest

import analytics
from tests.fakes import FakeFirestore


@pytest.fixture(autouse=True)
def fresh_snapshot():
    analytics.invalidate_snapshot()
    yield
    analytics.invalidate_snapshot()


BASE = datetime(2026, 1, 1, 10, 0, 0)


def seed(db, count, start_day=1, **extra):
    """Reports numbered oldest to newest, with strictly increasing timestamps so
    the ordering assertions mean something."""
    for i in range(count):
        day = start_day + i
        db.data.setdefault("messages", {})[f"msg-{day:05d}"] = {
            "reference_id": f"UO{day:05d}",
            "timestamp": (BASE + timedelta(minutes=day)).isoformat(),
            "sender": "whatsapp:+919800000000",
            "body": f"report {day}",
            "category": "Infrastructure",
            **extra,
        }
    return db


def test_snapshot_never_holds_more_than_the_cap(monkeypatch):
    monkeypatch.setattr(analytics, "SNAPSHOT_MAX_RECORDS", 50)
    db = seed(FakeFirestore(), 500)

    snap = analytics.get_snapshot(db, force=True)

    assert len(snap["records"]) == 50, "an unbounded read is what OOMs the function"


def test_total_count_stays_exact_even_when_truncated(monkeypatch):
    monkeypatch.setattr(analytics, "SNAPSHOT_MAX_RECORDS", 50)
    db = seed(FakeFirestore(), 500)

    snap = analytics.get_snapshot(db, force=True)

    assert snap["total_count"] == 500, "the dashboard must still show the true total"
    assert snap["truncated"] is True


def test_small_collections_are_read_whole_and_not_flagged(monkeypatch):
    monkeypatch.setattr(analytics, "SNAPSHOT_MAX_RECORDS", 5000)
    db = seed(FakeFirestore(), 120)

    snap = analytics.get_snapshot(db, force=True)

    assert len(snap["records"]) == 120
    assert snap["total_count"] == 120
    assert snap["truncated"] is False, "today's dataset must behave exactly as before"


def test_the_window_kept_is_the_newest_reports(monkeypatch):
    monkeypatch.setattr(analytics, "SNAPSHOT_MAX_RECORDS", 10)
    db = seed(FakeFirestore(), 100)

    snap = analytics.get_snapshot(db, force=True)
    kept = {r["reference_id"] for r in snap["records"]}

    assert "UO00100" in kept, "the newest report must survive truncation"
    assert "UO00001" not in kept, "the oldest should be the one trimmed"


def test_reports_without_a_timestamp_are_not_silently_dropped(monkeypatch):
    """order_by('timestamp') omits documents lacking the field. Older records
    may have none, and losing them would quietly shrink the civic dataset."""
    monkeypatch.setattr(analytics, "SNAPSHOT_MAX_RECORDS", 5000)
    db = seed(FakeFirestore(), 10)
    db.data["messages"]["legacy-no-ts"] = {
        "reference_id": "UOLEGACY",
        "sender": "whatsapp:+919800000001",
        "body": "old report with no timestamp",
        "category": "Infrastructure",
    }

    snap = analytics.get_snapshot(db, force=True)

    refs = {r["reference_id"] for r in snap["records"]}
    assert "UOLEGACY" in refs, "a report with no timestamp must still be counted"
    assert len(snap["records"]) == 11


def test_snapshot_is_cached_between_calls(monkeypatch):
    monkeypatch.setattr(analytics, "SNAPSHOT_MAX_RECORDS", 5000)
    db = seed(FakeFirestore(), 5)
    analytics.get_snapshot(db, force=True)
    version = analytics._SNAPSHOT["version"]

    analytics.get_snapshot(db)

    assert analytics._SNAPSHOT["version"] == version, "a cached read must not refetch"


def test_invalidate_forces_a_rebuild(monkeypatch):
    monkeypatch.setattr(analytics, "SNAPSHOT_MAX_RECORDS", 5000)
    db = seed(FakeFirestore(), 5)
    analytics.get_snapshot(db, force=True)
    version = analytics._SNAPSHOT["version"]

    analytics.invalidate_snapshot()
    analytics.get_snapshot(db)

    assert analytics._SNAPSHOT["version"] == version + 1


def test_count_falls_back_when_the_aggregation_is_unavailable():
    class NoAggregation(FakeFirestore):
        def collection(self, name):
            col = super().collection(name)
            col.count = lambda: (_ for _ in ()).throw(RuntimeError("not supported"))
            return col

    db = seed(NoAggregation(), 3)

    snap = analytics.get_snapshot(db, force=True)

    assert analytics.count_reports(db) is None
    assert snap["total_count"] == 3, "falls back to what was actually read"
    assert snap["truncated"] is False
