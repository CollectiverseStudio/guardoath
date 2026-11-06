"""FC-038/039/040/048: tenant-owned identity and trusted-reference checks.

Trusted quarantine/permission values are read from the saved tenant Policy,
never from the request document. Findings do not include submitted/reference
contents. These local checks do not verify a remote authority.
"""
import re

from guardoath.business import resolve
from guardoath.models import Finding

MAX_MEMBERS = 128
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,100}")


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("invalid_identifier")
    return value


def _members(value):
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_MEMBERS:
        raise ValueError("invalid_member_array")
    return [_identifier(item) for item in value]


def _valid(rule, payload):
    fields = {name: resolve(payload, pointer) for name, pointer in rule.fields.items()}
    if rule.kind == "order_id_match":
        return _identifier(fields["order_id"]) == _identifier(fields["shipment_order_id"])
    if rule.kind == "quarantine_exclusion":
        return _identifier(fields["requested_lot"]) not in rule.trusted_values
    if rule.kind == "serial_unique":
        ids = _members(fields["serials"])
        return len(set(ids)) == len(ids)
    if rule.kind == "authorized_scope_subset":
        requested = _members(fields["requested_scopes"])
        return set(requested).issubset(rule.trusted_values)
    return False


def identity_findings(payload, rules, stage):
    findings = []
    for rule in rules:
        if stage not in rule.stages:
            continue
        try:
            valid = _valid(rule, payload)
        except (KeyError, TypeError, IndexError, ValueError, OverflowError):
            valid = False
        if not valid:
            findings.append(Finding(
                rule_id="business." + rule.rule_id, category="business", severity="high",
                explanation="Configured identity, uniqueness or tenant-trusted reference constraint failed.",
            ))
    return tuple(findings)
