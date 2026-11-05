"""Read-only hosted preview of Codex's pinned Sigma event evidence.

Only server-owned synthetic fixtures are evaluated. This module does not
register Sigma as an AI checker, activate tenant rules, accept event payloads,
or authorize real customer Windows-event ingestion.
"""
from functools import lru_cache

from guardoath.sigma_rules.evidence_catalog import load_evidence_catalog
from guardoath.sigma_rules.routing import load_routing_table

PREVIEW_LIMITATIONS = (
    "Synthetic event demonstrations only. One source-identified rule is evaluated "
    "at a time against its bundled fixture. Other rules can also match the same "
    "event. No tenant rule is activated; this is not AI prompt safety, production "
    "event monitoring, or a guarantee of malicious activity."
)


@lru_cache(maxsize=1)
def _catalog():
    evidence = load_evidence_catalog()  # Verifies pinned shipped snapshots.
    return {row.rule_id: row for row in evidence}


@lru_cache(maxsize=1)
def _routing():
    return load_routing_table()  # Re-verifies shipped sources and routing.


def catalog_payload():
    """Public metadata only; synthetic events and source detections stay server-side."""
    rules = []
    for item in _catalog().values():
        source = dict(item.logsource)
        rules.append({
            "rule_id": item.rule_id,
            "title": item.title,
            "author": item.author,
            "source_url": item.source_url,
            "license": item.license,
            "product": source["product"],
            "category": source["category"],
            "use_case": item.use_case,
            "false_positives": list(item.upstream_false_positives or ()),
            "maturity": item.maturity,
        })
    return {
        "domain": "security_events",
        "mode": "synthetic_preview",
        "tenant_enforcement": False,
        "runtime_registered_as_ai_checker": False,
        "total_rules": len(rules),
        "limitations": PREVIEW_LIMITATIONS,
        "rules": rules,
    }


def evaluate_fixture(rule_id: str, sample: str):
    """Evaluate ONLY a pinned, server-owned fixture and one trusted source rule."""
    if sample not in ("positive", "lookalike"):
        raise ValueError("Unsupported synthetic sample")
    item = _catalog().get(rule_id)
    if item is None:
        raise KeyError("Unknown rule")
    # Neither logsource nor the allowed rule ID comes from a caller-provided event.
    table = _routing()
    source = table.source_for(rule_id)
    logsource = {"product": source["product"], "category": source["category"]}
    event = dict(item.positive_event if sample == "positive" else item.lookalike_event)
    matches = table.evaluate(
        event,
        trusted_logsource=logsource,
        allowed_rule_ids=(rule_id,),
        max_workers=1,
        timeout_seconds=1.0,
    )
    return {
        "domain": "security_events",
        "mode": "synthetic_preview",
        "rule_id": rule_id,
        "sample": sample,
        "evaluation_status": "complete",
        "matched": bool(matches),
        "findings": matches,
        "selected_rule_count": 1,
        "tenant_enforcement": False,
        "runtime_registered_as_ai_checker": False,
        "limitations": PREVIEW_LIMITATIONS,
    }
