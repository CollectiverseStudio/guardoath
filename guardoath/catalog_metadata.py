"""Source-controlled, metadata-only catalog release history.

No customer/API request can write this file. It is shipped with a reviewed source
commit. These changes may alter rule title/use-case text ONLY; the Engine, IDs,
detectors, examples, tenant actions, subscriptions and host reference authority
remain unchanged. Invalid metadata refuses startup instead of being ignored.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re
import unicodedata

BASE_REGISTRY_VERSION = "3"
FILE = Path(__file__).with_name("catalog_metadata_releases.json")
_ID = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
_RULE = re.compile(r"^[A-Za-z0-9_.:-]{3,100}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^[1-9][0-9]{0,8}$")
_FIELDS = frozenset({"title", "use_case"})


def _safe_text(text, limit: int) -> bool:
    return (isinstance(text, str) and 3 <= len(text) <= limit
            and text == text.strip()
            and not any(ord(ch) < 32 or 0x7f <= ord(ch) <= 0x9f
                        or unicodedata.category(ch) in ("Cf", "Cs", "Zl", "Zp")
                        for ch in text))


def validate_release_history(data: dict) -> dict:
    """Require exact format, bounded append-only versions and review provenance."""
    if (not isinstance(data, dict)
            or set(data) != {"base_registry_version", "releases"}
            or data["base_registry_version"] != BASE_REGISTRY_VERSION
            or not isinstance(data["releases"], list)
            or len(data["releases"]) > 100):
        raise ValueError("Invalid published catalog release history")
    version = int(BASE_REGISTRY_VERSION)
    seen_reviews, seen_drafts = set(), set()
    for entry in data["releases"]:
        if (not isinstance(entry, dict) or set(entry) != {
                "registry_version", "category_id", "check_id", "rule_id",
                "review_id", "draft_id", "baseline_sha256",
                "candidate_sha256", "changes"}
                or not isinstance(entry["registry_version"], str)
                or not _VERSION.fullmatch(entry["registry_version"])
                or int(entry["registry_version"]) != version + 1
                or not isinstance(entry["category_id"], str)
                or not _ID.fullmatch(entry["category_id"])
                or not isinstance(entry["check_id"], str)
                or not _ID.fullmatch(entry["check_id"])
                or not isinstance(entry["rule_id"], str)
                or not _RULE.fullmatch(entry["rule_id"])
                or any(not isinstance(entry[key], str)
                       or not _SHA.fullmatch(entry[key])
                       for key in ("review_id", "draft_id", "baseline_sha256", "candidate_sha256"))
                or entry["review_id"] in seen_reviews
                or entry["draft_id"] in seen_drafts
                or not isinstance(entry["changes"], dict)
                or not entry["changes"] or set(entry["changes"]) - _FIELDS):
            raise ValueError("Invalid catalog source release")
        for field, delta in entry["changes"].items():
            limit = 120 if field == "title" else 500
            if (not isinstance(delta, dict) or set(delta) != {"before", "after"}
                    or not _safe_text(delta["before"], limit)
                    or not _safe_text(delta["after"], limit)
                    or delta["before"] == delta["after"]
                    or (field == "use_case" and
                        not delta["after"].startswith("Turn this on"))):
                raise ValueError("Invalid metadata-only source change")
        seen_reviews.add(entry["review_id"])
        seen_drafts.add(entry["draft_id"])
        version += 1
    return data


def load_release_history(path: Path = FILE) -> dict:
    raw = path.read_bytes()
    if len(raw) > 262144:
        raise ValueError("Oversized catalog source history")
    data = json.loads(raw)
    return validate_release_history(data)


def published_version(data: dict) -> str:
    validate_release_history(data)
    return (data["releases"][-1]["registry_version"] if data["releases"]
            else data["base_registry_version"])


def apply_rule_releases(rules: dict, data: dict) -> dict:
    """Return immutable RuleInfo replacements, never mutate the source catalog."""
    validate_release_history(data)
    if not isinstance(rules, dict):
        raise ValueError("Invalid source rule catalog")
    updated = dict(rules)
    for entry in data["releases"]:
        rule = updated.get(entry["rule_id"])
        if (rule is None or getattr(rule, "rule_id", None) != entry["rule_id"]
                or getattr(rule, "control_id", None) != entry["check_id"]):
            raise ValueError("Catalog release rule/check identity changed")
        next_values = {}
        for field, change in entry["changes"].items():
            if getattr(rule, field, None) != change["before"]:
                raise ValueError("Catalog release baseline metadata changed")
            next_values[field] = change["after"]
        updated[entry["rule_id"]] = replace(rule, **next_values)
    return updated


RELEASE_DATA = load_release_history()
