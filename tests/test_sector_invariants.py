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

@pytest.mark.parametrize("sector,action,trusted,expected", [
    ("agriculture", {"operation":"record_ndvi","ndvi":"0.65"}, {"sensor_provenance_verified":True}, None),
    ("agriculture", {"operation":"record_ndvi","ndvi":"10"}, {"sensor_provenance_verified":True}, "agriculture.invalid_ndvi"),
    ("agriculture", {"operation":"record_ndvi","ndvi":"0.65"}, {"sensor_provenance_verified":False}, "agriculture.untrusted_telemetry"),
    ("utilities", {"operation":"operate_breaker","breaker_id":"grid-4"}, {"operator_authenticated":True,"dispatch_approved":True,"approved_breaker_id":"grid-4"}, None),
    ("utilities", {"operation":"operate_breaker","breaker_id":"grid-5"}, {"operator_authenticated":True,"dispatch_approved":True,"approved_breaker_id":"grid-4"}, "utilities.target_mismatch"),
    ("utilities", {"operation":"operate_breaker","breaker_id":"grid-4"}, {"operator_authenticated":False,"dispatch_approved":True,"approved_breaker_id":"grid-4"}, "utilities.dispatch_denied"),
    ("mining", {"operation":"set_pressure","pressure_psi":"900"}, {"certified_max_pressure_psi":"1000","safety_interlock_verified":True,"operator_authorized":True}, None),
    ("mining", {"operation":"set_pressure","pressure_psi":"1500"}, {"certified_max_pressure_psi":"1000","safety_interlock_verified":True,"operator_authorized":True}, "mining.pressure_limit"),
    ("mining", {"operation":"set_pressure","pressure_psi":"900"}, {"certified_max_pressure_psi":"1000","safety_interlock_verified":False,"operator_authorized":True}, "mining.safety_authorization"),
    ("management", {"operation":"transfer_budget","beneficiary_id":"vendor-a","amount":"100"}, {"initiator_authenticated":True,"dual_approval_verified":True,"approved_beneficiary_id":"vendor-a","approved_transfer_limit":"200"}, None),
    ("management", {"operation":"transfer_budget","beneficiary_id":"vendor-b","amount":"100"}, {"initiator_authenticated":True,"dual_approval_verified":True,"approved_beneficiary_id":"vendor-a","approved_transfer_limit":"200"}, "management.beneficiary_mismatch"),
    ("management", {"operation":"transfer_budget","beneficiary_id":"vendor-a","amount":"300"}, {"initiator_authenticated":True,"dual_approval_verified":True,"approved_beneficiary_id":"vendor-a","approved_transfer_limit":"200"}, "management.transfer_limit"),
    ("management", {"operation":"transfer_budget","beneficiary_id":"vendor-a","amount":"100"}, {"initiator_authenticated":True,"dual_approval_verified":False,"approved_beneficiary_id":"vendor-a","approved_transfer_limit":"200"}, "management.transfer_authorization"),
])
def test_agriculture_utility_mining_management(sector, action, trusted, expected):
    findings = check(sector, action, trusted)
    assert tuple(x.rule_id for x in findings) == ((expected,) if expected else ())

@pytest.mark.parametrize("value", [None, "NaN", "Infinity", "-1"])
def test_mining_invalid_pressure(value):
    findings = check("mining", {"operation":"set_pressure","pressure_psi":value},
                     {"certified_max_pressure_psi":"1000","safety_interlock_verified":True,"operator_authorized":True})
    assert findings[0].rule_id == "sector.invalid_numeric_value"

@pytest.mark.parametrize("sector,action,trusted,expected", [
    ("information", {"operation":"publish_rag_document","document_id":"doc-1"}, {"source_access_authorized":True,"document_provenance_verified":True,"approved_document_id":"doc-1"}, None),
    ("information", {"operation":"publish_rag_document","document_id":"doc-2"}, {"source_access_authorized":True,"document_provenance_verified":True,"approved_document_id":"doc-1"}, "information.document_mismatch"),
    ("information", {"operation":"publish_rag_document","document_id":"doc-1"}, {"source_access_authorized":False,"document_provenance_verified":True,"approved_document_id":"doc-1"}, "information.untrusted_document"),
    ("professional", {"operation":"cite_legal_precedent","citation_id":"case-1"}, {"citation_verified":True,"jurisdiction_current":True,"approved_citation_id":"case-1"}, None),
    ("professional", {"operation":"cite_legal_precedent","citation_id":"fake-case"}, {"citation_verified":True,"jurisdiction_current":True,"approved_citation_id":"case-1"}, "professional.citation_mismatch"),
    ("professional", {"operation":"cite_legal_precedent","citation_id":"case-1"}, {"citation_verified":False,"jurisdiction_current":True,"approved_citation_id":"case-1"}, "professional.unverified_precedent"),
    ("administrative", {"operation":"send_it_notice","template_id":"training"}, {"sender_authenticated":True,"campaign_authorized":True,"approved_template_id":"training"}, None),
    ("administrative", {"operation":"send_it_notice","template_id":"fake-reset"}, {"sender_authenticated":True,"campaign_authorized":True,"approved_template_id":"training"}, "administrative.template_mismatch"),
    ("administrative", {"operation":"send_it_notice","template_id":"training"}, {"sender_authenticated":False,"campaign_authorized":True,"approved_template_id":"training"}, "administrative.notice_authorization"),
    ("arts", {"operation":"reserve_tickets","ticket_quantity":2}, {"account_authenticated":True,"reservation_token_verified":True,"account_ticket_limit":4}, None),
    ("arts", {"operation":"reserve_tickets","ticket_quantity":100}, {"account_authenticated":True,"reservation_token_verified":True,"account_ticket_limit":4}, "arts.ticket_limit"),
    ("arts", {"operation":"reserve_tickets","ticket_quantity":2}, {"account_authenticated":True,"reservation_token_verified":False,"account_ticket_limit":4}, "arts.reservation_authorization"),
])
def test_final_four_sector_actions(sector, action, trusted, expected):
    findings = check(sector, action, trusted)
    assert tuple(x.rule_id for x in findings) == ((expected,) if expected else ())

@pytest.mark.parametrize("value", [None, "NaN", "-1", "1.5", 0])
def test_ticket_quantity_must_be_positive_integer(value):
    findings = check("arts", {"operation":"reserve_tickets","ticket_quantity":value},
                     {"account_authenticated":True,"reservation_token_verified":True,"account_ticket_limit":4})
    assert findings[0].rule_id == "sector.invalid_numeric_value"

@pytest.mark.parametrize("sector,action,trusted,field", [
    ("healthcare", {"operation":"issue_prescription"}, {"licensed_prescriber_verified":True,"prescription_authorized":True}, "licensed_prescriber_verified"),
    ("wholesale", {"operation":"verify_receipt","verification_status":"verified"}, {"manifest_signature_valid":True,"physical_receipt_verified":True}, "manifest_signature_valid"),
    ("education", {"operation":"publish_grade","grade":"A"}, {"grader_authorized":True,"rubric_evidence_verified":True,"rubric_grade":"A"}, "grader_authorized"),
    ("hospitality", {"operation":"confirm_booking","charged_total":"100"}, {"promo_validated":True,"payment_authorized":True,"server_total":"100"}, "promo_validated"),
    ("automotive", {"operation":"certify_brakes","brake_status":"pass"}, {"instrument_evidence_verified":True,"technician_authorized":True,"measured_brake_status":"pass"}, "instrument_evidence_verified"),
    ("construction", {"operation":"approve_curing","curing_days":"10"}, {"engineer_approved":True,"engineer_min_curing_days":"7"}, "engineer_approved"),
    ("real_estate", {"operation":"issue_lease","escalation_clause_id":"a"}, {"contract_approved":True,"signatory_authorized":True,"approved_escalation_clause_id":"a"}, "contract_approved"),
    ("transportation", {"operation":"approve_route","destination_id":"a"}, {"route_authorized":True,"geofence_verified":True,"approved_destination_id":"a"}, "route_authorized"),
    ("public_administration", {"operation":"release_record","classification":"public"}, {"identity_verified":True,"record_access_authorized":True,"approved_release_classification":"public"}, "identity_verified"),
    ("agriculture", {"operation":"record_ndvi","ndvi":"0.5"}, {"sensor_provenance_verified":True}, "sensor_provenance_verified"),
    ("utilities", {"operation":"operate_breaker","breaker_id":"a"}, {"operator_authenticated":True,"dispatch_approved":True,"approved_breaker_id":"a"}, "operator_authenticated"),
    ("mining", {"operation":"set_pressure","pressure_psi":"10"}, {"safety_interlock_verified":True,"operator_authorized":True,"certified_max_pressure_psi":"20"}, "safety_interlock_verified"),
    ("management", {"operation":"transfer_budget","beneficiary_id":"a","amount":"10"}, {"initiator_authenticated":True,"dual_approval_verified":True,"approved_beneficiary_id":"a","approved_transfer_limit":"20"}, "initiator_authenticated"),
    ("information", {"operation":"publish_rag_document","document_id":"a"}, {"source_access_authorized":True,"document_provenance_verified":True,"approved_document_id":"a"}, "source_access_authorized"),
    ("professional", {"operation":"cite_legal_precedent","citation_id":"a"}, {"citation_verified":True,"jurisdiction_current":True,"approved_citation_id":"a"}, "citation_verified"),
    ("administrative", {"operation":"send_it_notice","template_id":"a"}, {"sender_authenticated":True,"campaign_authorized":True,"approved_template_id":"a"}, "sender_authenticated"),
    ("arts", {"operation":"reserve_tickets","ticket_quantity":2}, {"account_authenticated":True,"reservation_token_verified":True,"account_ticket_limit":4}, "account_authenticated"),
])
@pytest.mark.parametrize("spoof", ["false", "true", 1, 0, None, [], {}])
def test_sector_attestations_reject_nonboolean_values(sector, action, trusted, field, spoof):
    tampered = dict(trusted, **{field: spoof})
    findings = check(sector, action, tampered)
    assert findings[0].rule_id == "sector.invalid_attestation"

@pytest.mark.parametrize("sector,action,trusted,action_field,trusted_field", [
    ("information", {"operation":"publish_rag_document"}, {"source_access_authorized":True,"document_provenance_verified":True}, "document_id", "approved_document_id"),
    ("professional", {"operation":"cite_legal_precedent"}, {"citation_verified":True,"jurisdiction_current":True}, "citation_id", "approved_citation_id"),
    ("administrative", {"operation":"send_it_notice"}, {"sender_authenticated":True,"campaign_authorized":True}, "template_id", "approved_template_id"),
    ("utilities", {"operation":"operate_breaker"}, {"operator_authenticated":True,"dispatch_approved":True}, "breaker_id", "approved_breaker_id"),
    ("management", {"operation":"transfer_budget","amount":"10"}, {"initiator_authenticated":True,"dual_approval_verified":True,"approved_transfer_limit":"20"}, "beneficiary_id", "approved_beneficiary_id"),
    ("real_estate", {"operation":"issue_lease"}, {"contract_approved":True,"signatory_authorized":True}, "escalation_clause_id", "approved_escalation_clause_id"),
    ("transportation", {"operation":"approve_route"}, {"route_authorized":True,"geofence_verified":True}, "destination_id", "approved_destination_id"),
    ("public_administration", {"operation":"release_record"}, {"identity_verified":True,"record_access_authorized":True}, "classification", "approved_release_classification"),
    ("education", {"operation":"publish_grade"}, {"grader_authorized":True,"rubric_evidence_verified":True}, "grade", "rubric_grade"),
    ("automotive", {"operation":"certify_brakes"}, {"instrument_evidence_verified":True,"technician_authorized":True}, "brake_status", "measured_brake_status"),
])
def test_missing_reference_ids_never_pass_as_equal_none(sector, action, trusted, action_field, trusted_field):
    assert check(sector, action, trusted)[0].rule_id == "sector.invalid_reference"
    assert check(sector, dict(action, **{action_field: "x"}), trusted)[0].rule_id == "sector.invalid_reference"
    assert check(sector, action, dict(trusted, **{trusted_field: "x"}))[0].rule_id == "sector.invalid_reference"
    assert check(sector, dict(action, **{action_field: ""}),
                 dict(trusted, **{trusted_field: ""}))[0].rule_id == "sector.invalid_reference"
    assert check(sector, dict(action, **{action_field: "x"}),
                 dict(trusted, **{trusted_field: "x"})) == ()
