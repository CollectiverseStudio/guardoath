"""Consolidate equivalent lexical findings without losing distinct security risks.

The alias table is deliberately small and explicit. The canonical rule remains
the one emitted by the existing batch-5 detector. This is a pure helper:
production integration is owned by the Director.
"""
from __future__ import annotations
from collections.abc import Iterable

# These pairs describe the same control; they are not a general-purpose
# equivalence rule for different checks or different affected resources.
ALIASES = {
    "config6.verify_webhook_signature": "agent5.disable_webhook_signature",
    "config6.verify_tls": "agent5.disable_tls_cert",
    "config6.audit_logging_enabled": "agent5.disable_audit_setting",
    "config6.secret_scanning_enabled": "agent5.disable_secret_scan",
    "config6.content_filter_enabled": "agent5.disable_content_filter",
    "config6.dlp_enabled": "agent5.disable_dlp_setting",
    "config6.backup_enabled": "agent5.disable_backup_setting",
    "config6.encrypt_at_rest": "agent5.disable_encryption_setting",
}

def consolidate(rule_ids: Iterable[str]) -> tuple[str, ...]:
    """Return stable, unique canonical IDs, preserving all unrelated findings.

    Never accepts prompt content or produces any user-controlled explanation.
    Unknown rule IDs are retained, not discarded.
    """
    if isinstance(rule_ids, (str, bytes)):
        raise TypeError("rule_ids must be an iterable of IDs, not a string")
    result = []
    seen = set()
    for rule_id in rule_ids:
        if not isinstance(rule_id, str) or not rule_id or len(rule_id) > 128:
            raise ValueError("invalid rule ID")
        canonical = ALIASES.get(rule_id, rule_id)
        if canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
    return tuple(result)
