"""Conservative Sigma rule import contract.

Preserves original metadata; unsupported rules are rejected rather than treated
as compatible. Rule files are provided by the caller, never downloaded at runtime.
"""
from __future__ import annotations
from dataclasses import dataclass
from collections.abc import Mapping
from .matcher import match_detection, match_selection, UnsupportedSigmaRule

LICENSE = "Detection Rule License 1.1 (DRL 1.1)"
SOURCE_PREFIX = "https://github.com/SigmaHQ/sigma/blob/master/"

@dataclass(frozen=True)
class ImportedRule:
    rule_id: str
    title: str
    author: str
    source_url: str
    license: str
    detection: Mapping[str, object]

    def matches(self, event: Mapping[str, object]) -> bool:
        return match_detection(self.detection, event)

    def finding(self) -> dict[str, str]:
        return {
            "rule_id": self.rule_id, "title": self.title,
            "author": self.author, "source_url": self.source_url,
            "license": self.license,
        }

def import_rule(data: Mapping[str, object], source_path: str) -> ImportedRule:
    if not isinstance(data, Mapping):
        raise UnsupportedSigmaRule("rule must be a mapping")
    if not source_path.startswith("rules/") or not source_path.endswith(".yml") or ".." in source_path:
        raise UnsupportedSigmaRule("invalid source path")
    source = data.get("logsource")
    if not isinstance(source, Mapping):
        raise UnsupportedSigmaRule("missing logsource")
    supported = (source.get("category") == "process_creation" and
                 source.get("product") == "windows") or (
                 source.get("category") == "ps_script" and source.get("product") == "windows")
    if not supported:
        raise UnsupportedSigmaRule("unsupported log source")
    detection = data.get("detection")
    if not isinstance(detection, Mapping):
        raise UnsupportedSigmaRule("missing detection")
    for field in ("id", "title", "author"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise UnsupportedSigmaRule("missing provenance")
    # Validate every selection, including branches that might otherwise be
    # skipped by a condition. Unsupported modifiers must fail at import time.
    for key, selection in detection.items():
        if key == "condition" or key == "timeframe":
            continue
        if not isinstance(key, str) or not key:
            raise UnsupportedSigmaRule("invalid selection name")
        match_selection(selection, {})
    # Validate condition support with an empty event; semantic fidelity still
    # requires per-rule positive and negative fixtures.
    match_detection(detection, {})
    return ImportedRule(
        rule_id=data["id"], title=data["title"], author=data["author"],
        source_url=SOURCE_PREFIX + source_path, license=LICENSE, detection=detection,
    )
