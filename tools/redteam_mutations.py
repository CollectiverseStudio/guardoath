"""Bounded, deterministic red-team mutation evaluation without external AI calls.

Mutation results are measurements, not an assertion of protection. The generated
cases are local test fixtures and are never submitted to a third-party service.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from guardoath import prompt_attack
from tools.redteam_scenarios import INDEPENDENT_FIXTURES

# Keep transformations explicit and auditable; avoid random generated content.
# Transformations may change the meaning of some inputs, so report per transform.
TRANSFORMS = {
    "case_swap": lambda s: s.swapcase(),
    "space_double": lambda s: s.replace(" ", "  "),
    "newline_separator": lambda s: s.replace(" ", "\n"),
    "zero_width_after_first": lambda s: s[:1] + "\u200b" + s[1:],
}
MAX_CASES = 500
MAX_TEXT_CHARS = prompt_attack.MAX_CHARS


def run_mutations(path: Path = INDEPENDENT_FIXTURES, max_cases: int = MAX_CASES) -> dict:
    if not 1 <= max_cases <= MAX_CASES:
        raise ValueError("max_cases must be between 1 and 500")
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1 or not isinstance(document.get("cases"), list):
        raise ValueError("Invalid independent fixture manifest")
    counts = {"tp": 0, "tn": 0, "fp": 0, "fn": 0}
    by_transform = {}
    misses = []
    count = 0
    for case in document["cases"]:
        if case.get("label") not in ("adversarial", "benign"):
            raise ValueError("Invalid case label")
        original = case.get("text")
        if not isinstance(original, str) or not original:
            raise ValueError("Invalid case text")
        for name, transform in TRANSFORMS.items():
            if count >= max_cases:
                break
            mutated = transform(original)
            if len(mutated) > MAX_TEXT_CHARS:
                continue
            expected = case["label"] == "adversarial"
            detected = bool(prompt_attack.inspect(mutated))
            outcome = ("tp" if expected else "fp") if detected else ("fn" if expected else "tn")
            counts[outcome] += 1
            by_transform.setdefault(name, {"tp": 0, "tn": 0, "fp": 0, "fn": 0})[outcome] += 1
            if detected != expected:
                misses.append({"scenario_id": case["id"], "transform": name, "outcome": outcome})
            count += 1
        if count >= max_cases:
            break
    positives = counts["tp"] + counts["fn"]
    negatives = counts["tn"] + counts["fp"]
    return {
        "suite": "deterministic_mutation_v1",
        "scenario_count": count,
        "counts": counts,
        "by_transform": by_transform,
        "recall": counts["tp"] / positives if positives else None,
        "false_positive_rate": counts["fp"] / negatives if negatives else None,
        "misses": misses,
        "provider_calls": 0,
        "limitations": [
            "Mutations are mechanical; they may change meaning or tokenization",
            "Lexical detector measurement only; no semantic or tenant enforcement guarantee",
            "Small, curated corpus; percentages are not population effectiveness estimates",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-cases", type=int, default=MAX_CASES)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_mutations(max_cases=args.max_cases)
    serialized = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
