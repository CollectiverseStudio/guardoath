"""All-sector negative contract: no proposed action may pass without host evidence.

These are isolated validator tests, not API or live dispatch tests.
"""
import pytest
from guardoath.sector_invariants import validate_sector_action
from guardoath.sector_pack_decisions import SECTORS, evaluate_sector_pack

OPERATIONS = {
    "agriculture": "record_ndvi", "mining": "set_pressure",
    "utilities": "operate_breaker", "construction": "approve_curing",
    "manufacturing": "set_machine_speed", "wholesale": "verify_receipt",
    "retail": "checkout", "transportation": "approve_route",
    "information": "publish_rag_document", "finance": "claim_payout",
    "real_estate": "issue_lease", "professional": "cite_legal_precedent",
    "management": "transfer_budget", "administrative": "send_it_notice",
    "education": "publish_grade", "healthcare": "issue_prescription",
    "arts": "reserve_tickets", "hospitality": "confirm_booking",
    "automotive": "certify_brakes", "public_administration": "release_record",
}

def test_all_twenty_sectors_have_an_operation():
    assert set(OPERATIONS) == SECTORS

@pytest.mark.parametrize("sector", sorted(OPERATIONS))
def test_missing_trusted_evidence_cannot_authorize(sector):
    action = {"operation": OPERATIONS[sector]}
    findings = validate_sector_action(sector, action, {})
    assert findings, sector
    result = evaluate_sector_pack(
        sector, action, {}, enabled_sectors=frozenset({sector}),
        identity_verified=True, tenant_authorized=True,
    )
    assert result.status == "block"
    assert result.finding_ids, sector

@pytest.mark.parametrize("sector", sorted(OPERATIONS))
def test_wrong_operation_cannot_authorize(sector):
    action = {"operation": "unrelated_operation"}
    findings = validate_sector_action(sector, action, {})
    assert len(findings) == 1
    assert findings[0].rule_id == "sector.operation_mismatch"

@pytest.mark.parametrize("sector", sorted(OPERATIONS))
def test_disabled_pack_cannot_authorize(sector):
    result = evaluate_sector_pack(
        sector, {"operation": OPERATIONS[sector]}, {},
        enabled_sectors=frozenset(), identity_verified=True,
        tenant_authorized=True,
    )
    assert result.status == "block"
    assert result.reason_codes == ("sector_not_enabled",)

@pytest.mark.parametrize("sector", sorted(OPERATIONS))
def test_unverified_identity_cannot_authorize(sector):
    result = evaluate_sector_pack(
        sector, {"operation": OPERATIONS[sector]}, {},
        enabled_sectors=frozenset({sector}), identity_verified=False,
        tenant_authorized=True,
    )
    assert result.status == "block"
    assert "identity_unverified" in result.reason_codes
