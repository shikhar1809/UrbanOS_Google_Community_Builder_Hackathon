from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import analytics
import main
import operations
from tests.dashboard_fixtures import DEMO_RECIPIENT, RECIPIENT, dashboard_db, report, use_local_desk_writes


NOW = datetime(2026, 11, 9, 12)


def row(key, **fields):
    return analytics.normalize_record(key, {
        "responsible_body": "Jal Sansthan", "category": "Water & Sanitation",
        "timestamp": datetime(2026, 7, 1), "status": "Open", "priority": "High", **fields,
    })


def test_estimate_uses_valid_closures_in_the_same_department_category_and_source():
    submitted = datetime(2026, 7, 1)
    records = [row("active", simulated=True, priority="Critical")]
    records += [row(str(hours), status="Completed", simulated=True,
                    resolved_at=submitted + timedelta(hours=hours)) for hours in (24, 48, 72, 96, 120)]
    records += [
        row("old", status="Completed", simulated=True),
        row("invalid", status="Completed", simulated=True, resolved_at=submitted - timedelta(days=1)),
        row("future", status="Completed", simulated=True, resolved_at=NOW + timedelta(days=1)),
        row("other-dept", status="Completed", simulated=True, responsible_body="PWD", resolved_at=submitted),
        row("other-category", status="Completed", simulated=True, category="Electricity", resolved_at=submitted),
        row("signed", status="Completed", intake_origin="signed_twilio", resolved_at=submitted),
    ]
    demo = operations.records_for(records, "demo", NOW)
    [item] = operations.queue(demo, "demo", "all", "", 0, 20, NOW)["items"]
    assert item["estimate"] == {"sample_size": 5, "minimum_sample": 5, "low_hours": 48, "high_hours": 96}
    sparse = [row for row in demo if row["id"] != "120"]
    [item] = operations.queue(sparse, "demo", "all", "", 0, 20, NOW)["items"]
    assert item["estimate"]["sample_size"] < 5
    assert "low_hours" not in item["estimate"]


def test_history_uses_month_end_dates_and_excludes_unknown_closures_from_backlog():
    records = [
        row("baseline", timestamp="2025-06-01T00:00:00+05:30", status="Completed", resolved_at="2025-06-02T00:00:00+05:30"),
        row("last-year", timestamp="2025-07-09", status="Completed", resolved_at="2025-07-17"),
        row("cross-boundary", timestamp="2026-07-30T08:00:00+05:30", status="Completed", resolved_at="2026-07-31T20:00:00Z"),
        row("legacy", timestamp="2026-07-05", status="Completed"),
        row("still-open", timestamp="2026-07-31", status="Open"),
        row("sanctioned", timestamp="2026-08-05", status="Sanctioned"),
        row("undated", timestamp=None),
    ]
    result = operations.history(records, "", NOW, False)
    months = {month["month"]: month for month in result["monthly"]}
    assert months["2026-07"] == {"month": "2026-07", "filed": 3, "closed": 0, "backlog": 2, "prior_year_filed": 1}
    assert months["2026-08"]["closed"] == 1
    assert months["2026-08"]["backlog"] == 2
    assert result["missing_filing_dates"] == result["missing_closure_dates"] == 1
    assert result["current_unresolved"] == 3
    assert "2026-11" not in months


def test_recent_archive_cannot_supply_a_prior_year_or_partial_month_baseline():
    result = operations.history([row("new", timestamp="2026-10-15")], "", NOW, True)
    assert result["monthly"][-1] == {"month": "2026-10", "filed": None, "closed": None,
                                     "backlog": None, "prior_year_filed": None}
    assert result["truncated"]


@pytest.fixture
def desk_client(monkeypatch):
    db = dashboard_db()
    use_local_desk_writes(db, monkeypatch)
    monkeypatch.setitem(main.app.dependency_overrides, main.get_db, lambda: db)
    analytics.invalidate_snapshot()
    with TestClient(main.app) as client:
        yield client, db
    analytics.invalidate_snapshot()


def test_desk_actions_are_ordered_idempotent_and_visible_in_feed(desk_client):
    client, db = desk_client
    base = "/api/operations/queue/demo"
    assert client.post(f"{base}/resolve").status_code == 409
    assert client.post(f"{base}/acknowledge").json()["stage"] == "acknowledged"
    first_ack = db.data["messages"]["demo"]["desk_acknowledged_at"]
    assert client.post(f"{base}/acknowledge").status_code == 200
    assert db.data["messages"]["demo"]["desk_acknowledged_at"] == first_ack
    assert client.post(f"{base}/resolve").json()["stage"] == "resolved"
    first_close = db.data["messages"]["demo"]["resolved_at"]
    assert client.post(f"{base}/resolve").status_code == 200
    assert db.data["messages"]["demo"]["resolved_at"] == first_close
    assert client.get("/api/operations/queue").json()["total"] == 0
    [focused] = client.get("/api/operations/queue", params={"reference": "#qa-demo"}).json()["items"]
    assert focused["stage"] == "resolved" and focused["reference"] == "QA-DEMO"
    [case] = client.get("/api/operations/queue", params={"stage": "resolved"}).json()["items"]
    assert case["resolved_at"] and case["action"] is None
    [feed_case] = client.get("/api/feed", params={"q": "QA-DEMO"}).json()["items"]
    assert feed_case["status"] == "Completed" and feed_case["desk_acknowledged_at"]


def test_desk_writes_cannot_touch_signed_legacy_or_incomplete_triage(desk_client):
    client, db = desk_client
    db.data["messages"]["real"].update(intake_origin="signed_twilio", responsible_body="PWD")
    db.data["messages"]["flagged"] = report("QA-FLAGGED", simulated=True, sender=RECIPIENT, responsible_body="PWD")
    db.data["messages"]["unclassified"] = report("QA-IMAGE", simulated=True, sender=DEMO_RECIPIENT,
                                                       responsible_body="Needs triage", triage_status="failed")
    [signed] = client.get("/api/operations/queue", params={"source": "signed"}).json()["items"]
    assert signed["action"] is None
    for case in ("real", "resolved", "flagged"):
        assert client.post(f"/api/operations/queue/{case}/acknowledge").status_code == 403
    assert client.post("/api/operations/queue/unclassified/acknowledge").status_code == 409
    assert client.post("/api/operations/queue/missing/acknowledge").status_code == 404
    assert client.post("/api/operations/queue/demo/invalid").status_code == 422


def test_example_data_is_read_only_and_displays_both_estimates_and_history(desk_client):
    client, _ = desk_client
    params = {"source": "examples", "department": "Jal Sansthan"}
    queue = client.get("/api/operations/queue", params=params)
    [example] = queue.json()["items"]
    assert example["estimate"]["sample_size"] >= 5
    assert example["action"] is None
    history = client.get("/api/operations/history", params=params).json()
    assert len(history["monthly"]) == 12
    assert all(month["prior_year_filed"] is not None for month in history["monthly"])
    assert RECIPIENT not in queue.text and DEMO_RECIPIENT not in queue.text
