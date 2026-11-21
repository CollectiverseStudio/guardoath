"""Bounded read-only administrative views of GuardOath's published validation catalog.

The authoritative catalog is guardoath.control_registry.registry_payload. Admin
views are projections only; they never create or activate tenant rules.
"""
from __future__ import annotations

from typing import Callable
import re


_VERSION = re.compile(r"^[1-9][0-9]{0,8}$")
_CONTROL_ID = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
_RULE_ID = re.compile(r"^[A-Za-z0-9_.:-]{3,100}$")


def _public_text(value, *, limit: int) -> str:
    """Reject malformed registry metadata rather than stringifying private objects."""
    if (not isinstance(value, str) or len(value) > limit or
            any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise ValueError("Invalid published catalog metadata")
    return value


CatalogProvider = Callable[[], dict]


class CatalogRegistryConflict(ValueError):
    """Duplicate published identifiers make a source ambiguous, never editable."""


def published_catalog() -> dict:
    # Lazy: the isolated package can be tested without bundling the complete
    # GuardOath validation dependencies. Production uses the actual registry.
    from guardoath.control_registry import registry_payload
    return registry_payload()


def read_catalog(provider: CatalogProvider, *, domain_id: str | None = None) -> dict:
    """Project only stable public metadata from the published catalog.

    Do not accept caller-supplied definitions, raw detector implementation,
    sensitive examples, or implicit tenant policy overrides.
    """
    payload = provider()
    if (not isinstance(payload, dict) or not isinstance(payload.get("domains"), list)
            or len(payload["domains"]) > 128 or
            not isinstance(payload.get("registry_version"), str) or
            not _VERSION.fullmatch(payload["registry_version"])):
        raise ValueError("Published catalog unavailable")
    domains = []
    category_ids, check_ids, rule_ids = set(), set(), set()
    total_control_count = total_rule_count = 0
    for domain in payload["domains"]:
        if not isinstance(domain, dict) or not isinstance(domain.get("domain_id"), str):
            raise ValueError("Invalid published domain")
        category_id = domain["domain_id"]
        if not _CONTROL_ID.fullmatch(category_id):
            raise ValueError("Invalid published category")
        category_name = _public_text(domain.get("name", ""), limit=255)
        category_status = _public_text(domain.get("status", "unknown"), limit=32)
        if category_id in category_ids:
            raise CatalogRegistryConflict("Duplicate published category")
        category_ids.add(category_id)
        controls = domain.get("controls")
        if not isinstance(controls, list) or len(controls) > 128:
            raise ValueError("Invalid published controls")
        total_control_count += len(controls)
        if total_control_count > 512:
            raise ValueError("Oversized published catalog")
        items = []
        for check in controls:
            if not isinstance(check, dict) or not isinstance(check.get("control_id"), str):
                raise ValueError("Invalid published control")
            control_id = check["control_id"]
            if not _CONTROL_ID.fullmatch(control_id):
                raise ValueError("Invalid published check")
            check_name = _public_text(check.get("name", ""), limit=255)
            severity = _public_text(check.get("severity", "unknown"), limit=32)
            check_use_case = _public_text(check.get("use_case", ""), limit=4096)
            limitations = _public_text(check.get("limitations", ""), limit=4096)
            if control_id in check_ids:
                raise CatalogRegistryConflict("Duplicate published check")
            check_ids.add(control_id)
            rules = check.get("rules")
            if not isinstance(rules, list) or len(rules) > 5000:
                raise ValueError("Invalid published rules")
            total_rule_count += len(rules)
            if total_rule_count > 20000:
                raise ValueError("Oversized published rules")
            declared = check.get("rule_count")
            if declared is not None and (type(declared) is not int or declared != len(rules)):
                raise ValueError("Published rule count mismatch")
            for rule in rules:
                if (not isinstance(rule, dict) or
                        not isinstance(rule.get("rule_id"), str) or
                        not _RULE_ID.fullmatch(rule["rule_id"])):
                    raise ValueError("Invalid published rule")
                _public_text(rule.get("title", ""), limit=255)
                _public_text(rule.get("use_case", ""), limit=4096)
                if rule["rule_id"] in rule_ids:
                    raise CatalogRegistryConflict("Duplicate published rule")
                rule_ids.add(rule["rule_id"])
            evidence = check.get("evidence")
            if evidence is not None and not isinstance(evidence, dict):
                raise ValueError("Invalid published test declarations")
            counts = {}
            for field, key in (("unit_tests", "unit_test_files_declared"),
                               ("evaluations", "evaluation_files_declared")):
                references = evidence.get(field) if evidence is not None else None
                if references is not None and (
                        not isinstance(references, list) or len(references) > 200 or
                        any(not isinstance(item, str) or not item for item in references)):
                    raise ValueError("Invalid published test declarations")
                counts[key] = len(references) if references is not None else None
            # Explicit malformed empty objects/false/null are not valid empty lists.
            stages = check.get("stages", [])
            actions = check.get("actions", [])
            if (not isinstance(stages, list) or
                    any(not isinstance(stage, str) for stage in stages) or
                    len(set(stages)) != len(stages) or
                    any(stage not in ("input", "output") for stage in stages) or
                    not isinstance(actions, list) or
                    any(not isinstance(action, str) for action in actions) or
                    len(set(actions)) != len(actions) or
                    any(action not in ("block", "flag", "allow", "redact") for action in actions)):
                raise ValueError("Invalid published check eligibility")
            if domain_id is not None and category_id != domain_id:
                continue
            items.append({
                "check_id": check["control_id"],
                "name": check_name,
                "severity": severity,
                "stages": list(stages),
                "supported_actions": list(actions),
                "use_case": check_use_case,
                "limitations": limitations,
                "test_evidence": {**counts,
                    "ci_verified_for_source": False, "live_deployment_verified": None,
                    "basis": "registry declarations, not executed test results"},
                "rule_count": len(rules),
                "rules": [{"rule_id": rule["rule_id"],
                           "title": _public_text(rule.get("title", ""), limit=255),
                           "use_case": _public_text(rule.get("use_case", ""), limit=4096)}
                          for rule in rules],
            })
        if domain_id is not None and category_id != domain_id:
            continue
        domains.append({
            "category_id": category_id,
            "name": category_name,
            "status": category_status,
            "checks": items,
        })
    return {
        "registry_version": payload["registry_version"],
        "source": "published GuardOath control registry",
        "read_only": True,
        "categories": domains,
        "total_categories": len(domains),
        "total_checks": sum(len(item["checks"]) for item in domains),
        "total_rules": sum(check["rule_count"] for item in domains for check in item["checks"]),
    }
