from datetime import datetime, timezone

from fastapi.testclient import TestClient

import analytics
import jobs
import main
import sim
from tests.fakes import FakeFirestore


def test_live_cursor_carries_triage_updates_and_handles_timestamp_ties(monkeypatch):
    db = FakeFirestore()
    monkeypatch.setitem(main.app.dependency_overrides, main.get_db, lambda: db)
    stamp = datetime.now(timezone.utc)
    db.data["messages"] = {
        f"report-{i:03d}": {
            jobs.LIVE_UPDATED_FIELD: stamp,
            "triage_status": jobs.STATUS_PENDING,
            "sender": "whatsapp:+919812345678",
        } for i in range(310)
    }
    client = TestClient(main.app)

    first = client.get("/api/feed/live").json()
    assert len(first["items"]) == 300
    assert first["has_more"]
    second = client.get("/api/feed/live", params={"cursor": first["cursor"]}).json()
    assert not second["has_more"]
    assert [r["id"] for r in second["items"]] == [f"report-{i:03d}" for i in range(300, 310)]
    assert not {"sender", "phone_number"} & first["items"][0].keys()

    db.data["messages"]["report-000"].update({
        jobs.LIVE_UPDATED_FIELD: datetime.now(timezone.utc),
        "triage_status": jobs.STATUS_DONE,
    })
    third = client.get("/api/feed/live", params={"cursor": second["cursor"]}).json()
    assert [r["id"] for r in third["items"]] == ["report-000"]
    assert third["items"][0]["triage_status"] == "done"


def test_live_feed_rejects_an_invalid_cursor():
    response = TestClient(main.app).get("/api/feed/live", params={"cursor": "invalid"})
    assert response.status_code == 400



def test_cleanup_signal_refreshes_a_warm_snapshot(monkeypatch):
    db = FakeFirestore()
    monkeypatch.setitem(main.app.dependency_overrides, main.get_db, lambda: db)
    stamp = datetime.now(timezone.utc)
    db.data["messages"] = {
        "real": {"timestamp": stamp.isoformat()},
        "demo": {"timestamp": stamp.isoformat(), "simulated": True},
    }
    analytics.get_snapshot(db, force=True)
    db.collection("messages").document("demo").delete()
    db.collection(sim.CLEANUP_COLLECTION).document(sim.CLEANUP_DOC).set({"at": stamp})
    monkeypatch.setitem(analytics._cleanup_cache, "checked_at", 0.0)
    client = TestClient(main.app)

    signal = client.get("/api/feed/live").json()
    assert signal["demo_cleanup_at"] == analytics.live_version(stamp)
    assert [item["id"] for item in client.get("/messages").json()] == ["real"]
