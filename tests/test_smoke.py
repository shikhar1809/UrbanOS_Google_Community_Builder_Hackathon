from fastapi.testclient import TestClient

from main import DEMO_DEMOGRAPHICS, app, project_gap_signal, verify_admin


def test_static_and_health_routes():
    app.dependency_overrides[verify_admin] = lambda: {"uid": "test", "email": "admin@example.com"}
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/sw.js").status_code == 200
    assert client.get("/favicon.svg").status_code == 200


def test_project_gap_signal_matches_project_type():
    signal = project_gap_signal(
        "Infrastructure Upgrade",
        "road_repair",
        "Broken bridge near main road",
        DEMO_DEMOGRAPHICS["West"],
    )

    assert signal["key"] == "road_connectivity"
    assert signal["normalized_gap"] == DEMO_DEMOGRAPHICS["West"]["road_gap_index"]
