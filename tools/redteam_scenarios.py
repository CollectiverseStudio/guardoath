"""Offline adversarial scenario runner using the actual GuardOath prompt-attack engine.

The baseline is deliberately lexical: these source-authored fixtures are not
evidence of semantic jailbreak robustness. No model calls, network or tenant writes.
Run: python -m tools.redteam_scenarios --output .guardoath-reports/redteam.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from guardoath import prompt_attack


def scenarios() -> tuple[dict, ...]:
    cases = []
    for rule_id, (_, severity, _, attack, benign) in sorted(prompt_attack.RULES.items()):
        for label, text, expected in (
            ("adversarial", attack, True),
            ("benign", benign, False),
        ):
            cases.append({
                "id": rule_id + "." + label,
                "rule_id": rule_id,
                "severity": severity,
                "label": label,
                "content": text,
                "expected": expected,
            })
    return tuple(cases)


def evaluate() -> dict:
    failures = []
    counts = {"tp": 0, "tn": 0, "fp": 0, "fn": 0}
    for case in scenarios():
        detected = case["rule_id"] in prompt_attack.inspect(case["content"])
        outcome = ("tp" if case["expected"] else "fp") if detected else (
            "fn" if case["expected"] else "tn")
        counts[outcome] += 1
        if detected != case["expected"]:
            # Do not leak adversarial prompt content in reports.
            failures.append({"scenario_id": case["id"], "outcome": outcome})
    positives = counts["tp"] + counts["fn"]
    negatives = counts["tn"] + counts["fp"]
    return {
        "engine": "guardoath.prompt_attack.inspect",
        "scenario_count": len(scenarios()),
        "counts": counts,
        "recall": counts["tp"] / positives if positives else None,
        "false_positive_rate": counts["fp"] / negatives if negatives else None,
        "failures": failures,
        "limitations": [
            "Source-authored lexical examples only; not independent attack coverage",
            "No semantic paraphrase, multi-turn context, tool provenance or model execution",
            "No claim of tenant policy enforcement or end-to-end dashboard verification",
        ],
        "provider_calls": 0,
    }



INDEPENDENT_FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "redteam_independent.json"


def evaluate_independent(path: Path = INDEPENDENT_FIXTURES) -> dict:
    """Measure independently authored challenges; gaps are evidence, not a CI waiver.

    A detected lexical rule is only an indicator; this does not establish
    semantic intent, safe action, or complete tenant enforcement.
    """
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1 or not isinstance(document.get("cases"), list):
        raise ValueError("Unsupported independent challenge manifest")
    counts = {"tp": 0, "tn": 0, "fp": 0, "fn": 0}
    failures = []
    family_counts = {}
    seen = set()
    for case in document["cases"]:
        case_id = case["id"]
        label = case["label"]
        family = case["family"]
        text = case["text"]
        if (not isinstance(case_id, str) or not case_id or case_id in seen
                or label not in ("adversarial", "benign")
                or not isinstance(family, str) or not family
                or not isinstance(text, str) or not text or len(text) > prompt_attack.MAX_CHARS):
            raise ValueError("Invalid or duplicate challenge scenario")
        seen.add(case_id)
        detected = bool(prompt_attack.inspect(text))
        expected = label == "adversarial"
        outcome = ("tp" if expected else "fp") if detected else ("fn" if expected else "tn")
        counts[outcome] += 1
        family_counts.setdefault(family, {"tp": 0, "tn": 0, "fp": 0, "fn": 0})[outcome] += 1
        if detected != expected:
            failures.append({"scenario_id": case_id, "family": family, "outcome": outcome})
    positives = counts["tp"] + counts["fn"]
    negatives = counts["tn"] + counts["fp"]
    return {
        "suite": "independent_challenge_v1",
        "scenario_count": len(seen),
        "counts": counts,
        "family_counts": family_counts,
        "recall": counts["tp"] / positives if positives else None,
        "false_positive_rate": counts["fp"] / negatives if negatives else None,
        "failures": failures,
        "limitations": [
            "Curated small challenge set; not a representative population estimate",
            "Lexical match only, not end-to-end tenant authorization or semantic model validation",
        ],
        "provider_calls": 0,
    }

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--independent", action="store_true", help="Use separately authored challenge cases")
    args = parser.parse_args()
    report = evaluate_independent() if args.independent else evaluate()
    encoded = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    # Independent misses are expected measurements, not runner infrastructure failures.
    return 1 if report["failures"] and not args.independent else 0


if __name__ == "__main__":
    raise SystemExit(main())
