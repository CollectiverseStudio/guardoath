"""Strict offline audit of generated examples against the entire active portfolio.

Exit 1 means acceptance defects were observed; it is not converted into a pass.
Reports contain synthetic IDs/statuses only, never generated document contents.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import CheckRequest, FieldComparison, Policy, Rule, Stage
from guardoath.record_rules import record_rules

ROOT = Path(__file__).resolve().parents[1]


def scenarios():
    inventory = json.loads((ROOT / "docs/field-comparisons-rule-inventory.json").read_text(encoding="utf-8"))
    yield "MFC-018-inventory", tuple(FieldComparison(**r["comparison"]) for r in inventory["rules"])
    for name, relations in (
        ("MFC-001-chain-refund", (("shipped_qty", "ordered_qty", "le"),
                                  ("delivered_qty", "shipped_qty", "le"),
                                  ("refund_amount", "purchase_amount", "le"))),
        ("MFC-002-chain", (("requested", "stock", "le"), ("stock", "warehouse", "le"))),
        ("MFC-007-equality-cycle", (("a", "b", "le"), ("b", "c", "le"), ("c", "a", "le"))),
        ("MFC-007-strict-contradiction", (("a", "b", "lt"), ("b", "a", "lt"))),
        ("MFC-009-self-strict", (("a", "a", "le"), ("a", "a", "lt"))),
    ):
        yield name, tuple(FieldComparison(rule_id=f"audit_{i}", left=f"/{left}",
                                          right=f"/{right}", operator=op)
                          for i, (left, right, op) in enumerate(relations))


async def audit_example(engine, policy, rule_id, text, kind, stage, unavailable=None):
    # The generator gives a stable reason code with None (record_rules *_unavailable, 2026-11-05). It is reported, never
    # accepted: this audit does not prove infeasibility itself.
    if text is None:
        return {"kind": kind, "stage": stage.value, "target_rule_id": rule_id,
                "accepted": False, "status": "unavailable",
                "reason": ("generator_unavailable:" + unavailable["code"]) if unavailable
                else "generator_returned_none_without_reason"}
    request = CheckRequest(application_id=policy.application_id, policy_version=policy.version,
                           content=text, content_format="json")
    result = await engine.check(request, stage)
    findings = [f.rule_id for f in result.findings]
    checks = [{"checker_id": c.checker_id, "status": c.status, "error_code": c.error_code}
              for c in result.checker_results]
    complete = result.evaluation_status == "complete"
    if kind == "positive":
        accepted = complete and rule_id in findings
    else:
        accepted = (complete and not findings and result.recommended_action == "allow"
                    and result.effective_action == "allow"
                    and len(checks) == 1 and checks[0]["status"] == "passed")
    return {"kind": kind, "stage": stage.value, "target_rule_id": rule_id,
            "accepted": accepted, "status": "evaluated", "finding_ids": findings,
            "evaluation_status": str(result.evaluation_status), "checker_results": checks,
            "recommended_action": result.recommended_action.value,
            "effective_action": result.effective_action.value}


async def audit_portfolio(name, comparisons):
    # Real validated Policy and enforce-mode Engine. No schema-limit bypass.
    policy = Policy(application_id="portfolio-audit", version="v1", mode="enforce",
                    rules=(Rule(checker_id="cross_field", required=True, on_finding="block"),),
                    comparisons=comparisons)
    engine = Engine([policy], default_checkers())
    generated = record_rules(SimpleNamespace(comparisons=comparisons, required_sections=()))
    cases = []
    for rule_id, row in generated.items():
        for stage in (Stage.INPUT, Stage.OUTPUT):
            for kind, field in (("positive", "example"), ("benign", "look_alike")):
                cases.append(await audit_example(engine, policy, rule_id, row[field], kind, stage,
                                                 row.get(field + "_unavailable")))
    return {"scenario": name, "comparison_count": len(comparisons),
            "case_count": len(cases), "accepted_cases": sum(c["accepted"] for c in cases),
            "cases": cases}


async def run_audit():
    rows = [await audit_portfolio(name, comparisons) for name, comparisons in scenarios()]
    return {"schema_version": 1, "release_readiness": False,
            "scope": "offline generated examples, real enforce-mode Engine, both stages",
            "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                              for name in ("guardoath/record_rules.py", "guardoath/business.py",
                                           "guardoath/models.py", "guardoath/engine.py",
                                           "docs/field-comparisons-rule-inventory.json",
                                           "tools/field_comparison_portfolio.py")},
            "acceptance_passed": all(c["accepted"] for r in rows for c in r["cases"]),
            "notes": ["Genuine overlaps retained; absence of only the target finding is not benignity.",
                      "No feasibility solver; a contradictory portfolio must not yield a fake benign sample.",
                      "None without an explicit generator reason is reported as an acceptance gap.",
                      "No generated documents or submitted values are included in this report."],
            "scenarios": rows}


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
    return 0 if report["acceptance_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
