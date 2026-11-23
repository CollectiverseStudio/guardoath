"""Audited owner inspection of a tenant's saved, stage-specific catalog rule action.

Read-only and redacted. A rule override cannot re-enable a check disabled
on that stage; the tenant's validated record remains the only policy source.
This is configured state, not proof that a detector executed or a live release.
"""
from __future__ import annotations

from guardoath.rule_catalog import RULES
from guardoath.tenant_record import TenantRecord, CHECK_IDS


class RuleSelectionInvalid(ValueError):
    """An unknown or unsupported system rule was selected."""


class RuleInspectionUnavailable(ValueError):
    """The authoritative tenant policy could not be safely inspected."""


def inspect_tenant_rule_action(record, *, tenant_id: str, stage: str, rule_id: str) -> dict:
    if (stage not in ("input", "output") or not isinstance(rule_id, str)
            or rule_id not in RULES):
        raise RuleSelectionInvalid("Published rule or stage unavailable")
    rule = RULES[rule_id]
    if rule.control_id not in CHECK_IDS:
        raise RuleSelectionInvalid("Published rule has no tenant-owned check")
    try:
        # Do not trust a synthetic/partial record, model_copy(update=...) or
        # an accidental cross-tenant lookup as the source of administrative state.
        if type(record) is not TenantRecord:
            raise RuleInspectionUnavailable("Typed tenant policy required")
        validated = TenantRecord.model_validate(record.model_dump(mode="python"))
        if validated.tenant_id != tenant_id or type(validated.revision) is not int or validated.revision < 1:
            raise RuleInspectionUnavailable("Tenant policy identity/revision unavailable")
        check = getattr(validated, stage + "_check_" + rule.control_id)
        check_action = check.value if check is not None else None
        overrides = getattr(validated, stage + "_rule_actions")
        if not isinstance(overrides, dict):
            raise RuleInspectionUnavailable("Saved rule override unavailable")
        selected = overrides.get(rule_id, "same_as_check")
        if selected not in ("same_as_check", "off", "allow", "flag", "block"):
            raise RuleInspectionUnavailable("Invalid rule override")
        # Explicit Off at check level wins over even a saved per-rule Block.
        eligibility = ("check_off" if check_action is None
                       else "rule_off" if selected == "off"
                       else "configured_not_execution_evidence")
        return {
            "tenant_id": tenant_id, "revision": validated.revision,
            "rule_id": rule_id, "check_id": rule.control_id, "stage": stage,
            "check_action": check_action, "rule_action": selected,
            "eligibility": eligibility,
            "source": "validated saved tenant record; not live evaluation or detector activation",
            "tenant_policies_changed": False,
        }
    except RuleInspectionUnavailable:
        raise
    except Exception as exc:
        raise RuleInspectionUnavailable("Tenant rule inspection unavailable") from exc
