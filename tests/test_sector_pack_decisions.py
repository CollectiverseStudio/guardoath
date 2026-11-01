"""Sector pack policy decision tests; trusted evidence supplied by host."""
import pytest
from guardoath.sector_pack_decisions import SECTORS, evaluate_sector_pack

def evaluate(sector="retail", action=None, evidence=None, **kwargs):
    return evaluate_sector_pack(
        sector,
        {"operation":"checkout","charged_total":"25"} if action is None else action,
        {"server_total":"25"} if evidence is None else evidence,
        enabled_sectors=kwargs.pop("enabled_sectors", frozenset({"retail"})),
        identity_verified=kwargs.pop("identity_verified", True),
        tenant_authorized=kwargs.pop("tenant_authorized", True),
        **kwargs,
    )

def test_all_twenty_sector_names():
    assert len(SECTORS) == 20

def test_allowed_action():
    assert evaluate().status == "allow"

@pytest.mark.parametrize("changes,reason", [
    ({"enabled_sectors":frozenset()}, "sector_not_enabled"),
    ({"identity_verified":False}, "identity_unverified"),
    ({"tenant_authorized":False}, "tenant_unauthorized"),
    ({"enabled_sectors":frozenset({"retail","unknown"})}, "invalid_entitlements"),
])
def test_denied_context(changes,reason):
    result = evaluate(**changes)
    assert result.status == "block" and reason in result.reason_codes

def test_all_identity_and_tenant_denials_returned():
    result = evaluate(identity_verified=False,tenant_authorized=False)
    assert result.reason_codes == ("identity_unverified","tenant_unauthorized")

def test_sector_invariant_denied():
    result = evaluate(action={"operation":"checkout","charged_total":"0"})
    assert result.status == "block"
    assert result.finding_ids == ("retail.total_mismatch",)

def test_unknown_sector_denied():
    assert evaluate(sector="unrecognized").reason_codes == ("unknown_sector",)

def test_invalid_action_denied():
    assert evaluate(action="not a mapping").status == "block"

def test_missing_evidence_denied():
    assert evaluate(evidence={}).status == "block"

def test_multiple_enabled_sectors_do_not_change_other_sector_policy():
    result = evaluate(enabled_sectors=frozenset({"retail","finance"}))
    assert result.status == "allow"

@pytest.mark.parametrize("field", ["identity_verified", "tenant_authorized"])
@pytest.mark.parametrize("invalid", ["false", "true", 0, 1, None, [], {}])
def test_untrusted_truthy_or_nonboolean_evidence_never_authorizes(field, invalid):
    result = evaluate(**{field: invalid})
    assert result.status == "block"
    assert result.reason_codes == ("invalid_identity_evidence",)

def test_invalid_entitlement_type_never_authorizes():
    result = evaluate(enabled_sectors={"retail"})
    assert result.status == "block"
    assert result.reason_codes == ("invalid_entitlements",)

@pytest.mark.parametrize("invalid", [None, [], {}, 42, True, ("retail",)])
def test_malformed_sector_ids_fail_closed(invalid):
    result = evaluate(sector=invalid)
    assert result.status == "block"
    assert result.reason_codes == ("unknown_sector",)

@pytest.mark.parametrize("invalid", [
    frozenset({None}), frozenset({42}), frozenset({True}),
    frozenset({"retail", None}), frozenset({"retail", "unrecognized"}),
])
def test_malformed_entitlement_elements_fail_closed(invalid):
    result = evaluate(enabled_sectors=invalid)
    assert result.status == "block"
    assert result.reason_codes == ("invalid_entitlements",)
