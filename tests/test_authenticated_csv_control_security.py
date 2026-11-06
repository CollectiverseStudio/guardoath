"""CSV control-byte rejection is enforced at the real tenant attachment API boundary.

Synthetic payloads only. A valid CSV formula in a separate file must retain its
finding even when an unsafe file forces the entire request to Block.
"""
import base64

import pytest
from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.tenant_record import TenantRecord, demo_key, key_hash, usage_month
from guardoath.tenant_store import MemoryTenantStore


SECRET = "synthetic-csv-control-byte-ingress-0123456789"
SAFE_FORMULA = b"label,value\nSynthetic,=2+3"
ESCAPE_CSV = b"label,value\nSynthetic,\x1b[31m=2+3"
BIDI_CSV = "label,value\nSynthetic,\u202e=2+3".encode("utf-8")


def _harness(tenant_id, *, mode="enforce", attachment_mode="auto"):
    token = demo_key(tenant_id, SECRET)
    record = TenantRecord(
        tenant_id=tenant_id, display_name="CSV controls", demo=True,
        mode=mode, monthly_allowance=15, overage="block",
        key_hashes=(key_hash(token),),
        input_check_csv_formula="flag", output_check_csv_formula="flag",
        input_attachment_csv_mode=attachment_mode,
        output_attachment_csv_mode=attachment_mode,
    )
    tenants = TenantService(MemoryTenantStore((record,)), default_checkers(), SECRET)
    client = TestClient(create_app(engine=demo_engine(), credentials={}, tenants=tenants))
    return client, tenants, record, token


def _send(client, record, token, raw_files, stage):
    return client.post(
        f"/v1/checks/{stage}/attachments",
        headers={"Authorization": "Bearer " + token},
        json={
            "application_id": record.tenant_id,
            "content": "Review synthetic CSV data.",
            "attachments_b64": [base64.b64encode(raw).decode("ascii") for raw in raw_files],
        },
    )


@pytest.mark.parametrize("unsafe", [ESCAPE_CSV, BIDI_CSV])
@pytest.mark.parametrize("stage", ["input", "output"])
def test_unsafe_csv_controls_block_and_keep_other_file_findings_and_one_meter(stage, unsafe):
    client, service, record, token = _harness("csv-control-a", mode="observe")
    response = _send(client, record, token, (SAFE_FORMULA, unsafe), stage)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["recommended_action"] == result["effective_action"] == "block"
    assert result["evaluation_status"] == "incomplete"
    assert len(result["attachments"]) == 2
    assert result["attachments"][0]["evaluation_status"] == "complete"
    assert result["attachments"][1]["evaluation_status"] == "incomplete"
    assert result["attachments"][1]["reason"] == "unsafe_csv_control_characters"
    assert result["attachments"][1]["status"] == "invalid"
    assert "csv_formula.potential_formula" in {item["rule_id"] for item in result["findings"]}
    assert any(item["checker_id"] == "attachment_probe" and item["status"] == "error"
               for item in result["checker_results"])
    assert "Synthetic" not in response.text
    assert "=2+3" not in response.text
    assert service.store.get(record.tenant_id).used(usage_month()) == 1


@pytest.mark.parametrize("unsafe", [ESCAPE_CSV, BIDI_CSV])
def test_explicit_attachment_off_cannot_turn_invalid_bytes_into_clean_attestation(unsafe):
    client, service, record, token = _harness(
        "csv-control-off", mode="observe", attachment_mode="off"
    )
    response = _send(client, record, token, (unsafe,), "input")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["recommended_action"] == result["effective_action"] == "block"
    assert result["evaluation_status"] == "incomplete"
    assert result["attachments"][0]["reason"] == "unsafe_csv_control_characters"
    assert not any(item["rule_id"] == "csv_formula.potential_formula"
                   for item in result["findings"])
    assert service.store.get(record.tenant_id).used(usage_month()) == 1
