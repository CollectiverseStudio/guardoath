"""Saved CSV-export contract and authenticated API coverage, synthetic inputs only."""
import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from guardoath.app import create_app
from guardoath.checkers import default_checkers
from guardoath.config import load_policies
from guardoath.engine import Engine
from guardoath.evaluate import evaluate, load_suite
from guardoath.models import CheckRequest, Stage


ROOT = Path(__file__).resolve().parents[1]
POLICIES = ROOT / "policies" / "csv-export.json"
SUITE = ROOT / "evals" / "csv-export.json"


def test_csv_export_saved_suite_matches_engine_with_no_raw_export_data():
    report = asyncio.run(evaluate(load_suite(SUITE), Engine(load_policies(POLICIES), default_checkers())))
    assert report["summary"] == {
        "total": 36, "matched": 36, "mismatched": 0, "incomplete": 10, "simulated": 0
    }, [(row["case_id"], row["differences"]) for row in report["cases"] if not row["matched"]]
    serialized = json.dumps(report)
    assert "HYPERLINK" not in serialized
    assert "=1+1" not in serialized
    assert all("content" not in row and "request" not in row for row in report["cases"])


def test_csv_export_policy_loads_as_dedicated_opt_in_checker():
    policies = load_policies(POLICIES)
    assert len(policies) == 1
    policy = policies[0]
    assert policy.mode == "enforce"
    assert [rule.checker_id for rule in policy.rules] == ["csv_formula"]
    assert policy.rules[0].on_finding == "block"
    response = asyncio.run(Engine(policies, default_checkers()).check(
        CheckRequest(application_id="demo", policy_version=policy.version,
                     content="item,description\n1,ordinary value"),
        Stage.INPUT,
    ))
    assert response.evaluation_status == "complete"
    assert response.effective_action == "allow"


def test_csv_export_authenticated_api_enforce_and_error_contract():
    app = create_app(
        engine=Engine(load_policies(POLICIES), default_checkers()),
        credentials={"synthetic-csv-key": "demo"},
    )
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer synthetic-csv-key"}
        for endpoint in ("/v1/checks/input", "/v1/checks/output"):
            data = {
                "application_id": "demo", "policy_version": "csv-export-v1",
                "content": 'name,entry\nA,"=1+2"', "content_format": "text",
            }
            response = client.post(endpoint, headers=headers, json=data)
            assert response.status_code == 200, response.status_code
            payload = response.json()
            assert payload["evaluation_status"] == "complete"
            assert payload["recommended_action"] == "block"
            assert payload["effective_action"] == "block"
            assert {item["rule_id"] for item in payload["findings"]} == {"csv_formula.potential_formula"}
            assert "=1+2" not in response.text
        invalid = client.post("/v1/checks/output", headers=headers, json={
            "application_id": "demo", "policy_version": "csv-export-v1",
            "content": 'name,entry\nA,"unterminated', "content_format": "text",
        })
        assert invalid.status_code == 200
        assert invalid.json()["evaluation_status"] == "incomplete"
        assert invalid.json()["effective_action"] == "block"
        assert invalid.json()["checker_results"][0]["error_code"] == "csv_invalid_content"
        # A different key cannot run the export policy or read results.
        unauthorized = client.post("/v1/checks/output", headers={
            "Authorization": "Bearer wrong-synthetic-key",
        }, json={
            "application_id": "demo", "policy_version": "csv-export-v1",
            "content": "name,entry\\nA,=1+2", "content_format": "text",
        })
        assert unauthorized.status_code in (401, 403)
        assert "csv_formula.potential_formula" not in unauthorized.text
