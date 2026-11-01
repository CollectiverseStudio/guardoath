"""Agent configuration hardening signals (40 rules).

Review-only lexical indicators, not authorization or runtime enforcement.
Each entry carries a matching example and a benign look-alike.
"""
from __future__ import annotations
from dataclasses import dataclass
import re

@dataclass(frozen=True)
class Signal:
    rule_id: str
    severity: str = "high"

# Field names are specific configuration keys. This intentionally avoids generic
# 'disable security' prose, which is too ambiguous for automatic blocking.
FIELDS = (
    "approval_required", "require_tool_auth", "tool_audit_enabled",
    "tool_output_validation", "tool_input_validation", "agent_identity_check",
    "verify_tool_signature", "redact_tool_results", "rollback_enabled",
    "confirm_destructive_actions", "require_idempotency_key",
    "replay_protection_enabled", "sandbox_enabled", "network_egress_filter",
    "tenant_isolation_enabled", "verify_webhook_signature", "verify_tls",
    "mfa_required", "session_binding_enabled", "csrf_protection_enabled",
    "audit_logging_enabled", "secret_scanning_enabled", "dlp_enabled",
    "content_filter_enabled", "rate_limit_enabled", "budget_enforcement_enabled",
    "human_review_required", "policy_validation_enabled",
    "credential_rotation_enabled", "encrypt_at_rest", "backup_enabled",
    "deletion_protection_enabled", "signed_artifacts_required",
    "dependency_scanning_enabled", "sast_enabled", "branch_protection_enabled",
    "require_signed_commits", "enforce_least_privilege",
    "tool_allowlist_enabled", "pii_redaction_enabled",
)
assert len(FIELDS) == 40
CASES = {
    "config6." + field: (
        re.compile(r"(?<![\w.])" + re.escape(field) + r"\s*[=:]\s*(?:false|off|0)\b", re.I),
        field + "=false",
        field + "=true",
    )
    for field in FIELDS
}

def inspect(content: str) -> tuple[Signal, ...]:
    if not isinstance(content, str):
        raise TypeError("content must be text")
    if len(content) > 32000:
        raise ValueError("input exceeds character budget")
    return tuple(Signal(name) for name, (rx, _, _) in CASES.items() if rx.search(content))
