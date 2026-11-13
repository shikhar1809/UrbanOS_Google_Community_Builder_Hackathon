import re
from datetime import datetime, timedelta

import h3
import pytest
from fastapi.testclient import TestClient

import analytics
import main
import planning_review
from tests.dashboard_fixtures import DEMO_RECIPIENT, RECIPIENT, dashboard_db, report


@pytest.fixture
def db():
    return dashboard_db()


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setitem(main.app.dependency_overrides, main.get_db, lambda: db)
    monkeypatch.setattr(main.LLM, "get_provider", lambda: None)
    analytics.invalidate_snapshot()
    with TestClient(main.app) as client:
        yield client
    analytics.invalidate_snapshot()


def test_ranking_counts_only_each_reporters_highest_priority():
    records = [analytics.normalize_record(str(index), row) for index, row in enumerate([
        report("QA-1", priority="Low"),
        report("QA-2", priority="Critical"),
        report("QA-3", sender="whatsapp:+919823456789", priority="High"),
        report("QA-4", sender="", priority="Critical"),
    ])]

    projects, total = planning_review.recommendations(records, "legacy")

    assert total == 4
    assert len(projects) == 1
    assert projects[0]["unique_reporters"] == 2
    assert projects[0]["uncounted_reports"] == 1
    assert projects[0]["score"] == 7


def test_gps_groups_cross_hex_boundaries_and_keep_unrelated_incidents_separate():
    lng = 80.9981
    first, second = 26.85502, 26.85504
    assert h3.latlng_to_cell(first, lng, 9) != h3.latlng_to_cell(second, lng, 9)
    now = datetime(2026, 11, 10, 12)
    cases = [(first, "water_supply", now), (second, "water_supply", now + timedelta(minutes=1)),
             (first, "road_repair", now), (first + 0.006, "water_supply", now),
             (first, "water_supply", now + timedelta(days=4))]
    records = [analytics.normalize_record(str(i), report(
        f"QA-{i}", sender=f"whatsapp:+150055500{10 + i}", latitude=lat, longitude=lng,
        timestamp=stamp, semantic_tag=issue, intake_origin=planning_review.SIGNED_INTAKE,
    )) for i, (lat, issue, stamp) in enumerate(cases)]

    projects, total = planning_review.recommendations(records, "verified")
    assert total == 5
    assert [project["report_count"] for project in projects] == [2, 1, 1, 1]
    assert {row["reference_id"] for row in projects[0]["reports"]} == {"QA-0", "QA-1"}
    assert projects[0]["unique_reporters"] == 2
    assert projects == planning_review.recommendations(list(reversed(records)), "verified")[0]


def test_open_critical_queue_excludes_completed_reports(client):
    response = client.get("/api/feed", params={"priority": "Critical", "status": "open"})
    assert response.status_code == 200
    assert response.json()["filtered_total"] == 1
    assert [item["reference_id"] for item in response.json()["items"]] == ["QA-OPEN"]


def test_insights_coverage_describes_the_same_report_window(client):
    dashboard = client.get("/api/analytics", params={"range": "7d"}).json()
    answer = client.post("/api/chat", json={"query": "Show the trend of reports in the last 7 days"}).json()

    assert dashboard["headline"]["total"] == answer["range"]["scoped_total"] == 3
    assert dashboard["coverage"]["simulated_count"] == answer["range"]["simulated_count"] == 1
    assert answer["range"]["matched_timeframe"] is True
    assert answer["chart"]["labels"]


def test_insights_empty_scope_has_no_map_evidence_or_chart(client):
    response = client.post("/api/chat", json={"query": "How many reports were filed in Ward 999?"})
    assert response.status_code == 200
    answer = response.json()
    assert "No reports match" in answer["response"]
    assert answer["chart"] is None
    assert answer["map_filter"]["ids"] == []


def test_planning_download_includes_candidates_beyond_the_first_page(client, db):
    db.data["messages"] = {str(i): report(f"QA-{i}", location_source="text") for i in range(26)}
    first_page = client.get("/api/planning/recommendations", params={"scope": "legacy"}).json()
    response = client.get("/api/planning/recommendations/export", params={"scope": "legacy"})

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="urbanos-recommendations-legacy.txt"'
    assert response.headers["cache-control"] == "no-store"
    assert len(first_page["projects"]) == 12
    assert first_page["total"] == 26
    assert len(re.findall(r"^\d+\. ", response.text, flags=re.MULTILINE)) == 26
    assert response.text.count("Recommended next check:") == 26
    assert RECIPIENT not in response.text


def test_planning_download_handles_empty_and_invalid_sources(client):
    empty = client.get("/api/planning/recommendations/export", params={"scope": "verified"})
    assert empty.status_code == 200
    assert "No open recommendations" in empty.text
    assert client.get("/api/planning/recommendations/export", params={"scope": "mixed"}).status_code == 422


def test_survey_preview_and_dry_run_exclude_demo_recipients(client, db, monkeypatch):
    def reject_twilio(*args, **kwargs):
        pytest.fail("A preview or dry run attempted to initialize Twilio")

    monkeypatch.setattr(main, "Client", reject_twilio)
    before = db.data["surveys"]["qa"].copy()
    preview = client.post("/surveys/audience/preview", json={})
    dry_run = client.post("/surveys/qa/broadcast", json={"dry_run": True})

    assert preview.status_code == dry_run.status_code == 200
    assert preview.json()["recipient_count"] == 2
    assert preview.json()["eligible_recipient_count"] == dry_run.json()["would_send_to"] == 1
    assert RECIPIENT not in preview.text and DEMO_RECIPIENT not in preview.text
    assert db.data["surveys"]["qa"] == before
