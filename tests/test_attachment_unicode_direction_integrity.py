"""Security regression: direction markers must not conceal CSV formula prefixes.

The server-received bytes go through the existing attachment probe before
tenant-enabled checks. Deliberately ambiguous direction formatting is never
attested inspected, even with explicit tenant attachment Off or observe/Flag.
Synthetic only: no personal or production data.
"""
import base64
import hashlib

import pytest
from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.attachment_probe import classify_attachment
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.tenant_record import TenantRecord, demo_key, key_hash, usage_month
from guardoath.tenant_store import MemoryTenantStore


MARKS = ("\u061c", "\u200e", "\u200f")  # ALM, LRM, RLM
SECRET = "csv-direction-probe-integration-secret-0123456789"


@pytest.mark.parametrize("mark", MARKS)
@pytest.mark.parametrize("prefix", ("=", "+", "-", "@"))
def test_direction_mark_before_formula_never_claims_inspected_csv(mark, prefix):
    payload = ("name,value\nSynthetic," + mark + prefix + "2+3").encode("utf-8")
    receipt = classify_attachment(payload)
    assert receipt.kind == "unknown"
    assert receipt.status == "invalid"
    assert receipt.reason == "unsafe_csv_control_characters"
    assert not receipt.csv_text_supported
    assert receipt.byte_count == len(payload)
    assert receipt.sha256 == hashlib.sha256(payload).hexdigest()
    assert "Synthetic" not in repr(receipt)
    assert prefix + "2+3" not in repr(receipt)


@pytest.mark.parametrize("stage", ("input", "output"))
@pytest.mark.parametrize("attachment_mode", ("auto", "off"))
@pytest.mark.parametrize("mark", MARKS)
def test_direction_marks_fail_closed_on_authenticated_bytes_even_when_off_and_observe(
    stage, attachment_mode, mark,
):
    tenant_id = "direction-integrity-demo"
    key = demo_key(tenant_id, SECRET)
    record = TenantRecord(
        tenant_id=tenant_id,
        display_name="Direction integrity",
        demo=True,
        monthly_allowance=40,
        overage="block",
        key_hashes=(key_hash(key),),
        mode="observe",
        failure_action="flag",
        input_check_text_security="allow",  # Valid tenant: at least one enabled check.
        output_check_text_security="allow",
        input_attachment_csv_mode=attachment_mode,
        output_attachment_csv_mode=attachment_mode,
    )
    service = TenantService(MemoryTenantStore((record,)), default_checkers(), SECRET)
    client = TestClient(create_app(engine=demo_engine(), credentials={}, tenants=service))
    payload = ("name,value\nSynthetic," + mark + "=2+3").encode("utf-8")
    response = client.post(
        "/v1/checks/" + stage + "/attachments",
        headers={"Authorization": "Bearer " + key},
        json={
            "application_id": tenant_id,
            "content": "Please summarize this synthetic CSV.",
            "attachments_b64": [base64.b64encode(payload).decode("ascii")],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "observe"
    assert body["evaluation_status"] == "incomplete"
    assert body["recommended_action"] == "block"
    assert body["effective_action"] == "block"
    assert body["attachments"][0]["status"] == "invalid"
    assert body["attachments"][0]["reason"] == "unsafe_csv_control_characters"
    assert body["attachments"][0]["evaluation_status"] == "incomplete"
    assert "Synthetic" not in response.text
    assert "=2+3" not in response.text
    assert service.store.get(tenant_id).used(usage_month()) == 1


@pytest.mark.parametrize("value", (
    "مرحبا", "فارسی", "東京", "A normal note", "👩\u200d🔬", "'=2+3"
))
def test_legitimate_unicode_and_explicit_apostrophe_escape_remain_csv(value):
    receipt = classify_attachment(("name,value\nSynthetic," + value).encode("utf-8"))
    assert receipt.kind == "csv"
    assert receipt.status == "recognized"
    assert receipt.csv_text_supported
