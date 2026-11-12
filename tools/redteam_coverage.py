"""Deterministic, offline red-team coverage audit for GuardOath.

This does not execute attacks or claim that a keyword detector defeats semantic
jailbreaks. It inventories the actual prompt-attack source and test contracts.
Run: python -m tools.redteam_coverage
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from guardoath import prompt_attack
from guardoath.rule_catalog import rules_for

# Stable, intentionally limited coverage assertions: source-defined examples
# are evidence of lexical rule coverage, NOT semantic adversarial robustness.
REQUIRED_FAMILIES = {
    "role_and_instruction_override": ("dan_persona", "developer_mode", "new_instructions_marker"),
    "prompt_extraction": ("system_prompt",),
    "indirect_injection": ("document",),
    "exfiltration": ("exfil",),
    "evasion": ("decode",),
}
ADVANCED_GAPS = (
    "cross-turn semantic intent across nonadjacent messages",
    "provenance-aware tool result trust boundaries",
    "automated paraphrase and multilingual evasion scoring",
    "model training-data poisoning provenance",
    "model extraction and membership-inference evaluation",
    "campaign-scoped authorization and bounded red-team execution",
    "measured precision/recall against reviewed adversarial and benign corpora",
)


def audit() -> dict:
    defined = prompt_attack.RULES
    catalog = {r.rule_id for r in rules_for("prompt_attack")}
    source_ids = set(defined)
    malformed = sorted(
        rule_id for rule_id, fields in defined.items()
        if not rule_id.startswith("prompt_attack.")
        or len(fields) != 5
        or not all(isinstance(v, str) and v.strip() for v in fields)
    )
    coverage = {}
    for family, markers in REQUIRED_FAMILIES.items():
        matching = sorted(rule_id for rule_id in source_ids
                          if any(marker in rule_id for marker in markers))
        coverage[family] = {"lexical_rule_ids": matching, "present": bool(matching)}
    return {
        "check": "prompt_attack",
        "lexical_rules": len(defined),
        "catalog_consistent": source_ids == catalog,
        "malformed_rule_ids": malformed,
        "families": coverage,
        "unverified_advanced_capabilities": list(ADVANCED_GAPS),
        "semantic_effectiveness_verified": False,
        "external_provider_required": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional local JSON evidence file")
    args = parser.parse_args()
    result = audit()
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if result["catalog_consistent"] and not result["malformed_rule_ids"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
