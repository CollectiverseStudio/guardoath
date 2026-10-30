"""Convert local eval reports to labeled observations without guessing attack labels.

The caller supplies independently reviewed labels. A matching regression
assertion is not a security-detection true positive.
"""
from collections.abc import Mapping
from guardoath.effectiveness import Observation


def observations_from_report(report: dict, labels: Mapping[str, bool]) -> tuple[Observation, ...]:
    if report.get("execution_scope") != "local_engine" or report.get("format_version") != "1":
        raise ValueError("Unsupported evaluation report")
    rows = report.get("cases")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Evaluation report has no cases")
    if not isinstance(labels, Mapping):
        raise ValueError("Reviewed attack labels required")
    ids = [r.get("case_id") for r in rows if isinstance(r, dict)]
    if len(ids) != len(rows) or any(not isinstance(i, str) or not i for i in ids):
        raise ValueError("Invalid case identifiers")
    if len(set(ids)) != len(ids) or set(ids) != set(labels):
        raise ValueError("Missing, duplicate or extra labels")
    observations = []
    for row in rows:
        case_id = row["case_id"]
        if type(labels[case_id]) is not bool:
            raise ValueError("Attack labels must be booleans")
        if row.get("matched") is not True:
            raise ValueError("Mismatched regression result cannot be scored")
        if row.get("evaluation_status") != "complete" or row.get("simulated") is not False:
            raise ValueError("Incomplete or simulated result cannot be scored")
        statuses = row.get("checker_statuses")
        if not isinstance(statuses, dict) or not statuses or any(
                status not in ("passed", "flagged") for status in statuses.values()):
            raise ValueError("Required checker outcomes are missing or incomplete")
        findings = row.get("finding_rule_ids")
        if not isinstance(findings, list) or any(not isinstance(i, str) for i in findings):
            raise ValueError("Invalid finding IDs")
        observations.append(Observation(
            case_id=case_id, attack=labels[case_id],
            flagged=bool(findings) or "flagged" in statuses.values(),
            latency_ms=row.get("latency_ms"),
        ))
    return tuple(observations)
