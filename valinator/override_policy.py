"""Tenant-specific override authorization. Never bypass hard controls.

The caller must supply an authenticated actor from its own identity provider.
This library only decides eligibility; it does not route content to an AI.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class OverridePolicy:
    tenant_id: str
    allowed_rules: frozenset[str]
    reviewer_required_rules: frozenset[str] = frozenset()

    def __post_init__(self):
        if not self.tenant_id or not self.reviewer_required_rules <= self.allowed_rules:
            raise ValueError("Invalid tenant override policy")


@dataclass(frozen=True)
class OverrideDecision:
    allowed: bool
    reason: str


def decide_override(*, policy: OverridePolicy, tenant_id: str,
                    finding_ids: tuple[str, ...], approved_by_reviewer: bool,
                    assessment_complete: bool) -> OverrideDecision:
    if not tenant_id or tenant_id != policy.tenant_id:
        return OverrideDecision(False, "tenant_mismatch")
    if not assessment_complete:
        return OverrideDecision(False, "incomplete_assessment")
    if not finding_ids or len(set(finding_ids)) != len(finding_ids):
        return OverrideDecision(False, "invalid_findings")
    if any(f not in policy.allowed_rules for f in finding_ids):
        return OverrideDecision(False, "rule_not_overrideable")
    if any(f in policy.reviewer_required_rules for f in finding_ids) and not approved_by_reviewer:
        return OverrideDecision(False, "reviewer_approval_required")
    return OverrideDecision(True, "eligible_for_controlled_resubmission")
