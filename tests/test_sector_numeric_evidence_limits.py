"""Fail-closed numeric boundaries for isolated industry-sector action controls."""
from decimal import Decimal

import pytest

from guardoath.sector_invariants import MAX_NUMERIC_CHARS, validate_sector_action
from guardoath.sector_pack_decisions import evaluate_sector_pack


class CoercionTrap:
    def __str__(self):
        raise RuntimeError("untrusted conversion must not execute")


@pytest.mark.parametrize("value", [
    "9" * (MAX_NUMERIC_CHARS + 1),
    "0." + "0" * (MAX_NUMERIC_CHARS + 1),
    10 ** 300,
    CoercionTrap(),
    [], {}, object(), True, "NaN", "Infinity", "-Infinity",
])
@pytest.mark.parametrize("side", ["proposed", "trusted"])
def test_oversized_and_non_numeric_evidence_blocks_without_exception(value, side):
    action = {"operation": "checkout", "charged_total": "100"}
    evidence = {"server_total": "100"}
    if side == "proposed":
        action["charged_total"] = value
    else:
        evidence["server_total"] = value
    findings = validate_sector_action("retail", action, evidence)
    assert tuple(f.rule_id for f in findings) == ("sector.invalid_numeric_value",)


@pytest.mark.parametrize("numeric", [
    "0", "100.00", 100, 100.0, Decimal("100.00"), "1e2",
])
def test_supported_bounded_numeric_inputs_remain_valid(numeric):
    findings = validate_sector_action(
        "retail", {"operation": "checkout", "charged_total": numeric},
        {"server_total": "100" if numeric != "0" else "0"},
    )
    assert findings == ()


def test_sector_pack_returns_denial_for_oversized_numeric_evidence():
    result = evaluate_sector_pack(
        "finance",
        {"operation": "claim_payout", "payout": "7" * (MAX_NUMERIC_CHARS + 1)},
        {"approved_payout_limit": "100"},
        enabled_sectors=frozenset({"finance"}),
        identity_verified=True,
        tenant_authorized=True,
    )
    assert result.status == "block"
    assert result.reason_codes == ("sector_invariant_failed",)
    assert result.finding_ids == ("sector.invalid_numeric_value",)
