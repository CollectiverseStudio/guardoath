"""Pure, opt-in sector invariants for proposed AI tool calls.

The host supplies trusted limits and authoritative values. No commands are run.
These checks are not substitutes for physical interlocks, payment settlement,
licensed clinical workflows or actual authorization at dispatch.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping, Any

@dataclass(frozen=True)
class SectorFinding:
    rule_id: str
    reason: str

def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None

def validate_sector_action(sector: str, action: Mapping[str, Any],
                           trusted: Mapping[str, Any]) -> tuple[SectorFinding, ...]:
    """Evaluate known dangerous deviations from trusted state.

    Unknown sectors/actions are rejected explicitly; this helper must not
    interpret a prompt-supplied limit as authoritative.
    """
    if not isinstance(action, Mapping) or not isinstance(trusted, Mapping):
        raise TypeError("action and trusted configuration must be mappings")
    supported = {
        "retail": "checkout",
        "finance": "claim_payout",
        "manufacturing": "set_machine_speed",
        "healthcare": "issue_prescription",
        "wholesale": "verify_receipt",
        "education": "publish_grade",
        "hospitality": "confirm_booking",
        "automotive": "certify_brakes",
        "construction": "approve_curing",
        "real_estate": "issue_lease",
        "transportation": "approve_route",
        "public_administration": "release_record",
        "agriculture": "record_ndvi",
        "utilities": "operate_breaker",
        "mining": "set_pressure",
        "management": "transfer_budget",
        "information": "publish_rag_document",
        "professional": "cite_legal_precedent",
        "administrative": "send_it_notice",
        "arts": "reserve_tickets",
    }
    if sector not in supported:
        raise ValueError("unsupported sector")
    if action.get("operation") != supported[sector]:
        return (SectorFinding("sector.operation_mismatch", "unapproved operation"),)
    # Host attestations must be genuine booleans. Strings such as "false"
    # are truthy in Python and must never authorize safety-critical actions.
    attestation_fields = {
        "healthcare": ("licensed_prescriber_verified", "prescription_authorized"),
        "wholesale": ("manifest_signature_valid", "physical_receipt_verified"),
        "education": ("grader_authorized", "rubric_evidence_verified"),
        "hospitality": ("promo_validated", "payment_authorized"),
        "automotive": ("instrument_evidence_verified", "technician_authorized"),
        "construction": ("engineer_approved",),
        "real_estate": ("contract_approved", "signatory_authorized"),
        "transportation": ("route_authorized", "geofence_verified"),
        "public_administration": ("identity_verified", "record_access_authorized"),
        "agriculture": ("sensor_provenance_verified",),
        "utilities": ("operator_authenticated", "dispatch_approved"),
        "mining": ("safety_interlock_verified", "operator_authorized"),
        "management": ("initiator_authenticated", "dual_approval_verified"),
        "information": ("source_access_authorized", "document_provenance_verified"),
        "professional": ("citation_verified", "jurisdiction_current"),
        "administrative": ("sender_authenticated", "campaign_authorized"),
        "arts": ("account_authenticated", "reservation_token_verified"),
    }
    if any(field in trusted and type(trusted[field]) is not bool
           for field in attestation_fields.get(sector, ())):
        return (SectorFinding("sector.invalid_attestation", "trusted attestation must be boolean"),)
    if sector == "information":
        if not trusted.get("source_access_authorized") or not trusted.get("document_provenance_verified"):
            return (SectorFinding("information.untrusted_document", "retrieval source not authorized or verified"),)
        if action.get("document_id") != trusted.get("approved_document_id"):
            return (SectorFinding("information.document_mismatch", "document differs from approved source"),)
        return ()
    if sector == "professional":
        if not trusted.get("citation_verified") or not trusted.get("jurisdiction_current"):
            return (SectorFinding("professional.unverified_precedent", "legal citation or jurisdiction not verified"),)
        if action.get("citation_id") != trusted.get("approved_citation_id"):
            return (SectorFinding("professional.citation_mismatch", "citation differs from verified reference"),)
        return ()
    if sector == "administrative":
        if not trusted.get("sender_authenticated") or not trusted.get("campaign_authorized"):
            return (SectorFinding("administrative.notice_authorization", "trusted communication authorization missing"),)
        if action.get("template_id") != trusted.get("approved_template_id"):
            return (SectorFinding("administrative.template_mismatch", "message template not approved"),)
        return ()
    if sector == "arts":
        if not trusted.get("account_authenticated") or not trusted.get("reservation_token_verified"):
            return (SectorFinding("arts.reservation_authorization", "trusted ticket reservation evidence missing"),)
        quantity = _decimal(action.get("ticket_quantity"))
        maximum = _decimal(trusted.get("account_ticket_limit"))
        if quantity is None or maximum is None or quantity != quantity.to_integral_value() or maximum != maximum.to_integral_value() or quantity < 1 or maximum < 1:
            return (SectorFinding("sector.invalid_numeric_value", "missing or invalid numeric evidence"),)
        if quantity > maximum:
            return (SectorFinding("arts.ticket_limit", "ticket quantity exceeds account limit"),)
        return ()
    if sector == "agriculture":
        value = _decimal(action.get("ndvi"))
        if value is None or value < -1 or value > 1:
            return (SectorFinding("agriculture.invalid_ndvi", "NDVI must be within physical bounds"),)
        if not trusted.get("sensor_provenance_verified"):
            return (SectorFinding("agriculture.untrusted_telemetry", "sensor provenance not verified"),)
        return ()
    if sector == "utilities":
        if not trusted.get("operator_authenticated") or not trusted.get("dispatch_approved"):
            return (SectorFinding("utilities.dispatch_denied", "operator or dispatch approval missing"),)
        if action.get("breaker_id") != trusted.get("approved_breaker_id"):
            return (SectorFinding("utilities.target_mismatch", "breaker differs from approved target"),)
        return ()
    if sector == "mining":
        actual, maximum = _decimal(action.get("pressure_psi")), _decimal(trusted.get("certified_max_pressure_psi"))
        if actual is None or maximum is None or actual < 0 or maximum <= 0:
            return (SectorFinding("sector.invalid_numeric_value", "missing or invalid numeric evidence"),)
        if not trusted.get("safety_interlock_verified") or not trusted.get("operator_authorized"):
            return (SectorFinding("mining.safety_authorization", "trusted safety authorization missing"),)
        if actual > maximum:
            return (SectorFinding("mining.pressure_limit", "pressure exceeds certified maximum"),)
        return ()
    if sector == "management":
        if not trusted.get("initiator_authenticated") or not trusted.get("dual_approval_verified"):
            return (SectorFinding("management.transfer_authorization", "trusted transfer approval missing"),)
        if action.get("beneficiary_id") != trusted.get("approved_beneficiary_id"):
            return (SectorFinding("management.beneficiary_mismatch", "beneficiary differs from approved recipient"),)
        amount, maximum = _decimal(action.get("amount")), _decimal(trusted.get("approved_transfer_limit"))
        if amount is None or maximum is None or amount < 0 or maximum < 0:
            return (SectorFinding("sector.invalid_numeric_value", "missing or invalid numeric evidence"),)
        if amount > maximum:
            return (SectorFinding("management.transfer_limit", "amount exceeds approved limit"),)
        return ()
    if sector == "construction":
        actual, minimum = _decimal(action.get("curing_days")), _decimal(trusted.get("engineer_min_curing_days"))
        if actual is None or minimum is None or actual < 0 or minimum < 0:
            return (SectorFinding("sector.invalid_numeric_value", "missing or invalid numeric evidence"),)
        if not trusted.get("engineer_approved") or actual < minimum:
            return (SectorFinding("construction.curing_invariant", "engineering curing approval or minimum missing"),)
        return ()
    if sector == "real_estate":
        if not trusted.get("contract_approved") or not trusted.get("signatory_authorized"):
            return (SectorFinding("real_estate.contract_authorization", "trusted contract approval missing"),)
        if action.get("escalation_clause_id") != trusted.get("approved_escalation_clause_id"):
            return (SectorFinding("real_estate.clause_mismatch", "lease clause differs from approved template"),)
        return ()
    if sector == "transportation":
        if not trusted.get("route_authorized") or not trusted.get("geofence_verified"):
            return (SectorFinding("transportation.route_authorization", "route approval or geofence verification missing"),)
        if action.get("destination_id") != trusted.get("approved_destination_id"):
            return (SectorFinding("transportation.destination_mismatch", "destination differs from approved route"),)
        return ()
    if sector == "public_administration":
        if not trusted.get("identity_verified") or not trusted.get("record_access_authorized"):
            return (SectorFinding("public_administration.access_denied", "verified access authorization missing"),)
        if action.get("classification") != trusted.get("approved_release_classification"):
            return (SectorFinding("public_administration.classification_mismatch", "record classification not approved for release"),)
        return ()
    if sector == "wholesale":
        if not trusted.get("manifest_signature_valid") or not trusted.get("physical_receipt_verified"):
            return (SectorFinding("wholesale.receipt_unverified", "trusted receipt verification missing"),)
        if action.get("verification_status") != "verified":
            return (SectorFinding("wholesale.invalid_receipt_state", "unapproved receipt status"),)
        return ()
    if sector == "education":
        if not trusted.get("grader_authorized") or not trusted.get("rubric_evidence_verified"):
            return (SectorFinding("education.grading_authorization", "trusted grading evidence missing"),)
        if action.get("grade") != trusted.get("rubric_grade"):
            return (SectorFinding("education.grade_mismatch", "grade differs from verified rubric"),)
        return ()
    if sector == "hospitality":
        if not trusted.get("promo_validated") or not trusted.get("payment_authorized"):
            return (SectorFinding("hospitality.booking_authorization", "trusted promotion/payment evidence missing"),)
        actual, reference = _decimal(action.get("charged_total")), _decimal(trusted.get("server_total"))
        if actual is None or reference is None or actual < 0 or reference < 0:
            return (SectorFinding("sector.invalid_numeric_value", "missing or invalid numeric evidence"),)
        if actual != reference:
            return (SectorFinding("hospitality.total_mismatch", "booking total differs from trusted server calculation"),)
        return ()
    if sector == "automotive":
        if not trusted.get("instrument_evidence_verified") or not trusted.get("technician_authorized"):
            return (SectorFinding("automotive.inspection_unverified", "trusted inspection evidence missing"),)
        if action.get("brake_status") != trusted.get("measured_brake_status"):
            return (SectorFinding("automotive.brake_status_mismatch", "certification differs from verified measurement"),)
        return ()
    if sector == "healthcare":
        if not trusted.get("licensed_prescriber_verified") or not trusted.get("prescription_authorized"):
            return (SectorFinding("healthcare.prescriber_authorization", "trusted clinical authorization missing"),)
        return ()
    amount_key, max_key = {
        "retail": ("charged_total", "server_total"),
        "finance": ("payout", "approved_payout_limit"),
        "manufacturing": ("speed", "certified_max_speed"),
    }[sector]
    actual = _decimal(action.get(amount_key))
    reference = _decimal(trusted.get(max_key))
    if actual is None or reference is None or actual < 0 or reference < 0:
        return (SectorFinding("sector.invalid_numeric_value", "missing or invalid numeric evidence"),)
    if sector == "retail" and actual != reference:
        return (SectorFinding("retail.total_mismatch", "checkout total differs from trusted server calculation"),)
    if sector == "finance" and actual > reference:
        return (SectorFinding("finance.payout_limit", "payout exceeds approved limit"),)
    if sector == "manufacturing" and actual > reference:
        return (SectorFinding("manufacturing.speed_limit", "setpoint exceeds certified limit"),)
    return ()
