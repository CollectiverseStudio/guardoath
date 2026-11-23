"""Static application guidance, separate from model output and stored evidence.

Only a verified response/receipt's status and recorded check identifiers select
these messages. No prompt, model text, error body or free-form provider metadata
is read, interpolated, classified, retained or sent anywhere by this module.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Literal


NoticeKind = Literal[
    "safety_support", "input_blocked", "output_blocked", "validation_incomplete",
    "unavailable", "outcome_unknown", "quota_exceeded", "receipt_only", "not_completed",
]

NOTICE_MESSAGES = MappingProxyType({
    "safety_support": (
        "No model answer is being shown. If this relates to a real concern about your safety or someone else's, "
        "reach out to someone you trust or an appropriate local support service. If there is immediate danger, "
        "contact local emergency services. GuardOath has not contacted anyone."
    ),
    "input_blocked": (
        "Your saved Input policy blocked this request before a model was called. No model answer is available. "
        "Review the receipt for the applicable checks."
    ),
    "output_blocked": (
        "Your saved Output policy withheld the model's answer. No rejected model text is shown. "
        "Review the receipt for the applicable checks."
    ),
    "validation_incomplete": (
        "GuardOath could not complete the required validation, so no model answer is being shown. "
        "Keep this request ID and refresh its receipt before starting a separate request."
    ),
    "unavailable": (
        "No model answer could be released for this request. Keep this request ID and review the receipt "
        "for its status. Do not retry generation automatically."
    ),
    "outcome_unknown": (
        "The model's outcome is uncertain. It may have run and consumed quota. Keep this request ID and "
        "refresh its receipt; do not start another generation to recover this answer."
    ),
    "quota_exceeded": (
        "This request could not finish within the configured allowance. No model answer is being shown. "
        "Review the receipt and the application's usage limits."
    ),
    "receipt_only": (
        "Stored receipts contain no model answer. Viewing or replaying this receipt does not start another model call. "
        "Keep the request ID and inspect the recorded status before starting a separate request."
    ),
    "not_completed": (
        "No completed, checked model answer is available. Keep this request ID and refresh its receipt "
        "before starting another request."
    ),
})

# A focused set of the existing recorded self-harm/violence indicators. Other
# findings (for example a financial claim or an ordinary insult) do not select
# support guidance. Quoting one of these indicators still does not establish
# real-world risk; the support message is expressly conditional.
SUPPORT_FINDINGS = MappingProxyType({
    "human_safety": frozenset({
        "human_safety.suicidal_statement",
        "human_safety.self_harm_statement",
        "human_safety.self_harm_method_request",
        "human_safety.lethal_dose_request",
        "human_safety.output_lethal_dose",
        "human_safety.output_self_harm_method",
        "human_safety.output_encourages_self_harm",
        "human_safety.output_discourages_crisis_help",
        "human_safety.intent_to_hurt_someone",
        "human_safety.weapon_to_hurt",
        "human_safety.output_attack_planning",
    }),
    "harmful_content": frozenset({
        "harmful_content.threat_to_hurt_you",
        "harmful_content.threat_to_family",
        "harmful_content.bomb_threat",
        "harmful_content.mass_shooting_threat",
        "harmful_content.output_weapon_instructions",
        "harmful_content.poison_a_person",
        "harmful_content.hire_violence",
        "harmful_content.output_violent_encouragement",
    }),
})


def _recorded_support_finding(receipt):
    for check in (receipt.input_check, receipt.output_check):
        if check is None or check.simulated:
            continue
        for checker_id, finding_ids in SUPPORT_FINDINGS.items():
            if (checker_id in check.checker_ids and
                    any(checker.checker_id == checker_id and checker.status == "flagged"
                        for checker in check.checker_results) and
                    any(finding_id in finding_ids for finding_id in check.finding_ids)):
                return True
    return False


def notice_payload(receipt, *, replayed=False, response_available=False):
    """Derive a bounded notice from recorded evidence, never a safety verdict.

No notice authorizes release, execution, an automatic retry or human contact.
Receipt GETs remain metadata-only and do not persist this presentation field.
"""
    status = receipt.status
    if status == "completed":
        if not replayed and response_available:
            return None
        kind = "receipt_only"
    elif status in {"input_blocked", "output_blocked", "input_incomplete", "output_incomplete"}:
        if _recorded_support_finding(receipt):
            kind = "safety_support"
        elif status.endswith("incomplete"):
            kind = "validation_incomplete"
        else:
            kind = status
    elif status == "outcome_unknown":
        kind = "outcome_unknown"
    elif status == "quota_exceeded":
        kind = "quota_exceeded"
    elif status in {"provider_withheld", "provider_rejected", "unavailable"}:
        kind = "unavailable"
    else:
        kind = "not_completed"
    return {"kind": kind, "message": NOTICE_MESSAGES[kind]}
