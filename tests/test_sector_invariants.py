"""Positive and benign tests for four opt-in sector invariants."""
import pytest
from guardoath.sector_invariants import validate_sector_action as check

@pytest.mark.parametrize("sector,action,trusted,expected", [
    ("retail", {"operation":"checkout","charged_total":"0.00"}, {"server_total":"125.00"}, "retail.total_mismatch"),
    ("retail", {"operation":"checkout","charged_total":"125.00"}, {"server_total":"125.00"}, None),
    ("finance", {"operation":"claim_payout","payout":"5000"}, {"approved_payout_limit":"1000"}, "finance.payout_limit"),
    ("finance", {"operation":"claim_payout","payout":"900"}, {"approved_payout_limit":"1000"}, None),
    ("manufacturing", {"operation":"set_machine_speed","speed":"5"}, {"certified_max_speed":"2"}, "manufacturing.speed_limit"),
    ("manufacturing", {"operation":"set_machine_speed","speed":"1"}, {"certified_max_speed":"2"}, None),
    ("healthcare", {"operation":"issue_prescription"}, {"licensed_prescriber_verified":False,"prescription_authorized":False}, "healthcare.prescriber_authorization"),
    ("healthcare", {"operation":"issue_prescription"}, {"licensed_prescriber_verified":True,"prescription_authorized":True}, None),
])
def test_sector_risk_and_lookalike(sector,action,trusted,expected):
    findings = check(sector,action,trusted)
    assert tuple(x.rule_id for x in findings) == ((expected,) if expected else ())

@pytest.mark.parametrize("value", [None, "NaN", "Infinity", "invalid", True, "-5"])
def test_invalid_financial_values(value):
    result = check("finance",{"operation":"claim_payout","payout":value},{"approved_payout_limit":"100"})
    assert result[0].rule_id == "sector.invalid_numeric_value"

def test_missing_authoritative_limit_does_not_pass():
    assert check("manufacturing",{"operation":"set_machine_speed","speed":"1"}, {})[0].rule_id == "sector.invalid_numeric_value"

def test_unapproved_operation_rejected():
    assert check("retail",{"operation":"delete_orders"}, {"server_total":"0"})[0].rule_id == "sector.operation_mismatch"

def test_unsupported_sector_rejected():
    with pytest.raises(ValueError):
        check("unknown",{"operation":"x"},{})

def test_input_types_checked():
    with pytest.raises(TypeError):
        check("retail", "not a mapping", {})
