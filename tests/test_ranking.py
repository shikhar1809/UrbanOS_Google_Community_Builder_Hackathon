"""Ranking drives which projects an MP sanctions, so which reports it sees is a
correctness question, not a performance one. It used to read `limit(500)` with
no ordering, which Firestore answers in document-id order: past 500 reports the
ranking silently covered an arbitrary subset.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import analytics
import main
from tests.fakes import FakeFirestore

BASE = datetime(2026, 1, 1, 10, 0, 0)


@pytest.fixture(autouse=True)
def fresh_caches():
    analytics.invalidate_snapshot()
    main._RANKED_CACHE.update({"data": None, "at": 0.0, "version": None})
    yield
    analytics.invalidate_snapshot()
    main._RANKED_CACHE.update({"data": None, "at": 0.0, "version": None})


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "gemini_client", None)
    monkeypatch.setattr(main, "_groq_client", None)
    main.app.dependency_overrides[main.verify_admin] = lambda: {"email": "mp@example.com"}
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def seed(db, count, zone="North", tag="sewage_overflow"):
    for i in range(count):
        db.data.setdefault("messages", {})[f"m{i:05d}"] = {
            "reference_id": f"UO{i:05d}",
            "timestamp": (BASE + timedelta(minutes=i)).isoformat(),
            "constituency_zone": zone,
            "semantic_tag": tag,
            "category": "Infrastructure",
            "priority": "High",
            "estimated_budget": 100000,
            "sender": f"whatsapp:+9199{i:08d}",
            "body": "sewage overflowing",
            "summary": "Sewage overflow",
            "status": "Open",
        }
    return db


def test_ranking_counts_every_report_not_just_the_first_500(client, monkeypatch):
    """900 reports in one cluster must all count toward its demand."""
    db = seed(FakeFirestore(), 900)
    main.app.dependency_overrides[main.get_db] = lambda: db

    response = client.get("/projects/ranked")

    assert response.status_code == 200
    projects = response.json()
    assert projects, "expected at least one ranked project"
    assert projects[0]["demand_count"] == 900, (
        "a limit(500) read would have reported 500 and understated demand"
    )


def test_ranking_is_recomputed_when_the_snapshot_changes(client):
    db = seed(FakeFirestore(), 10)
    main.app.dependency_overrides[main.get_db] = lambda: db

    first = client.get("/projects/ranked").json()
    assert first[0]["demand_count"] == 10

    seed(db, 20)                      # new reports arrive
    analytics.invalidate_snapshot()   # as the intake path does on write

    second = client.get("/projects/ranked").json()
    assert second[0]["demand_count"] == 20, "a stale cache would still say 10"


def test_cached_result_is_reused_while_the_snapshot_is_unchanged(client):
    db = seed(FakeFirestore(), 5)
    main.app.dependency_overrides[main.get_db] = lambda: db

    client.get("/projects/ranked")
    cached_at = main._RANKED_CACHE["at"]
    client.get("/projects/ranked")

    assert main._RANKED_CACHE["at"] == cached_at, "unchanged data should not be recomputed"


def test_clusters_are_split_by_zone(client):
    db = FakeFirestore()
    seed(db, 6, zone="North")
    for i in range(3):
        db.data["messages"][f"s{i}"] = dict(
            db.data["messages"]["m00000"],
            reference_id=f"UOS{i}",
            constituency_zone="South",
            sender=f"whatsapp:+918{i:09d}",
            timestamp=(BASE + timedelta(hours=5, minutes=i)).isoformat(),
        )
    main.app.dependency_overrides[main.get_db] = lambda: db

    projects = client.get("/projects/ranked").json()

    by_zone = {p["zone"]: p["demand_count"] for p in projects}
    assert by_zone.get("North") == 6
    assert by_zone.get("South") == 3
