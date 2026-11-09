"""FC-031–049 dashboard examples must be admitted by the real JSON/API contract.

A rule may be saved with deep JSON Pointers because it still evaluates submitted
documents fail-closed. Such a rule must NOT advertise a synthetic sample which
the shared API would reject for depth or content length. All values are synthetic.
"""
import json

from guardoath.business import exact_number
import pytest
from fastapi.testclient import TestClient

from guardoath.advanced_arithmetic import _valid as arithmetic_valid
from guardoath.advanced_identity import _valid as identity_valid
from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.json_data import parse_json
from guardoath.models import AdvancedArithmeticRule, AdvancedIdentityRule
from guardoath.record_rules import (
    ARITHMETIC_EXAMPLES, IDENTITY_EXAMPLES, _field_document, record_rules,
)
from guardoath.tenant_record import TenantRecord, demo_key, key_hash
from guardoath.tenant_store import MemoryTenantStore


CASES = (
    [("advanced_arithmetic", kind) for kind in ARITHMETIC_EXAMPLES]
    + [("advanced_identity", kind) for kind in IDENTITY_EXAMPLES]
)


def _rule(family, kind, *, deep=False):
    roles = (ARITHMETIC_EXAMPLES[kind][0] if family == "advanced_arithmetic"
             else IDENTITY_EXAMPLES[kind][0])
    prefix = "/record" + ("/x" * 33 if deep else "")
    fields = {role: prefix + "/" + role for role in roles}
    cls = AdvancedArithmeticRule if family == "advanced_arithmetic" else AdvancedIdentityRule
    return cls(rule_id="sample_contract", kind=kind, fields=fields)


def _examples(family, kind):
    if family == "advanced_arithmetic":
        clean, variable, unsafe_value = ARITHMETIC_EXAMPLES[kind]
        return clean, {**clean, variable: unsafe_value}
    clean, violating = IDENTITY_EXAMPLES[kind]
    return clean, violating


@pytest.mark.parametrize("family,kind", CASES)
def test_all_nonreference_examples_are_rule_certified_at_normal_json_depth(family, kind):
    rule = _rule(family, kind)
    record = TenantRecord(tenant_id="fc-sample-normal", display_name="Synthetic FC example",
                          input_check_cross_field="flag", output_check_cross_field="block",
                          **{family: (rule,)})
    metadata = record_rules(record)["business.sample_contract"]
    validator = arithmetic_valid if family == "advanced_arithmetic" else identity_valid
    assert metadata["example_unavailable"] is None
    assert metadata["look_alike_unavailable"] is None
    for name, expected in (("example", False), ("look_alike", True)):
        sample = metadata[name]
        assert isinstance(sample, str) and len(sample) <= 32_000
        parse_json(sample)  # strict shared admission precedes exact Decimal decode
        assert bool(validator(rule, json.loads(sample, parse_float=exact_number))) is expected


@pytest.mark.parametrize("family,kind", CASES)
def test_valid_deep_pointer_never_advertises_unadmittable_json_sample(family, kind):
    rule = _rule(family, kind, deep=True)
    record = TenantRecord(tenant_id="fc-sample-deep", display_name="Synthetic FC example",
                          input_check_cross_field="flag", output_check_cross_field="block",
                          **{family: (rule,)})
    metadata = record_rules(record)["business.sample_contract"]
    clean, unsafe = _examples(family, kind)
    # An unrestricted JSON decoder accepts the old advertised samples, but the
    # single production JSON admission contract rejects their excessive depth.
    for values in (clean, unsafe):
        unrestricted = _field_document(rule.fields, values)
        assert json.loads(unrestricted)
        with pytest.raises(ValueError):
            parse_json(unrestricted)
    for name in ("example", "look_alike"):
        assert metadata[name] is None
        assert metadata[name + "_unavailable"]["code"] == "sample_unavailable"
        assert "record" not in metadata[name + "_unavailable"]["reason"]


@pytest.mark.parametrize("family,kind", [
    ("advanced_arithmetic", "invoice_total"),
    ("advanced_identity", "order_id_match"),
])
def test_authenticated_tenant_persists_deep_rule_and_hides_bad_examples_in_api(family, kind):
    tenant_id, secret = "sample-depth-tenant", "sample-depth-tenant-secret-0123456789"
    token = demo_key(tenant_id, secret)
    baseline = TenantRecord(tenant_id=tenant_id, display_name="Synthetic sample admission",
                            demo=True, monthly_allowance=30, overage="block",
                            key_hashes=(key_hash(token),),
                            input_check_text_security="flag", output_check_text_security="flag")
    store = MemoryTenantStore((baseline,))
    client = TestClient(create_app(engine=demo_engine(), credentials={},
                                   tenants=TenantService(store, default_checkers(), secret)))
    headers = {"Authorization": "Bearer " + token}
    rule = _rule(family, kind, deep=True)
    response = client.post("/v1/tenants/current/checks", headers=headers, json={
        "revision": baseline.revision,
        "changes": {family: [rule.model_dump(mode="json")],
                    "input_check_cross_field": "flag",
                    "output_check_cross_field": "block"},
    })
    assert response.status_code == 200, response.text[:200]
    saved = store.get(tenant_id)
    assert saved.revision == baseline.revision + 1
    assert getattr(saved, family) == (rule,)
    listing = client.get("/v1/applications/current", headers=headers)
    assert listing.status_code == 200
    rows = {r["rule_id"]: r for r in listing.json()["tenant"]["record_rules"]}
    row = rows["business.sample_contract"]
    assert row["example"] is None and row["look_alike"] is None
    assert row["example_unavailable"]["code"] == "sample_unavailable"
    assert row["look_alike_unavailable"]["code"] == "sample_unavailable"
    for stage in ("input", "output"):
        raw_sample = _field_document(rule.fields, _examples(family, kind)[0])
        checked = client.post("/v1/checks/" + stage, headers=headers, json={
            "application_id": tenant_id, "content_format": "json",
            "content": raw_sample,
        })
        # CheckRequest rejects over-deep JSON before launching the Engine.
        # HTTP 422 is the correct fail-closed API admission, not a fabricated
        # status=200 incomplete evaluation or a bypass of the size/depth limit.
        assert checked.status_code == 422
        assert checked.json() == {"detail": "Invalid check request"}
        assert raw_sample not in checked.text
    assert store.get(tenant_id) == saved
