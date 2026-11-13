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


def test_sourced_examples_have_traceable_historical_evidence(client, monkeypatch):
    monkeypatch.setitem(main.app.dependency_overrides, main.get_db, lambda: FakeFirestore())
    base = "/api/planning/recommendations"
    payload = client.get(base).json()
    first, second = payload["projects"]
    assert payload["scope"] == "examples"
    assert (first["rank"], first["score"], first["report_count"]) == (1, 6, 2)
    assert (second["rank"], second["score"], second["report_count"]) == (2, 4, 3)
    assert all(26.7 < p["map_location"]["latitude"] < 27 and 80.8 < p["map_location"]["longitude"] < 81.2
               for p in (first, second))

    detail = client.get(f"{base}/{first['id']}?limit=1").json()
    assert detail["next_offset"] == 1
    assert detail["evidence"]["publisher"] == "Lucknow Municipal Corporation"
    assert detail["evidence"]["published"] == "January 2015"
    assert "Current school access needs field verification" in detail["evidence"]["limitation"]
    assert detail["reports"][0]["triage_source"] == "example"
    assert client.get(f"{base}/{first['id']}?scope=verified").status_code == 404


def test_review_groups_exact_locations_and_counts_each_reporter_once(client, monkeypatch):
    db = seed(FakeFirestore(), 5)
    reports = db.data["messages"]
    for report in reports.values():
        report.update(intake_origin=main.REVIEW.SIGNED_INTAKE, location_source="gps_pin",
                      latitude=26.8534, longitude=80.9981, status="Open")
    reports["m00000"].update(sender="whatsapp:+919000000001", priority="Low", intake_formats=["Text", "GPS pin"])
    reports["m00001"].update(sender="+919000000001", priority="Critical", intake_formats=["Voice", "Photo", "GPS pin"])
    reports["m00002"].update(priority="Medium", intake_formats=["Text", "Photo", "GPS pin"])
    reports["m00003"].update(latitude=26.92, longitude=80.95)
    reports["m00004"].update(location_source="text")
    monkeypatch.setitem(main.app.dependency_overrides, main.get_db, lambda: db)

    base = "/api/planning/recommendations"
    projects = client.get(f"{base}?scope=verified").json()["projects"]
    assert len(projects) == 3
    first = projects[0]
    assert first["score"] == 6
    assert first["report_count"] == 3
    assert first["unique_reporters"] == 2
    assert {project["location_basis"] for project in projects} == {"gps_area", "unconfirmed"}
    assert first["map_location"] is not None
    assert next(project for project in projects if project["location_basis"] == "unconfirmed")["map_location"] is None
    detail = client.get(f"{base}/{first['id']}?scope=verified").json()
    assert {kind for report in detail["reports"] for kind in report["formats"]} == {"Text", "Voice", "Photo", "GPS pin"}
    assert all("sender" not in report for report in detail["reports"])
    assert "+919000000001" not in str(detail)


def test_review_scopes_never_mix_demo_signed_and_legacy_demand(client, monkeypatch):
    db = seed(FakeFirestore(), 6)
    reports = db.data["messages"]
    for report in reports.values():
        report.update(extracted_location="Jankipuram", location_source="gps_pin",
                      latitude=26.9195, longitude=80.9450)
    reports["m00000"]["intake_origin"] = main.REVIEW.SIGNED_INTAKE
    reports["m00001"].update(simulated=True, intake_origin=main.REVIEW.SIGNED_INTAKE)
    reports["m00002"].update(sender="whatsapp:+999000000001", location_source="text")
    reports["m00003"].update(intake_origin=main.REVIEW.SIGNED_INTAKE, status="Completed")
    reports["m00004"].update(intake_origin=main.REVIEW.SIGNED_INTAKE, sender=None)
    monkeypatch.setitem(main.app.dependency_overrides, main.get_db, lambda: db)

    base = "/api/planning/recommendations?scope="
    signed = client.get(base + "verified").json()
    demo = client.get(base + "demo").json()
    legacy = client.get(base + "legacy").json()
    assert (signed["report_count"], demo["report_count"], legacy["report_count"]) == (2, 2, 1)
    assert signed["projects"][0]["unique_reporters"] == 1
    assert signed["projects"][0]["uncounted_reports"] == 1
    assert demo["projects"][0]["location_basis"] == "demo_site"
    assert demo["projects"][0]["report_count"] == 2
    assert client.get(base + "all").status_code == 422


def test_demo_volume_cannot_push_signed_reports_out_of_planning(client, monkeypatch):
    db = seed(FakeFirestore(), 6)
    reports = db.data["messages"]
    reports["m00000"]["intake_origin"] = main.REVIEW.SIGNED_INTAKE
    for i in range(1, 6):
        reports[f"m{i:05d}"]["simulated"] = True
    monkeypatch.setattr(analytics, "SNAPSHOT_MAX_RECORDS", 2)
    monkeypatch.setitem(main.app.dependency_overrides, main.get_db, lambda: db)

    assert client.get("/api/planning/recommendations?scope=demo").json()["truncated"]
    signed = client.get("/api/planning/recommendations?scope=verified").json()
    assert signed["report_count"] == 1
    assert signed["truncated"] is False



def test_planning_totals_cover_clusters_outside_the_top_eight(client):
    db = seed(FakeFirestore(), 10)
    for i, report in enumerate(db.data["messages"].values()):
        report.update(semantic_tag=f"issue_{i}", estimated_budget="₹1,00,000", status="Sanctioned")
    main.app.dependency_overrides[main.get_db] = lambda: db

    result = client.get("/projects/ranked?include_totals=true").json()
    reports = client.get("/messages").json()

    assert len(result["projects"]) == 8
    assert result["totals"]["sanctioned_projects"] == 10
    assert result["totals"]["sanctioned_reports"] == 10
    assert result["totals"]["requested_report_estimate"] == sum(r["estimated_budget"] for r in reports) == 1_000_000
    assert result["totals"]["sanctioned_report_estimate"] == 1_000_000


def test_project_status_and_location_evidence_are_based_on_the_cluster(client):
    db = seed(FakeFirestore(), 4, zone="Gomti Nagar")
    reports = db.data["messages"]
    reports["m00001"]["status"] = "Sanctioned"
    reports["m00002"]["constituency_zone"] = "Chowk"
    reports["m00003"]["constituency_zone"] = "Unmapped locality"
    main.app.dependency_overrides[main.get_db] = lambda: db

    projects = {p["zone"]: p for p in client.get("/projects/ranked").json()}

    east = projects["Gomti Nagar"]
    assert east["status"] == "Partially sanctioned"
    assert east["impact_score"] == round(6 * 1.64, 1)
    assert "East" in east["evidence"]["gap_signal"]["source"]
    assert "Central" in projects["Chowk"]["evidence"]["gap_signal"]["source"]
    assert projects["Unmapped locality"]["evidence"]["gap_signal"] is None


def test_random_seed_priority_does_not_make_routine_park_work_urgent(client):
    db = seed(FakeFirestore(), 7)
    for i in range(5):
        db.data["messages"][f"m{i:05d}"].update(
            category="Parks & Rec", semantic_tag="broken swings", summary="Broken swings", priority="Critical",
        )
    for key in ("m00005", "m00006"):
        db.data["messages"][key].update(priority="Critical")
    main.app.dependency_overrides[main.get_db] = lambda: db

    projects = client.get("/projects/ranked").json()

    assert [p["semantic_tag"] for p in projects] == ["sewage_overflow", "broken swings"]
    assert projects[1]["evidence"]["priority_score"] == 5



def test_proposal_link_accepts_whitespace_around_the_configured_number(client, monkeypatch):
    db = seed(FakeFirestore(), 1)
    main.app.dependency_overrides[main.get_db] = lambda: db
    monkeypatch.setattr(main, "TWILIO_WHATSAPP_NUMBER", "whatsapp:+15550102030\n")

    result = client.get("/projects/ranked?include_totals=true").json()

    assert result["proposal_url"] == "https://wa.me/15550102030?text=Hi"
