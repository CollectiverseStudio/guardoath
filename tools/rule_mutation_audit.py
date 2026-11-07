"""Deterministic adversarial drift audit across EVERY static tenant rule.

Never treats an exploratory textual mutation as a guaranteed synonym. The
original rule examples are fixed baseline contracts. Mutations are diagnostic:
false negatives and false positives are recorded independently, with no
raw inputs, findings, credentials or redactions in reports.

Usage: python -m tools.rule_mutation_audit --variants 8 --output .guardoath-reports/rule-drift.json
Increase --variants up to 128 for 134,400 positive/benign cases across 525 rules.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

from guardoath.checkers import default_checkers
from guardoath.models import Action, CheckRequest, Policy, Rule, Stage
from guardoath.rule_catalog import RULES
from guardoath.tenant_record import demo_defaults

DEFAULT_VARIANTS = 8
MAX_VARIANTS = 128
# Mutation names identify which transformations introduced a detector gap.
WRAPPERS = (
    ("leading_space", " ", ""),
    ("trailing_space", "", " "),
    ("newlines", "\n", "\n"),
    ("tabs", "\t", "\t"),
    ("quoted", "\"", "\""),
    ("paragraph", "Context:\n", "\nEnd."),
    ("parenthetical", "(", ")"),
    ("message_boundary", "\nUser message:\n", "\n"),
)
JSON_FORMATS = (
    ("indent2", {"indent": 2, "ensure_ascii": False}),
    ("indent4", {"indent": 4, "ensure_ascii": False}),
    ("ascii", {"ensure_ascii": True}),
    ("compact", {"separators": (",", ":"), "ensure_ascii": False}),
)


def mutation(content: str, content_format: str, index: int) -> tuple[str, str]:
    """Produce a bounded, reproducible variant; no AI-generated risky payloads."""
    if content_format == "json":
        payload = json.loads(content)
        name, options = JSON_FORMATS[index % len(JSON_FORMATS)]
        # None of the reserializations modifies parsed JSON values.
        return name, json.dumps(payload, **options)
    name, prefix, suffix = WRAPPERS[index % len(WRAPPERS)]
    # Whitespace/quoting/framing can change matching of anchors or context.
    # Therefore these are *exploratory*, not guaranteed semantic equivalents.
    if index >= len(WRAPPERS):
        # Deterministic repeated local framing is not claimed to be natural
        # language semantic paraphrase.
        repeat = 1 + (index // len(WRAPPERS))
        prefix = ("Note. " * repeat) + prefix
        name += "_frame_" + str(repeat)
    return name, prefix + content + suffix


def summary_template():
    return {"total": 0, "detected": 0, "missed": 0, "incomplete": 0,
            "false_positive": 0, "clean": 0}


async def audit(*, variants=DEFAULT_VARIANTS, limit=None, both_stages=True):
    if type(variants) is not int or not 1 <= variants <= MAX_VARIANTS:
        raise ValueError("variants must be between 1 and 128")
    if limit is not None and (type(limit) is not int or not 1 <= limit <= len(RULES)):
        raise ValueError("limit must be 1..number of static catalog rules")
    catalog = tuple(sorted(RULES.items()))[:limit]
    defaults = demo_defaults()
    checkers = default_checkers(enable_pii=True)
    by_control = defaultdict(summary_template)
    misses = []
    unexpected = []
    stages = (Stage.INPUT, Stage.OUTPUT) if both_stages else (Stage.INPUT,)
    for rule_id, rule in catalog:
        settings = {rule.needs_setting: True} if rule.needs_setting else {}
        settings.update({name: defaults[name] for name in rule.settings})
        check_ids = tuple(dict.fromkeys((rule.control_id, *rule.needs_checks)))
        policy = Policy(application_id="mutation-audit", version="v1",
                        timeout_ms=2000,
                        rules=tuple(Rule(checker_id=check_id, on_finding=Action.FLAG)
                                    for check_id in check_ids), **settings)
        for stage in stages:
            for expected, example in ((True, rule.example), (False, rule.look_alike)):
                for index in range(variants):
                    name, altered = mutation(example, rule.content_format, index)
                    request = CheckRequest(application_id="mutation-audit",
                                           policy_version="v1",
                                           content=altered, content_format=rule.content_format)
                    # No tenant default bypass: invoke the actual registered
                    # checker(s) with the exact policy settings for this rule.
                    # All processing remains local.
                    # A broken/timeout checker is recorded as incomplete,
                    # rather than aborting the entire 525-rule audit. Record
                    # exception *types* only; messages may contain raw input.
                    results = await asyncio.gather(*(
                        asyncio.wait_for(checkers[c](request, policy, stage),
                                         timeout=policy.timeout_ms / 1000)
                        for c in check_ids
                    ), return_exceptions=True)
                    failures = [type(result).__name__ for result in results
                                if isinstance(result, BaseException)]
                    successful = [result for result in results
                                  if not isinstance(result, BaseException)]
                    incomplete = bool(failures) or any(
                        result.status not in ("passed", "flagged")
                        for result in successful)
                    detected = rule_id in {
                        finding.rule_id for result in successful
                        for finding in result.findings
                    }
                    stats = by_control[rule.control_id]
                    stats["total"] += 1
                    if incomplete:
                        stats["incomplete"] += 1
                    if expected:
                        if detected and not incomplete:
                            stats["detected"] += 1
                        else:
                            stats["missed"] += 1
                            misses.append({"rule_id": rule_id, "stage": stage.value,
                                           "variant": name, "incomplete": incomplete,
                                           "error_types": failures})
                    elif detected:
                        stats["false_positive"] += 1
                        unexpected.append({"rule_id": rule_id, "stage": stage.value,
                                           "variant": name})
                    else:
                        stats["clean"] += 1
    totals = {key: sum(stats[key] for stats in by_control.values())
              for key in summary_template()}
    return {
        "source": "real_registered_checkers_and_catalog",
        "catalog_rules": len(catalog),
        "variants_per_positive_and_benign": variants,
        "stages": [stage.value for stage in stages],
        "expected_evaluations": len(catalog) * variants * 2 * len(stages),
        "totals": totals,
        "controls": dict(sorted(by_control.items())),
        # Include ONLY rule IDs, stage, mutation family and boolean; never
        # customer text or the potentially sensitive synthetic examples.
        "misses": misses, "false_positives": unexpected,
        "limitations": (
            "Exploratory formatting is NOT guaranteed meaning-preserving.",
            "No external semantic intent model is called or certified.",
            "Tenant-specific dynamically configured rules are not static catalog entries.",
            "No verdict of passed is proof that a message is safe.",
            "True semantic paraphrase coverage needs separately labeled human-reviewed corpora.",
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variants", type=int, default=DEFAULT_VARIANTS)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--input-only", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(audit(variants=args.variants, limit=args.limit,
                                   both_stages=not args.input_only))
    except Exception:
        # Never expose raw content or sensitive exception messages.
        print("Rule mutation audit failed; inspect local environment and tests.", file=sys.stderr)
        return 2
    encoded = json.dumps(report, sort_keys=True, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    # Summary only on stdout; per-rule metadata remains in the private report.
    print(json.dumps({"catalog_rules": report["catalog_rules"],
                      "evaluations": report["totals"]["total"],
                      "missed": report["totals"]["missed"],
                      "false_positive": report["totals"]["false_positive"],
                      "incomplete": report["totals"]["incomplete"],
                      "by_control": report["controls"]}, sort_keys=True))
    # An audit *reports* failures; gate only incomplete or missing coverage.
    # Misses and false positives intentionally remain measurable, not hidden.
    return 0 if report["totals"]["total"] == report["expected_evaluations"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
