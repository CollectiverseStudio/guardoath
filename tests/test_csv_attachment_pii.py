"""Authenticated tenant PII checks on recognized real CSV bytes.

The deterministic synthetic recognizer tests orchestration and non-leaking receipts
on every CI matrix; the optional local-Pii test below also runs the real offline
recognizer wherever its dependency extra is installed. No provider requests.
"""
import base64

from fastapi.testclient import TestClient

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.models import CheckerResult, Finding
from guardoath.tenant_record import TenantRecord, demo_key, key_hash, usage_month
from guardoath.tenant_store import MemoryTenantStore


SECRET = "synthetic-file-pii-tenant-test-secret-0123456789"
EMAIL = "first.last@example.com"
CSV_EMAIL = ("record,email\nSynthetic," + EMAIL).encode("utf-8")
CSV_BENIGN = b"record,amount\nSynthetic,25"
CSV_FORMULA = ("record,email\n=2+3," + EMAIL).encode("utf-8")
PII_RULE = "pii.email_address"


async def _synthetic_pii(request, policy, stage):
    """Safe stand-in: exercises actual Engine, stage and policy without optional dependencies."""
    matches = EMAIL in request.content
    return CheckerResult(
        checker_id="pii", checker_version="synthetic-test-1",
        status="flagged" if matches else "passed",
        findings=(Finding(rule_id=PII_RULE, category="pii", severity="high",
                          explanation="Synthetic private identifier candidate."),) if matches else (),
    )


def _harness(*records, local_pii=None):
    service = TenantService(
        MemoryTenantStore(tuple(record for record, _ in records)),
        ({**default_checkers(), "pii": _synthetic_pii} if local_pii is None else local_pii),
        SECRET,
    )
    client = TestClient(create_app(engine=demo_engine(), credentials={}, tenants=service))
    return client, service


def _tenant(name="pii-csv-a", **settings):
    token = demo_key(name, SECRET)
    return (TenantRecord(tenant_id=name, display_name="Synthetic CSV PII", demo=True,
                         monthly_allowance=30, overage="block", key_hashes=(key_hash(token),),
                         **settings), token)


def _send(client, record, token, raw=CSV_EMAIL, *, stage="input", content="Summarize the report."):
    return client.post(
        f"/v1/checks/{stage}/attachments",
        headers={"Authorization": "Bearer " + token},
        json={"application_id": record.tenant_id, "content": content,
              "attachments_b64": [base64.b64encode(raw).decode("ascii")]},
    )


def test_tenant_input_output_pii_actions_apply_to_recognized_file_and_group():
    row, token = _tenant(input_check_pii="flag", output_check_pii="block")
    client, service = _harness((row, token))
    for stage, expected in (("input", "flag"), ("output", "block")):
        response = _send(client, row, token, stage=stage)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["effective_action"] == data["recommended_action"] == expected
        assert data["evaluation_status"] == "complete"
        assert PII_RULE in {f["rule_id"] for f in data["findings"]}
        assert any(r["checker_id"] == "pii" and r["status"] == "flagged"
                   for r in data["checker_results"])
        assert any(g["group_id"] == "sensitive_data" and g["status"] == "findings"
                   for g in data["groups"])
        assert data["attachments"][0]["evaluation_status"] == "complete"
        assert EMAIL not in response.text
    assert service.store.get(row.tenant_id).used(usage_month()) == 2


def test_benign_csv_is_clean_and_tenant_disabled_check_stays_off():
    on, on_key = _tenant(input_check_pii="block")
    off, off_key = _tenant("pii-csv-b")
    client, service = _harness((on, on_key), (off, off_key))
    clean = _send(client, on, on_key, CSV_BENIGN)
    assert clean.status_code == 200, clean.text
    assert clean.json()["effective_action"] == "allow"
    assert PII_RULE not in {f["rule_id"] for f in clean.json()["findings"]}
    disabled = _send(client, off, off_key)
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["effective_action"] == "allow"
    assert "pii" not in {r["checker_id"] for r in disabled.json()["checker_results"]}
    denied = _send(client, off, on_key)
    assert denied.status_code == 403
    assert service.store.get(off.tenant_id).used(usage_month()) == 1


def test_per_rule_off_and_attachment_off_preserve_independent_tenant_stage_policy():
    row, token = _tenant(
        input_check_pii="block", output_check_pii="flag",
        input_rule_actions={PII_RULE: "off"},
        output_attachment_csv_mode="off",
    )
    client, _ = _harness((row, token))
    incoming = _send(client, row, token).json()
    assert incoming["effective_action"] == "allow"
    assert PII_RULE not in {f["rule_id"] for f in incoming["findings"]}
    outgoing = _send(client, row, token, stage="output").json()
    assert outgoing["effective_action"] == "allow"
    assert outgoing["attachments"][0]["evaluation_status"] == "not_inspected_policy_off"
    assert PII_RULE not in {f["rule_id"] for f in outgoing["findings"]}


def test_file_redact_action_never_claims_sanitized_bytes_and_blocks_even_observe():
    for mode in ("enforce", "observe"):
        row, token = _tenant(mode=mode, input_check_pii="redact",
                             failure_action="flag", output_check_pii="flag")
        client, service = _harness((row, token))
        response = _send(client, row, token)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["recommended_action"] == data["effective_action"] == "block"
        assert data["evaluation_status"] == "incomplete"
        assert data["attachments"][0]["evaluation_status"] == "incomplete"
        assert data["attachments"][0]["reason"] == "attachment_redaction_unavailable"
        assert PII_RULE in {f["rule_id"] for f in data["findings"]}
        assert any(g["group_id"] == "sensitive_data" and g["status"] == "incomplete"
                   for g in data["groups"])
        assert data["redaction"]["content"] is None
        assert EMAIL not in response.text
        assert service.store.get(row.tenant_id).used(usage_month()) == 1
        # Only the file is unredactable: ordinary clean chat still has the
        # original tenant configuration; the code did not persist a Block action.
        assert service.store.get(row.tenant_id).input_check_pii.value == "redact"
        benign = _send(client, row, token, raw=CSV_BENIGN).json()
        assert benign["evaluation_status"] == "complete"
        assert benign["effective_action"] == "allow"
        assert benign["attachments"][0]["evaluation_status"] == "complete"


def test_missing_local_pii_checker_fails_closed_even_when_failure_action_flags():
    row, token = _tenant(mode="observe", input_check_pii="flag", failure_action="flag")
    service = TenantService(MemoryTenantStore((row,)), default_checkers(), SECRET)
    client = TestClient(create_app(engine=demo_engine(), credentials={}, tenants=service))
    response = _send(client, row, token)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["effective_action"] == data["recommended_action"] == "block"
    assert data["evaluation_status"] == "incomplete"
    assert any(r["checker_id"] == "pii" and r["status"] == "unsupported"
               for r in data["checker_results"])
    assert EMAIL not in response.text


def test_csv_formula_and_pii_return_both_findings_strongest_action_one_meter():
    row, token = _tenant(input_check_pii="block", input_check_csv_formula="flag")
    client, service = _harness((row, token))
    response = _send(client, row, token, CSV_FORMULA)
    assert response.status_code == 200, response.text
    result = response.json()
    assert {"csv_formula.potential_formula", PII_RULE} <= {f["rule_id"] for f in result["findings"]}
    assert result["recommended_action"] == result["effective_action"] == "block"
    assert any(g["group_id"] == "structure" and g["status"] == "findings" for g in result["groups"])
    assert any(g["group_id"] == "sensitive_data" and g["status"] == "findings" for g in result["groups"])
    assert service.store.get(row.tenant_id).used(usage_month()) == 1
    assert EMAIL not in response.text and "=2+3" not in response.text


def test_real_offline_pii_recognizer_detects_synthetic_email_when_extra_installed():
    import pytest
    pytest.importorskip("regex")
    pytest.importorskip("tldextract")
    pytest.importorskip("phonenumbers")
    from guardoath.pii import build_pii_checker

    checkers = default_checkers()
    checkers["pii"] = build_pii_checker()
    row, token = _tenant(input_check_pii="block")
    client, _ = _harness((row, token), local_pii=checkers)
    response = _send(client, row, token)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["evaluation_status"] == "complete"
    assert PII_RULE in {f["rule_id"] for f in data["findings"]}
    assert data["effective_action"] == "block"
    assert EMAIL not in response.text
