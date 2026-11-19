"""Bounded read-only administrative views of GuardOath's published validation catalog.

The authoritative catalog is guardoath.control_registry.registry_payload. Admin
views are projections only; they never create or activate tenant rules.
"""
from __future__ import annotations

from typing import Callable


CatalogProvider = Callable[[], dict]


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
    for domain in payload["domains"]:
        if not isinstance(domain, dict) or not isinstance(domain.get("domain_id"), str):
            raise ValueError("Invalid published domain")
        if domain_id is not None and domain["domain_id"] != domain_id:
            continue
        controls = domain.get("controls")
        if not isinstance(controls, list):
            raise ValueError("Invalid published controls")
        items = []
        for check in controls:
            if not isinstance(check, dict) or not isinstance(check.get("control_id"), str):
                raise ValueError("Invalid published control")
            rules = check.get("rules")
            if not isinstance(rules, list):
                raise ValueError("Invalid published rules")
            items.append({
                "check_id": check["control_id"],
                "name": str(check.get("name") or ""),
                "severity": str(check.get("severity") or "unknown"),
                "stages": list(check.get("stages") or []),
                "rule_count": len(rules),
                "rules": [{"rule_id": str(rule["rule_id"]),
                           "title": str(rule.get("title") or ""),
                           "use_case": str(rule.get("use_case") or "")}
                          for rule in rules if isinstance(rule, dict) and isinstance(rule.get("rule_id"), str)],
            })
        domains.append({
            "category_id": domain["domain_id"],
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
