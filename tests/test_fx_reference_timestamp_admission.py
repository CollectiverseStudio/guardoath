"""FX reference timestamps must be valid before tenant settings are saved."""
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.models import AdvancedConversionTimeRule, Policy
from guardoath.tenant_record import TenantRecord, demo_key, key_hash, usage_month
from guardoath.tenant_store import MemoryTenantStore


INVALID = ("+00:60", "+01:99", "-00:60")
VALID = ("Z", "+01:59", "-00:59")


def fx(offset):
    return dict(rule_id="fx_guard", kind="currency_conversion_cap", fields={
        "payment_amount": "/payment", "payment_currency": "/payment_ccy",
        "invoice_amount": "/invoice", "invoice_currency": "/invoice_ccy"},
        source_currency="EUR", destination_currency="USD",
        trusted_rate_numerator=6, trusted_rate_denominator=5,
        rate_reference="synthetic-fx-reference", rate_asof="2026-11-01T09:00:00" + offset)


@pytest.mark.parametrize("offset", INVALID)
@pytest.mark.parametrize("owner", ["rule", "policy", "tenant"])
def test_malformed_fx_reference_offset_is_rejected_by_typed_owners(offset, owner):
    data = fx(offset)
    with pytest.raises(ValidationError):
        if owner == "rule":
            AdvancedConversionTimeRule.model_validate(data)
        elif owner == "policy":
            Policy.model_validate(dict(application_id="fx-a", version="v1", rules=[], advanced_conversion_time=[data]))
        else:
            TenantRecord.model_validate(dict(tenant_id="fx-a", display_name="FX", input_check_cross_field="block", advanced_conversion_time=[data]))


@pytest.mark.parametrize("offset", VALID)
def test_valid_fx_reference_offsets_remain_configurable(offset):
    rule = AdvancedConversionTimeRule.model_validate(fx(offset))
    assert rule.rate_asof == fx(offset)["rate_asof"]
    policy = Policy(application_id="fx-a", version="v1", rules=(), advanced_conversion_time=(rule,))
    tenant = TenantRecord(tenant_id="fx-a", display_name="FX", input_check_cross_field="block", advanced_conversion_time=(rule,))
    assert policy.advanced_conversion_time == tenant.advanced_conversion_time == (rule,)


@pytest.mark.parametrize("offset,valid", [(x, False) for x in INVALID] + [(x, True) for x in VALID])
def test_revisioned_api_rejects_invalid_reference_without_mutating_other_tenants(offset, valid):
    secret = "synthetic-fx-admission-demo-secret-0123456789"
    token = demo_key("fx-a", secret)
    original = TenantRecord(tenant_id="fx-a", display_name="FX A", demo=True,
                            key_hashes=(key_hash(token),), monthly_allowance=100, overage="block", input_check_text_security="flag")
    other = TenantRecord(tenant_id="fx-b", display_name="FX B", demo=True, monthly_allowance=100, overage="block", input_check_text_security="flag")
    store = MemoryTenantStore((original, other))
    headers = {"Authorization": "Bearer " + token}
    with TestClient(create_app(engine=demo_engine(), credentials={},
                              tenants=TenantService(store, default_checkers(), secret))) as client:
        original = store.get("fx-a")
        other = store.get("fx-b")
        response = client.post("/v1/tenants/current/checks", headers=headers, json={
            "revision": original.revision, "changes": {
                "advanced_conversion_time": [fx(offset)],
                "input_check_cross_field": "block", "output_check_cross_field": "flag"}})
        if not valid:
            assert response.status_code == 422
            assert store.get("fx-a").model_dump() == original.model_dump()
            assert fx(offset)["rate_asof"] not in response.text
            assert "synthetic-fx-reference" not in response.text
        else:
            assert response.status_code == 200, response.text
            assert store.get("fx-a").advanced_conversion_time[0].rate_asof == fx(offset)["rate_asof"]
            assert store.get("fx-a").revision == original.revision + 1
        assert store.get("fx-b") == other
        assert store.get("fx-a").used(usage_month()) == 0
        assert store.get("fx-b").used(usage_month()) == 0
