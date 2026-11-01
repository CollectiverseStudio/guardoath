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

@pytest.mark.parametrize("sector,action,trusted,expected", [
    ("wholesale", {"operation":"verify_receipt","verification_status":"verified"}, {"manifest_signature_valid":True,"physical_receipt_verified":True}, None),
    ("wholesale", {"operation":"verify_receipt","verification_status":"force-passed"}, {"manifest_signature_valid":True,"physical_receipt_verified":True}, "wholesale.invalid_receipt_state"),
    ("wholesale", {"operation":"verify_receipt","verification_status":"verified"}, {"manifest_signature_valid":False,"physical_receipt_verified":True}, "wholesale.receipt_unverified"),
    ("education", {"operation":"publish_grade","grade":"A"}, {"grader_authorized":True,"rubric_evidence_verified":True,"rubric_grade":"A"}, None),
    ("education", {"operation":"publish_grade","grade":"A+"}, {"grader_authorized":True,"rubric_evidence_verified":True,"rubric_grade":"B"}, "education.grade_mismatch"),
    ("education", {"operation":"publish_grade","grade":"A"}, {"grader_authorized":False,"rubric_evidence_verified":True,"rubric_grade":"A"}, "education.grading_authorization"),
    ("hospitality", {"operation":"confirm_booking","charged_total":"125.00"}, {"promo_validated":True,"payment_authorized":True,"server_total":"125.00"}, None),
    ("hospitality", {"operation":"confirm_booking","charged_total":"0"}, {"promo_validated":True,"payment_authorized":True,"server_total":"125.00"}, "hospitality.total_mismatch"),
    ("hospitality", {"operation":"confirm_booking","charged_total":"125"}, {"promo_validated":False,"payment_authorized":True,"server_total":"125"}, "hospitality.booking_authorization"),
    ("automotive", {"operation":"certify_brakes","brake_status":"pass"}, {"instrument_evidence_verified":True,"technician_authorized":True,"measured_brake_status":"pass"}, None),
    ("automotive", {"operation":"certify_brakes","brake_status":"pass"}, {"instrument_evidence_verified":True,"technician_authorized":True,"measured_brake_status":"fail"}, "automotive.brake_status_mismatch"),
    ("automotive", {"operation":"certify_brakes","brake_status":"pass"}, {"instrument_evidence_verified":False,"technician_authorized":True,"measured_brake_status":"pass"}, "automotive.inspection_unverified"),
])
def test_additional_sector_actions(sector, action, trusted, expected):
    findings = check(sector, action, trusted)
    assert tuple(x.rule_id for x in findings) == ((expected,) if expected else ())

@pytest.mark.parametrize("value", [None, "NaN", "-1", "invalid"])
def test_hospitality_invalid_amount(value):
    findings = check("hospitality", {"operation":"confirm_booking","charged_total":value},
                     {"promo_validated":True,"payment_authorized":True,"server_total":"100"})
    assert findings[0].rule_id == "sector.invalid_numeric_value"

@pytest.mark.parametrize("sector,action,trusted,expected", [
    ("construction", {"operation":"approve_curing","curing_days":"14"}, {"engineer_min_curing_days":"10","engineer_approved":True}, None),
    ("construction", {"operation":"approve_curing","curing_days":"0"}, {"engineer_min_curing_days":"10","engineer_approved":True}, "construction.curing_invariant"),
    ("construction", {"operation":"approve_curing","curing_days":"14"}, {"engineer_min_curing_days":"10","engineer_approved":False}, "construction.curing_invariant"),
    ("real_estate", {"operation":"issue_lease","escalation_clause_id":"approved-v1"}, {"contract_approved":True,"signatory_authorized":True,"approved_escalation_clause_id":"approved-v1"}, None),
    ("real_estate", {"operation":"issue_lease","escalation_clause_id":"zero-99-years"}, {"contract_approved":True,"signatory_authorized":True,"approved_escalation_clause_id":"approved-v1"}, "real_estate.clause_mismatch"),
    ("real_estate", {"operation":"issue_lease","escalation_clause_id":"approved-v1"}, {"contract_approved":False,"signatory_authorized":True,"approved_escalation_clause_id":"approved-v1"}, "real_estate.contract_authorization"),
    ("transportation", {"operation":"approve_route","destination_id":"safe-airport"}, {"route_authorized":True,"geofence_verified":True,"approved_destination_id":"safe-airport"}, None),
    ("transportation", {"operation":"approve_route","destination_id":"0,0"}, {"route_authorized":True,"geofence_verified":True,"approved_destination_id":"safe-airport"}, "transportation.destination_mismatch"),
    ("transportation", {"operation":"approve_route","destination_id":"safe-airport"}, {"route_authorized":True,"geofence_verified":False,"approved_destination_id":"safe-airport"}, "transportation.route_authorization"),
    ("public_administration", {"operation":"release_record","classification":"public"}, {"identity_verified":True,"record_access_authorized":True,"approved_release_classification":"public"}, None),
    ("public_administration", {"operation":"release_record","classification":"restricted"}, {"identity_verified":True,"record_access_authorized":True,"approved_release_classification":"public"}, "public_administration.classification_mismatch"),
    ("public_administration", {"operation":"release_record","classification":"public"}, {"identity_verified":True,"record_access_authorized":False,"approved_release_classification":"public"}, "public_administration.access_denied"),
])
def test_more_sector_actions(sector, action, trusted, expected):
    findings = check(sector, action, trusted)
    assert tuple(x.rule_id for x in findings) == ((expected,) if expected else ())

def test_construction_rejects_invalid_curing():
    result = check("construction", {"operation":"approve_curing","curing_days":"NaN"},
                   {"engineer_min_curing_days":"10","engineer_approved":True})
    assert result[0].rule_id == "sector.invalid_numeric_value"
