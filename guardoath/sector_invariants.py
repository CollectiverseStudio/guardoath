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
    }
    if sector not in supported:
        raise ValueError("unsupported sector")
    if action.get("operation") != supported[sector]:
        return (SectorFinding("sector.operation_mismatch", "unapproved operation"),)
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
