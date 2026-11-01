"""Pure sector pack selection and policy decisions.

The trusted host must provide verified sector entitlements and evidence. This
helper does not authenticate, authorize, reserve, or execute external actions.
"""
from __future__ import annotations
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any

from .sector_invariants import validate_sector_action

SECTORS = frozenset({
    "agriculture", "mining", "utilities", "construction", "manufacturing",
    "wholesale", "retail", "transportation", "information", "finance",
    "real_estate", "professional", "management", "administrative", "education",
    "healthcare", "arts", "hospitality", "automotive", "public_administration",
})

@dataclass(frozen=True)
class PackDecision:
    status: str
    reason_codes: tuple[str, ...]
    finding_ids: tuple[str, ...]

def evaluate_sector_pack(sector: str, action: Mapping[str, Any],
                         evidence: Mapping[str, Any], *,
                         enabled_sectors: frozenset[str],
                         identity_verified: bool,
                         tenant_authorized: bool) -> PackDecision:
    if sector not in SECTORS:
        return PackDecision("block", ("unknown_sector",), ())
    if not isinstance(enabled_sectors, frozenset) or not enabled_sectors <= SECTORS:
        return PackDecision("block", ("invalid_entitlements",), ())
    if sector not in enabled_sectors:
        return PackDecision("block", ("sector_not_enabled",), ())
    if type(identity_verified) is not bool or type(tenant_authorized) is not bool:
        return PackDecision("block", ("invalid_identity_evidence",), ())
    if not identity_verified or not tenant_authorized:
        reasons = tuple(code for failed, code in (
            (not identity_verified, "identity_unverified"),
            (not tenant_authorized, "tenant_unauthorized"),
        ) if failed)
        return PackDecision("block", reasons, ())
    if not isinstance(action, Mapping) or not isinstance(evidence, Mapping):
        return PackDecision("block", ("invalid_action_or_evidence",), ())
    try:
        findings = validate_sector_action(sector, action, evidence)
    except (TypeError, ValueError):
        return PackDecision("block", ("validation_error",), ())
    if findings:
        return PackDecision("block", ("sector_invariant_failed",),
                            tuple(f.rule_id for f in findings))
    return PackDecision("allow", (), ())
