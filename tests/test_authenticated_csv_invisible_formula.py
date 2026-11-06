"""Leading invisible Unicode before a CSV formula must not produce a clean attachment receipt.

This tests the authenticated tenant API using actual synthetic UTF-8 file bytes.
It does not claim spreadsheet execution or semantic interpretation.
"""
import base64

import pytest
from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.tenant_record import TenantRecord, demo_key, key_hash, usage_month
from guardoath.tenant_store import MemoryTenantStore


SECRET = "synthetic-invisible-formula-ingress-0123456789"
HIDDEN_FORMULA = "label,value\nSynthetic,\u200b=2+3".encode("utf-8")
VISIBLE_FORMULA = b"label,value\nSynthetic,=4+5"
BENIGN_JOINER = "label,value\nSynthetic,👩\u200d🔬".encode("utf-8")


def _harness(*, mode="enforce", attachment_mode="auto"):
    tenant_id = "csv-invisible-test"
    token = demo_key(tenant_id, SECRET)
    record = TenantRecord(
        tenant_id=tenant_id, display_name="Hidden CSV Formula", demo=True,
        key_hashes=(key_hash(token),), monthly_allowance=12, overage="block",
        mode=mode, input_check_csv_formula="flag", output_check_csv_formula="block",
        input_attachment_csv_mode=attachment_mode,
        output_attachment_csv_mode=attachment_mode,
    )
    service = TenantService(MemoryTenantStore((record,)), default_checkers(), SECRET)
    client = TestClient(create_app(engine=demo_engine(), credentials={}, tenants=service))
    return client, service, record, token


def _check(client, record, token, stage, files):
    return client.post(
        f"/v1/checks/{stage}/attachments",
        headers={"Authorization": "Bearer " + token},
        json={
            "application_id": record.tenant_id, "content": "Inspect synthetic CSV.",
            "attachments_b64": [base64.b64encode(value).decode("ascii") for value in files],
        },
    )


@pytest.mark.parametrize("stage", ["input", "output"])
@pytest.mark.parametrize("mode", ["enforce", "observe"])
def test_hidden_formula_blocks_both_stages_and_preserves_independent_findings(stage, mode):
    client, service, record, token = _harness(mode=mode)
    response = _check(client, record, token, stage, (HIDDEN_FORMULA, VISIBLE_FORMULA))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["recommended_action"] == data["effective_action"] == "block"
    assert data["evaluation_status"] == "incomplete"
    assert len(data["attachments"]) == 2
    assert data["attachments"][0]["status"] == "invalid"
    assert data["attachments"][0]["evaluation_status"] == "incomplete"
    assert data["attachments"][0]["reason"] == "unsafe_csv_invisible_formula_prefix"
    assert data["attachments"][1]["evaluation_status"] == "complete"
    assert "csv_formula.potential_formula" in {item["rule_id"] for item in data["findings"]}
    assert any(result["checker_id"] == "attachment_probe" and result["status"] == "error"
               for result in data["checker_results"])
    assert "Synthetic" not in response.text and "=2+3" not in response.text
    assert service.store.get(record.tenant_id).used(usage_month()) == 1


@pytest.mark.parametrize("stage", ["input", "output"])
def test_attachment_off_cannot_attest_hidden_formula_as_clean(stage):
    client, service, record, token = _harness(mode="observe", attachment_mode="off")
    response = _check(client, record, token, stage, (HIDDEN_FORMULA,))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["recommended_action"] == data["effective_action"] == "block"
    assert data["evaluation_status"] == "incomplete"
    assert data["attachments"][0]["reason"] == "unsafe_csv_invisible_formula_prefix"
    assert not any(item["rule_id"].startswith("csv_formula.") for item in data["findings"])
    assert service.store.get(record.tenant_id).used(usage_month()) == 1


def test_legitimate_unicode_joiner_is_recognized_with_no_false_formula_finding():
    client, service, record, token = _harness(mode="enforce")
    response = _check(client, record, token, "input", (BENIGN_JOINER,))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["attachments"][0]["status"] == "recognized"
    assert data["attachments"][0]["evaluation_status"] == "complete"
    assert data["effective_action"] == "allow"
    assert not any(finding["rule_id"].startswith("csv_formula.") for finding in data["findings"])
    assert service.store.get(record.tenant_id).used(usage_month()) == 1
