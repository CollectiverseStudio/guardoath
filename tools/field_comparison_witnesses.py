"""Offline witness classification; no runtime generator repair.

Strict exit1 records generated-example defects, never false benign acceptance.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import FieldComparison, Policy, Rule, Stage
from guardoath.record_rules import record_rules
from tools.field_comparison_portfolio import audit_example

ROOT = Path(__file__).resolve().parents[1]


def witnesses():
    definitions = (
        ("W1-shared-pointer", "MFC-001", "shared_pointer", (
            ("shipped", "/shipped_qty", "/ordered_qty", "le"),
            ("delivered", "/delivered_qty", "/shipped_qty", "le"),
            ("refund", "/refund_amount", "/purchase_amount", "le")),
         {"shipped_qty": 8, "ordered_qty": 10, "delivered_qty": 6,
          "refund_amount": 25, "purchase_amount": 100},
         {"shipped_qty": 11, "ordered_qty": 10, "delivered_qty": 12,
          "refund_amount": 101, "purchase_amount": 100}),
        ("W2-overwrite", "MFC-006", "target_overwrite", (
            ("above_minimum", "/observed", "/minimum", "gt"),
            ("below_maximum", "/observed", "/maximum", "lt")),
         {"observed": 2, "minimum": 1, "maximum": 3},
         {"observed": 2, "minimum": 3, "maximum": 1}),
        ("W3-feasible-dag", "MFC-002", "feasible_dag", (
            ("requested", "/requested", "/stock", "le"),
            ("stock", "/stock", "/warehouse", "le")),
         {"requested": 1, "stock": 2, "warehouse": 3},
         {"requested": 3, "stock": 2, "warehouse": 1}),
        ("W4-contradiction", "MFC-007", "strict_two_cycle", (
            ("forward", "/a", "/b", "lt"), ("reverse", "/b", "/a", "lt")),
         None, {"a": 1, "b": 1}),
        ("W5-escaped-pointer", "MFC-011", "escaped_pointer", (
            ("escaped", "/a~1b", "/c~0d", "le"),
            ("ceiling", "/c~0d", "/upper", "le")),
         {"a/b": 1, "c~d": 2, "upper": 3},
         {"a/b": 3, "c~d": 2, "upper": 1}),
    )
    for name, matrix, kind, rows, benign, bad in definitions:
        comparisons = tuple(FieldComparison(rule_id=i, left=l, right=r, operator=o)
                            for i, l, r, o in rows)
        yield {"witness_id": name, "matrix_id": matrix, "kind": kind,
               "comparisons": comparisons, "manual_benign": benign, "manual_bad": bad}


def strict_cycle_proof(comparisons):
    if len(comparisons) != 2:
        raise ValueError("Proof applies only to two comparisons")
    a, b = comparisons
    if not (a.operator == b.operator == "lt" and a.value_type == b.value_type == "number"
            and a.left == b.right and a.right == b.left and a.left != a.right
            and set(a.stages) == set(b.stages) == {Stage.INPUT, Stage.OUTPUT}):
        raise ValueError("Not the stated strict numeric two-cycle")
    return "strict_numeric_two_cycle_no_joint_witness"


async def classify(witness):
    comparisons = witness["comparisons"]
    policy = Policy(application_id="witness-audit", version="v1", mode="enforce",
                    rules=(Rule(checker_id="cross_field", required=True, on_finding="block"),),
                    comparisons=comparisons)
    engine = Engine([policy], default_checkers())
    generated = record_rules(SimpleNamespace(comparisons=comparisons, required_sections=()))
    ids = ["business." + c.rule_id for c in comparisons]
    manual, violations, samples = [], [], []
    for stage in (Stage.INPUT, Stage.OUTPUT):
        if witness["manual_benign"] is not None:
            manual.append(await audit_example(engine, policy, ids[0],
                          json.dumps(witness["manual_benign"]), "benign", stage))
        row = await audit_example(engine, policy, ids[0], json.dumps(witness["manual_bad"]),
                                  "positive", stage)
        row["all_original_findings_preserved"] = row["finding_ids"] == ids
        violations.append(row)
        for rule_id, fields in generated.items():
            for kind, field in (("positive", "example"), ("benign", "look_alike")):
                samples.append(await audit_example(engine, policy, rule_id, fields[field], kind, stage,
                                                   fields.get(field + "_unavailable")))
    proof = strict_cycle_proof(comparisons) if witness["kind"] == "strict_two_cycle" else None
    feasible = bool(manual) and all(c["accepted"] for c in manual)
    generated_benign_pass = all(c["accepted"] for c in samples if c["kind"] == "benign")
    classification = ("infeasible_proven" if proof else "manual_witness_invalid" if not feasible
                      else "feasible_generated_valid" if generated_benign_pass
                      else "feasible_generator_defect")
    return {"witness_id": witness["witness_id"], "matrix_id": witness["matrix_id"],
            "classification": classification, "infeasibility_proof": proof,
            "classification_contract_passed": (bool(proof) or feasible) and all(
                c["accepted"] and c["all_original_findings_preserved"] for c in violations),
            "generated_acceptance_passed": all(c["accepted"] for c in samples),
            "manual_benign": manual, "manual_all_violations": violations,
            "generated_cases": samples}


async def run_audit():
    rows = [await classify(w) for w in witnesses()]
    return {"status": "offline-witness-classification", "schema_version": 1,
            "runtime_scope": "Source hashes identify measured code, not installed/hosted acceptance",
            "release_readiness": False,
            "classification_contract_passed": all(r["classification_contract_passed"] for r in rows),
            "generated_acceptance_passed": all(r["generated_acceptance_passed"] for r in rows),
            "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                              for name in ("guardoath/record_rules.py", "guardoath/business.py",
                                           "guardoath/engine.py", "guardoath/models.py",
                                           "tools/field_comparison_portfolio.py",
                                           "tools/field_comparison_witnesses.py")},
            "witnesses": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(run_audit())
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0 if report["classification_contract_passed"] and report["generated_acceptance_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
