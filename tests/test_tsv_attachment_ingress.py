"""Real UTF-8 TSV attachments use the existing tenant CSV-formula engine.

No filename/MIME trust, no external parser or paid service. These tests exercise
the authenticated byte endpoint on both stages with real Engine and metering.
"""
import base64

import pytest
from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.attachment_probe import classify_attachment
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.tenant_record import TenantRecord, demo_key, key_hash, usage_month
from guardoath.tenant_store import MemoryTenantStore

SECRET = "synthetic-tsv-attachment-secret-0123456789012345"
TSV_FORMULA = b"name\tvalue\nSynthetic\t=2+3"
TSV_CLEAN = b"name\tvalue\nSynthetic\t25"
TSV_APOSTROPHE = b"name\tvalue\nSynthetic\t'=2+3"
CSV_FORMULA = b"name,value\nSynthetic,=3+4"
INVALID_TSV = b"name\tvalue\nSynthetic\t1\textra"
PDF = b"%PDF-1.7\nnot parsed"


def harness(*, input_action="flag", output_action="block", **settings):
    token = demo_key("tsv-tenant", SECRET)
    record = TenantRecord(
        tenant_id="tsv-tenant", display_name="Synthetic TSV",
        demo=True, overage="block", monthly_allowance=100,
        key_hashes=(key_hash(token),),
        input_check_text_security="flag", output_check_text_security="flag",
        input_check_csv_formula=input_action, output_check_csv_formula=output_action,
        **settings,
    )
    service = TenantService(MemoryTenantStore((record,)), default_checkers(), SECRET)
    app = create_app(engine=demo_engine(), credentials={}, tenants=service)
    return TestClient(app), service, token


def submit(client, token, files, *, stage="input", owner="tsv-tenant"):
    return client.post(
        "/v1/checks/" + stage + "/attachments",
        headers={"Authorization": "Bearer " + token},
        json={"application_id": owner, "content": "Please inspect uploaded tabular data",
              "attachments_b64": [base64.b64encode(item).decode("ascii") for item in files]},
    )


@pytest.mark.parametrize("payload", [
    TSV_FORMULA, TSV_CLEAN, TSV_APOSTROPHE,
    b"\xef\xbb\xbfname\tvalue\r\nSynthetic\t=2+3",
    "name\tvalue\n東京\t正常".encode("utf-8"),
])
def test_real_tsv_bytes_are_recognized_without_filename(payload):
    receipt = classify_attachment(payload)
    assert receipt.kind == "tsv"
    assert receipt.status == "recognized"
    assert receipt.reason == "tsv_structure_recognized_only"
    assert receipt.csv_text_supported
    assert "Synthetic" not in str(receipt)


@pytest.mark.parametrize("stage,action", [("input", "flag"), ("output", "block")])
def test_authenticated_tsv_formula_respects_tenant_stage_action_and_usage(stage, action):
    client, service, token = harness()
    with client:
        response = submit(client, token, (TSV_FORMULA,), stage=stage)
    assert response.status_code == 200
    result = response.json()
    assert result["evaluation_status"] == "complete"
    assert result["effective_action"] == action
    assert result["attachments"][0]["kind"] == "tsv"
    assert result["attachments"][0]["evaluation_status"] == "complete"
    assert result["attachments"][0]["reason"] == "tsv_structure_recognized_only"
    assert "csv_formula.potential_formula" in {f["rule_id"] for f in result["findings"]}
    assert TSV_FORMULA.decode() not in response.text
    assert "=2+3" not in response.text
    assert service.store.get("tsv-tenant").used(usage_month()) == 1


@pytest.mark.parametrize("stage", ["input", "output"])
@pytest.mark.parametrize("payload", [TSV_CLEAN, TSV_APOSTROPHE])
def test_benign_tsv_cells_complete_without_formula_findings(stage, payload):
    client, _, token = harness()
    with client:
        response = submit(client, token, (payload,), stage=stage)
    assert response.status_code == 200
    result = response.json()
    assert result["evaluation_status"] == "complete"
    assert result["effective_action"] == "allow"
    assert result["attachments"][0]["kind"] == "tsv"
    assert "csv_formula.potential_formula" not in {f["rule_id"] for f in result["findings"]}


def test_mixed_tsv_and_csv_findings_are_preserved_with_one_metered_call():
    client, service, token = harness()
    with client:
        response = submit(client, token, (TSV_FORMULA, CSV_FORMULA, TSV_CLEAN))
    assert response.status_code == 200
    data = response.json()
    assert [r["kind"] for r in data["attachments"]] == ["tsv", "csv", "tsv"]
    assert [f["rule_id"] for f in data["findings"]].count("csv_formula.potential_formula") == 2
    assert data["effective_action"] == "flag"
    assert service.store.get("tsv-tenant").used(usage_month()) == 1


@pytest.mark.parametrize("stage", ["input", "output"])
def test_explicit_attachment_off_skips_recognized_tsv_without_fake_pass(stage):
    client, _, token = harness(input_attachment_csv_mode="off",
                                output_attachment_csv_mode="off")
    with client:
        response = submit(client, token, (TSV_FORMULA,), stage=stage)
    assert response.status_code == 200
    data = response.json()
    assert data["attachments"][0]["kind"] == "tsv"
    assert data["attachments"][0]["evaluation_status"] == "not_inspected_policy_off"
    assert data["effective_action"] == "allow"
    assert not data["findings"]


@pytest.mark.parametrize("stage", ["input", "output"])
@pytest.mark.parametrize("bad,reason", [
    (INVALID_TSV, "inconsistent_csv_columns"),
    (PDF, "pdf_parser_unavailable"),
    (b"name\tvalue\nSynthetic\t\x1b=2+3", "unsafe_csv_control_characters"),
    ("name\tvalue\nSynthetic\t\u200b=2+3".encode("utf-8"), "unsafe_csv_invisible_formula_prefix"),
])
def test_uninspectable_tsv_and_other_bytes_hard_block_even_observe_off(stage, bad, reason):
    client, service, token = harness(
        mode="observe", failure_action="flag",
        input_attachment_csv_mode="off", output_attachment_csv_mode="off")
    with client:
        response = submit(client, token, (bad,), stage=stage)
    assert response.status_code == 200
    data = response.json()
    assert data["evaluation_status"] == "incomplete"
    assert data["recommended_action"] == data["effective_action"] == "block"
    assert data["attachments"][0]["reason"] == reason
    assert data["attachments"][0]["evaluation_status"] == "incomplete"
    assert service.store.get("tsv-tenant").used(usage_month()) == 1


def test_cross_tenant_key_cannot_use_other_owner_and_does_not_meter():
    client, service, token = harness()
    with client:
        response = submit(client, token, (TSV_FORMULA,), owner="other-tenant")
    assert response.status_code == 403
    assert service.store.get("tsv-tenant").used(usage_month()) == 0
