"""Conservative Sigma rule import contract.

Preserves original metadata; unsupported rules are rejected rather than treated
as compatible. Rule files are provided by the caller, never downloaded at runtime.
"""
from __future__ import annotations
from dataclasses import dataclass
from collections.abc import Mapping
from copy import deepcopy
import re
from .matcher import match_detection, UnsupportedSigmaRule

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

def import_rule(data: Mapping[str, object], source_path: str, *,
                source_revision: str | None = None) -> ImportedRule:
    if not isinstance(data, Mapping):
        raise UnsupportedSigmaRule("rule must be a mapping")
    if (not isinstance(source_path, str) or
            not re.fullmatch(r"rules/(?:[A-Za-z0-9_-]+/)+[A-Za-z0-9_-]+\.yml", source_path)):
        raise UnsupportedSigmaRule("invalid source path")
    if source_revision is not None and (not isinstance(source_revision, str) or
                                       not re.fullmatch(r"[0-9a-f]{40}", source_revision)):
        raise UnsupportedSigmaRule("invalid source revision")
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
    # Temporal/correlation rules and rule-level field expansion are not supported.
    if any(key in data for key in ("correlation", "scope", "fields_mapping")):
        raise UnsupportedSigmaRule("unsupported rule extension")
    declared_license = data.get("license", "DRL-1.1")
    if declared_license not in ("DRL-1.1", LICENSE):
        raise UnsupportedSigmaRule("unsupported rule license")
    # Validate condition support with an empty event; semantic fidelity still
    # requires per-rule positive and negative fixtures.
    match_detection(detection, {})
    return ImportedRule(
        rule_id=data["id"], title=data["title"], author=data["author"],
        source_url=(SOURCE_PREFIX if source_revision is None else
                    f"https://github.com/SigmaHQ/sigma/blob/{source_revision}/") + source_path,
        license=LICENSE, detection=deepcopy(detection),
    )
