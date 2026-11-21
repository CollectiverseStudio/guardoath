"""Bounded read-only administrative views of GuardOath's published validation catalog.

The authoritative catalog is guardoath.control_registry.registry_payload. Admin
views are projections only; they never create or activate tenant rules.
"""
from __future__ import annotations

from typing import Callable


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
    if not isinstance(payload, dict) or not isinstance(payload.get("domains"), list):
        raise ValueError("Published catalog unavailable")
    domains = []
    category_ids, check_ids, rule_ids = set(), set(), set()
    for domain in payload["domains"]:
        if not isinstance(domain, dict) or not isinstance(domain.get("domain_id"), str):
            raise ValueError("Invalid published domain")
        category_id = domain["domain_id"]
        if not category_id:
            raise ValueError("Empty published category")
        if category_id in category_ids:
            raise CatalogRegistryConflict("Duplicate published category")
        category_ids.add(category_id)
        controls = domain.get("controls")
        if not isinstance(controls, list):
            raise ValueError("Invalid published controls")
        items = []
        for check in controls:
            if not isinstance(check, dict) or not isinstance(check.get("control_id"), str):
                raise ValueError("Invalid published control")
            control_id = check["control_id"]
            if not control_id:
                raise ValueError("Empty published check")
            if control_id in check_ids:
                raise CatalogRegistryConflict("Duplicate published check")
            check_ids.add(control_id)
            rules = check.get("rules")
            if not isinstance(rules, list):
                raise ValueError("Invalid published rules")
            declared = check.get("rule_count")
            if declared is not None and (type(declared) is not int or declared != len(rules)):
                raise ValueError("Published rule count mismatch")
            for rule in rules:
                if (not isinstance(rule, dict) or
                        not isinstance(rule.get("rule_id"), str) or
                        not rule["rule_id"]):
                    raise ValueError("Invalid published rule")
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
            stages = check.get("stages") or []
            actions = check.get("actions") or []
            if (not isinstance(stages, list) or len(set(stages)) != len(stages)
                    or any(stage not in ("input", "output") for stage in stages)
                    or not isinstance(actions, list) or len(set(actions)) != len(actions)
                    or any(action not in ("block", "flag", "allow", "redact") for action in actions)):
                raise ValueError("Invalid published check eligibility")
            if domain_id is not None and category_id != domain_id:
                continue
            items.append({
                "check_id": check["control_id"],
                "name": str(check.get("name") or ""),
                "severity": str(check.get("severity") or "unknown"),
                "stages": stages,
                "supported_actions": actions,
                "use_case": str(check.get("use_case") or ""),
                "limitations": str(check.get("limitations") or ""),
                "test_evidence": {**counts,
                    "ci_verified_for_source": False, "live_deployment_verified": None,
                    "basis": "registry declarations, not executed test results"},
                "rule_count": len(rules),
                "rules": [{"rule_id": str(rule["rule_id"]),
                           "title": str(rule.get("title") or ""),
                           "use_case": str(rule.get("use_case") or "")}
                          for rule in rules],
            })
        if domain_id is not None and category_id != domain_id:
            continue
        domains.append({
            "category_id": category_id,
            "name": str(domain.get("name") or ""),
            "status": str(domain.get("status") or "unknown"),
            "checks": items,
        })
    return {
        "registry_version": str(payload.get("registry_version") or "unknown"),
        "source": "published GuardOath control registry",
        "read_only": True,
        "categories": domains,
        "total_categories": len(domains),
        "total_checks": sum(len(item["checks"]) for item in domains),
        "total_rules": sum(check["rule_count"] for item in domains for check in item["checks"]),
    }
