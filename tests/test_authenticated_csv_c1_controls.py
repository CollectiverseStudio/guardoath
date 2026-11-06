"""Authenticated CSV C1 control-code rejection through the tenant's existing API.

Only synthetic bytes are used; no second validator, tenant store or billing path.
C1 NEL/CSI/OSC bytes must never be returned or attested as inspected.
"""
import base64

import pytest
from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.tenant_record import TenantRecord, demo_key, key_hash, usage_month
from guardoath.tenant_store import MemoryTenantStore


SECRET = "synthetic-csv-c1-control-ingress-secret-0123456789"
VALID_FORMULA = b"label,value\nSynthetic,=2+3"


def _harness(*, mode="observe", attachment_mode="auto"):
    tenant_id = "c1-controlled-demo"
    token = demo_key(tenant_id, SECRET)
    record = TenantRecord(
        tenant_id=tenant_id, display_name="C1 CSV security", demo=True,
        mode=mode, monthly_allowance=15, overage="block",
        key_hashes=(key_hash(token),),
        input_check_csv_formula="flag", output_check_csv_formula="flag",
        input_attachment_csv_mode=attachment_mode,
        output_attachment_csv_mode=attachment_mode,
    )
    service = TenantService(MemoryTenantStore((record,)), default_checkers(), SECRET)
    client = TestClient(create_app(engine=demo_engine(), credentials={}, tenants=service))
    return client, service, record, token


def _submit(client, record, token, *, stage, files):
    return client.post(
        f"/v1/checks/{stage}/attachments",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "application_id": record.tenant_id,
            "content": "Review an attached synthetic CSV.",
            "attachments_b64": [base64.b64encode(data).decode("ascii") for data in files],
        },
    )


@pytest.mark.parametrize("control", ["\u0085", "\u009b", "\u009d", "\u009f"])
@pytest.mark.parametrize("stage", ["input", "output"])
def test_c1_controls_fail_closed_and_keep_independent_file_finding(stage, control):
    client, service, record, token = _harness()
    hidden = ("label,value\nSynthetic," + control + "=2+3").encode("utf-8")
    response = _submit(client, record, token, stage=stage, files=(VALID_FORMULA, hidden))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recommended_action"] == body["effective_action"] == "block"
    assert body["evaluation_status"] == "incomplete"
    assert len(body["attachments"]) == 2
    assert body["attachments"][0]["evaluation_status"] == "complete"
    assert body["attachments"][1]["evaluation_status"] == "incomplete"
    assert body["attachments"][1]["reason"] == "unsafe_csv_control_characters"
    assert body["attachments"][1]["status"] == "invalid"
    assert "csv_formula.potential_formula" in {item["rule_id"] for item in body["findings"]}
    assert any(item["checker_id"] == "attachment_probe" and item["status"] == "error"
               for item in body["checker_results"])
    assert control not in response.text
    assert "Synthetic" not in response.text
    assert "=2+3" not in response.text
    assert service.store.get(record.tenant_id).used(usage_month()) == 1


@pytest.mark.parametrize("stage", ["input", "output"])
def test_off_preference_never_relabels_c1_invalid_bytes_clean(stage):
    client, service, record, token = _harness(attachment_mode="off")
    hidden = "label,value\nSynthetic,\u009b[31m=2+3".encode("utf-8")
    response = _submit(client, record, token, stage=stage, files=(hidden,))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recommended_action"] == body["effective_action"] == "block"
    assert body["evaluation_status"] == "incomplete"
    assert body["attachments"][0]["reason"] == "unsafe_csv_control_characters"
    assert not any(item["rule_id"] == "csv_formula.potential_formula" for item in body["findings"])
    assert "\u009b" not in response.text
    assert service.store.get(record.tenant_id).used(usage_month()) == 1
