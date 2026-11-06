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


# This verifier deliberately does NOT call the generator's _solve/_levels helpers.
# For the small unsatisfiable portfolios it independently proves impossibility via
# negative-cycle detection in difference constraints. Unknown is never accepted.
_NEGATED = {"lt": "ge", "le": "gt", "eq": "ne", "ge": "lt", "gt": "le", "ne": "eq"}
_PROOF_BRANCH_LIMIT = 128


def _constraint_feasibility(relations):
    """True=feasible, False=proved impossible, None=unknown/bounded search.

    Relations have no anchored constants: the relative ordering problem is
    equivalent to integer difference constraints (strict < is <= -1), for
    number and date comparisons. Branch for !=; cap search, never guess.
    """
    nodes = {path for left, right, _ in relations for path in (left, right)}
    if len(nodes) > 256 or len(relations) > 256:
        return None
    budget = [_PROOF_BRANCH_LIMIT]

    def solve(rows):
        budget[0] -= 1
        if budget[0] < 0:
            return None
        for index, (left, right, op) in enumerate(rows):
            if op == "ne":
                a = solve(rows[:index] + [(left, right, "lt")] + rows[index + 1:])
                if a is True:
                    return True
                b = solve(rows[:index] + [(left, right, "gt")] + rows[index + 1:])
                if b is True:
                    return True
                return False if a is False and b is False else None
        edges = []
        for left, right, op in rows:
            if op == "le":
                edges.append((right, left, 0))
            elif op == "lt":
                edges.append((right, left, -1))
            elif op == "ge":
                edges.append((left, right, 0))
            elif op == "gt":
                edges.append((left, right, -1))
            elif op == "eq":
                edges.extend(((right, left, 0), (left, right, 0)))
            else:
                return None
        dist = dict.fromkeys(nodes, 0)
        for _ in nodes:
            changed = False
            for start, end, weight in edges:
                if dist[end] > dist[start] + weight:
                    dist[end] = dist[start] + weight
                    changed = True
            if not changed:
                return True
        return False  # a relaxation on the |V|-th pass proves a negative cycle

    return solve(list(relations))


def _infeasibility_proved(comparisons, rule_id, kind, unavailable):
    """Only prove a generator's precise impossible-portfolio reason.

    Do not turn search limits, schema/type conflicts or unexplained None into
    passing cases. The real Engine still validates every available example.
    """
    expected = "cannot_all_pass" if kind == "benign" else "cannot_fail_alone"
    if not isinstance(unavailable, dict) or unavailable.get("code") != expected:
        return False
    local_id = rule_id.removeprefix("business.")
    target = next((c for c in comparisons if c.rule_id == local_id), None)
    if target is None:
        return False
    active = [c for c in comparisons if set(c.stages) & set(target.stages)]
    types = {}
    for c in active:
        for pointer in (c.left, c.right):
            if pointer in types and types[pointer] != c.value_type:
                return False
            types[pointer] = c.value_type
    rows = []
    for c in active:
        op = _NEGATED[c.operator] if c is target and kind == "positive" else c.operator
        rows.append((c.left, c.right, op))
    return _constraint_feasibility(rows) is False


async def audit_example(engine, policy, rule_id, text, kind, stage, unavailable=None, comparisons=()):
    # A generator's None is acceptable ONLY when a separate, bounded proof
    # establishes that the requested witness cannot exist. Preserve the absence:
    # it must never be mislabeled as an evaluated benign JSON document.
    if text is None:
        proven = _infeasibility_proved(comparisons, rule_id, kind, unavailable)
        return {"kind": kind, "stage": stage.value, "target_rule_id": rule_id,
                "accepted": proven, "status": "unavailable",
                "proof_status": "independently_proved_impossible" if proven else "unverified",
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
                                                 row.get(field + "_unavailable"), comparisons))
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
                      "Independently proved impossible witnesses may be unavailable; no false clean JSON is manufactured.",
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
