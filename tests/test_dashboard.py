from fastapi.testclient import TestClient

from valinator.app import create_app
from valinator.checkers import default_checkers
from valinator.engine import Engine
from valinator.models import Policy, Rule


def test_dashboard_and_local_assets_load():
    client = TestClient(create_app(credentials={"test-key": "demo"}))
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "Check playground" in response.text
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    for asset in ["dashboard.css", "dashboard.js"]:
        assert client.get("/assets/" + asset).status_code == 200


def test_dashboard_metadata_requires_auth_and_is_application_scoped():
    policies = [Policy(application_id="demo", version="public-v1", rules=(Rule(checker_id="safety"),)),
                Policy(application_id="other", version="private-v1", rules=())]
    client = TestClient(create_app(engine=Engine(policies, default_checkers()), credentials={"test-key": "demo"}))
    assert client.get("/v1/applications/current").status_code == 401
    response = client.get("/v1/applications/current", headers={"Authorization": "Bearer test-key"})
    assert response.status_code == 200
    assert "private-v1" not in response.text
    assert response.json()["policies"][0]["checks"][0]["simulated"] is True
    assert "test-key" not in response.text
