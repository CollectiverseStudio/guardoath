"""Owner inspection of published examples and selected tenant setting schemas.

This is deliberately separate from read_catalog: its stable projection is the
fingerprint contract for existing signed drafts and source-release history.
Nothing here reads a tenant record, executes an example or changes a definition.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re

from guardoath.admin_catalog import (
    CatalogProvider, _CONTROL_ID, _RULE_ID, _VERSION, _public_text, read_catalog,
)


class CatalogInspectionNotFound(LookupError):
    """The exact category/check/rule tuple is not published."""


class CatalogInspectionConflict(RuntimeError):
    """The operator selected a different published registry version."""


_REFERENCE = re.compile(r"^#\/\$defs\/([A-Za-z][A-Za-z0-9_]{0,99})$")
_EXAMPLE_GROUPS = ("positive_examples", "look_alikes", "known_false_positives", "known_misses")
_MAX_RESPONSE_BYTES = 262_144


def _names(value, *, allowed: set[str], limit: int = 64) -> list[str]:
    if (not isinstance(value, list) or len(value) > limit or
            any(not isinstance(name, str) or name not in allowed for name in value) or
            len(set(value)) != len(value)):
        raise ValueError("Invalid published prerequisites")
    return list(value)


def _paths(value, *, prefix: str, suffix: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 200:
        raise ValueError("Invalid published source references")
    paths = []
    for path in value:
        if (not isinstance(path, str) or not 1 <= len(path) <= 255 or
                not path.startswith(prefix + "/") or not path.endswith(suffix) or
                not re.fullmatch(r"[A-Za-z0-9_./-]+", path) or
                any(part in ("", ".", "..") for part in path.split("/")) or path in paths):
            raise ValueError("Invalid published source reference")
        paths.append(path)
    return paths


def _example_text(value) -> str:
    if not isinstance(value, str) or len(value) > 16_384:
        raise ValueError("Invalid published example")
    # Invisible, directional and control characters are intentional examples
    # for some checks. Make every such code point visible, not executable HTML
    # or direction-changing text. The browser renders this string as text only.
    return json.dumps(value, ensure_ascii=True)


def _examples(value, stages: list[str]) -> list[dict]:
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError("Invalid published examples")
    examples = []
    for item in value:
        if (not isinstance(item, dict) or item.get("stage") not in stages or
                item.get("format") not in ("text", "json")):
            raise ValueError("Invalid published example eligibility")
        examples.append({
            "label": _public_text(item.get("label"), limit=255),
            "stage": item["stage"], "format": item["format"],
            "escaped_text": _example_text(item.get("text")),
        })
    return examples


def _settings_schema(check_id: str) -> dict:
    from guardoath.tenant_record import CHECK_IDS, SETTINGS_OF, TenantRecord

    if check_id not in CHECK_IDS:
        raise ValueError("Published check has no authoritative tenant schema")
    fields = [f"input_check_{check_id}", f"output_check_{check_id}",
              *SETTINGS_OF.get(check_id, ()), "input_rule_actions", "output_rule_actions"]
    source = TenantRecord.model_json_schema()
    properties = source.get("properties")
    definitions = source.get("$defs", {})
    if not isinstance(properties, dict) or not isinstance(definitions, dict):
        raise ValueError("Tenant setting schema unavailable")
    if any(name not in properties for name in fields):
        raise ValueError("Tenant setting schema is incomplete")
    selected = {name: deepcopy(properties[name]) for name in fields}
    referenced: dict = {}
    nodes = 0

    def visit(value, depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > 20_000 or depth > 40:
            raise ValueError("Oversized tenant setting schema")
        if isinstance(value, dict):
            for name, child in value.items():
                if name == "$ref":
                    match = _REFERENCE.fullmatch(child) if isinstance(child, str) else None
                    if not match or match[1] not in definitions:
                        raise ValueError("Unsupported tenant schema reference")
                    key = match[1]
                    if key not in referenced:
                        if len(referenced) >= 64:
                            raise ValueError("Oversized tenant schema definitions")
                        referenced[key] = deepcopy(definitions[key])
                        visit(referenced[key], depth + 1)
                else:
                    visit(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                visit(child, depth + 1)

    visit(selected)
    schema = {"type": "object", "additionalProperties": False, "properties": selected}
    if referenced:
        schema["$defs"] = referenced
    return {"source": "TenantRecord.model_json_schema", "field_names": fields,
            "schema": schema,
            "limitations": "Data shape only. Tenant model validators, stage eligibility, host-reference verification and saved Off settings remain authoritative."}


def inspect_catalog(provider: CatalogProvider, *, category_id: str, check_id: str,
                    expected_registry_version: str, rule_id: str | None = None) -> dict:
    """Read one source snapshot and return only bounded public declarations."""
    if (not isinstance(category_id, str) or not _CONTROL_ID.fullmatch(category_id) or
            not isinstance(check_id, str) or not _CONTROL_ID.fullmatch(check_id) or
            (rule_id is not None and (not isinstance(rule_id, str) or not _RULE_ID.fullmatch(rule_id)))):
        raise CatalogInspectionNotFound("Published catalog target not found")
    if not isinstance(expected_registry_version, str) or not _VERSION.fullmatch(expected_registry_version):
        raise ValueError("Invalid registry version")
    payload = deepcopy(provider())
    # Validate global ID uniqueness and the existing bounded inventory without
    # changing its public projection or calling a changing provider twice.
    inventory = read_catalog(lambda: payload)
    if inventory["registry_version"] != expected_registry_version:
        raise CatalogInspectionConflict("Published registry changed")
    domain = next((item for item in payload["domains"] if item["domain_id"] == category_id), None)
    control = next((item for item in domain["controls"] if item["control_id"] == check_id), None) if domain else None
    if control is None:
        raise CatalogInspectionNotFound("Published catalog target not found")
    rule = next((item for item in control["rules"] if item["rule_id"] == rule_id), None) if rule_id else None
    if rule_id is not None and rule is None:
        raise CatalogInspectionNotFound("Published catalog target not found")
    category_view = next(item for item in inventory["categories"] if item["category_id"] == category_id)
    check_view = next(item for item in category_view["checks"] if item["check_id"] == check_id)
    settings = _settings_schema(check_id)
    from guardoath.tenant_record import CHECK_IDS, SETTINGS_OF
    selected_rule = None
    if rule is not None:
        dependencies = _names(rule.get("needs_checks"), allowed=set(CHECK_IDS))
        own_settings = set(SETTINGS_OF.get(check_id, ()))
        dependency_settings = set(own_settings)
        for dependency in dependencies:
            dependency_settings.update(SETTINGS_OF.get(dependency, ()))
        needed = rule.get("needs_setting")
        if needed is not None and (not isinstance(needed, str) or needed not in dependency_settings):
            raise ValueError("Invalid published setting prerequisite")
        if rule.get("content_format") not in ("text", "json"):
            raise ValueError("Invalid published example format")
        selected_rule = {key: _public_text(rule.get(key), limit=4096 if key == "use_case" else 255)
                         for key in ("rule_id", "title", "use_case", "severity")}
        if selected_rule["severity"] not in ("low", "medium", "high"):
            raise ValueError("Invalid published rule severity")
        selected_rule.update({
            "content_format": rule["content_format"],
            "example": _example_text(rule.get("example")),
            "look_alike": _example_text(rule.get("look_alike")),
            "needs_setting": needed,
            "settings": _names(rule.get("settings"), allowed=own_settings),
            "needs_checks": dependencies,
        })
    evidence = control.get("evidence")
    detector = control.get("detector")
    if not isinstance(evidence, dict) or not isinstance(detector, dict):
        raise ValueError("Published source declarations unavailable")
    version = detector.get("version")
    if version is not None:
        version = _public_text(version, limit=64)
        if not version:
            raise ValueError("Invalid detector version")
    recommended = control.get("recommended_action")
    if recommended not in check_view["supported_actions"]:
        raise ValueError("Invalid published recommended action")
    result = {
        "status": "ok", "read_only": True,
        "source": "published GuardOath control registry",
        "registry_version": inventory["registry_version"],
        "category": {key: category_view[key] for key in ("category_id", "name", "status")},
        "check": {key: deepcopy(value) for key, value in check_view.items() if key not in ("rules", "test_evidence")},
        "rule": selected_rule,
        "examples": {group: _examples(control.get(group), check_view["stages"]) for group in _EXAMPLE_GROUPS},
        "example_representation": "escaped JSON strings from published examples, never tenant data",
        "settings": settings,
        "test_evidence": {
            "unit_tests": _paths(evidence.get("unit_tests"), prefix="tests", suffix=".py"),
            "evaluations": _paths(evidence.get("evaluations"), prefix="evals", suffix=".json"),
            "ci_verified_for_source": False, "live_deployment_verified": None,
            "basis": "registry declarations, not executed test results",
        },
        "detector": {"version": version,
                     "implementation": _paths(detector.get("implementation"), prefix="guardoath", suffix=".py")},
    }
    result["category"]["description"] = _public_text(domain.get("description"), limit=4096)
    result["check"]["outcome"] = _public_text(control.get("outcome"), limit=4096)
    result["check"]["recommended_action"] = recommended
    if len(json.dumps(result, ensure_ascii=True, allow_nan=False).encode("utf-8")) > _MAX_RESPONSE_BYTES:
        raise ValueError("Oversized published inspection")
    return result
