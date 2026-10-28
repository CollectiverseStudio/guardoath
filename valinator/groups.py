"""Shared coverage catalogue. Planned groups never appear as passed."""

from valinator.models import GroupResult

GROUPS = (
    ("sensitive_data", "Regulated and sensitive data", ("pii",), "Email, credit-card, and selected U.S. SSN candidates only. Redaction is policy-controlled. PHI, names, addresses, and other identifiers are not assessed."),
    ("secrets", "Secrets and confidential information", ("secrets",), "Selected AWS, GitHub, OpenAI, Stripe, JWT, and private-key patterns only. Credentials are not verified. Generic passwords and proprietary documents are not classified or redacted."),
    ("prompt_security", "Prompt injection and manipulation", (), "No real prompt-injection detector is implemented."),
    ("harmful_content", "Harmful content and abuse", ("safety",), "Safety checks are synthetic test doubles, not content detectors."),
    ("structure", "Structure and data validity", ("schema",), "Configured JSON Schema and supported formats only; no guarantee of factual correctness."),
    ("scope", "Scope and intent", (), "Task scope and relevance are not assessed."),
    ("accuracy", "Accuracy and supporting evidence", (), "No grounding or factual accuracy evaluator is implemented."),
    ("bias", "Bias and fairness", ("bias",), "Bias checks are synthetic test doubles, not fairness assessments."),
    ("business", "Business rules and commitments", ("cross_field",), "Configured numeric and date comparisons only. Submitted fields are not independently verified facts."),
    ("actions", "Code and action safety", (), "No code or tool-action safety checker is implemented."),
)


def group_results(policy, results, stage):
    indexed = {result.checker_id: result for result in results}
    groups = []
    for group_id, name, checker_ids, limitations in GROUPS:
        configured = [r for r in policy.rules if r.checker_id in checker_ids]
        active = [r for r in configured if stage in r.stages]
        checks = [indexed[r.checker_id] for r in active]
        if not checker_ids:
            status = "not_implemented"
        elif not configured:
            status = "disabled"
        elif not active:
            status = "not_applicable"
        elif any(r.required and c.status not in {"passed", "flagged"} for r, c in zip(active, checks)):
            status = "incomplete"
        elif any(c.status == "flagged" and not c.simulated for c in checks):
            status = "findings"
        elif any(c.simulated for c in checks):
            status = "simulated"
        elif any(c.status != "passed" for c in checks):
            status = "partial"
        else:
            status = "passed"
        groups.append(GroupResult(group_id=group_id, name=name, checker_ids=tuple(r.checker_id for r in active),
                                  status=status, limitations=limitations))
    # Custom adapters must remain visible rather than disappear from grouped UI.
    known = {c for _, _, ids, _ in GROUPS for c in ids}
    extra = tuple(c.checker_id for c in results if c.checker_id not in known)
    if extra:
        groups.append(GroupResult(group_id="custom", name="Custom checks", status="partial",
                                  checker_ids=extra, limitations="Review the individual custom checker results."))
    return tuple(groups)
