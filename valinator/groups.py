"""Shared coverage catalogue. Planned groups never appear as passed."""

from valinator.models import GroupResult

GROUPS = (
    ("sensitive_data", "Regulated and sensitive data", ("pii",), "Email, credit-card and selected U.S. SSN candidates. Opt-in explicitly labeled phone, birth date, medical-record, health-plan and bank-account fields. Redaction is policy-controlled. Not comprehensive PHI detection, compliance assessment, names/address recognition or identity verification."),
    ("secrets", "Secrets and confidential information", ("secrets",), "Selected provider credentials, JWTs, private-key markers and embedded URL credentials. Optional quoted assignments, labeled encoding inspection and configured confidential terms. No credential verification, secret redaction, general password detection or proprietary-document classification."),
    ("prompt_security", "Prompt injection and manipulation", ("text_security", "azure_prompt_shields"), "Local text_security checks selected controls/encodings only. Optional Azure Prompt Shields analyzes user-prompt attacks externally on input only. No document, multi-turn, output-attack, or malicious-intent guarantee; inspect selected checkers. Content is not sanitized."),
    ("harmful_content", "Harmful content and abuse", ("safety",), "Safety checks are synthetic test doubles, not content detectors."),
    ("structure", "Structure and data validity", ("schema",), "Configured JSON Schema and supported formats only; no guarantee of factual correctness."),
    ("scope", "Scope and intent", (), "Task scope and relevance are not assessed."),
    ("accuracy", "Accuracy and supporting evidence", (), "No grounding or factual accuracy evaluator is implemented."),
    ("bias", "Bias and fairness", ("bias",), "Bias checks are synthetic test doubles, not fairness assessments."),
    ("business", "Business rules and commitments", ("cross_field", "completeness"), "Configured numeric/date comparisons and labeled text section presence only. No assessment of truth, legal adequacy, medical sufficiency, or meaning."),
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
        if group_id == 'sensitive_data' and configured:
            limitations += f" Labeled sensitive-data fields: {'enabled' if policy.detect_labeled_sensitive_data else 'disabled'}."
        if group_id == 'secrets' and configured:
            count=sum(stage in r.stages for r in policy.confidential_terms)
            limitations += f" Quoted assignments: {'enabled' if policy.detect_secret_assignments else 'disabled'}; encoded inspection: {'enabled' if policy.inspect_encoded_secrets else 'disabled'}; active confidential-term rules: {count}."
        groups.append(GroupResult(group_id=group_id, name=name, checker_ids=tuple(r.checker_id for r in active),
                                  status=status, limitations=limitations))
    # Custom adapters must remain visible rather than disappear from grouped UI.
    known = {c for _, _, ids, _ in GROUPS for c in ids}
    extra = tuple(c.checker_id for c in results if c.checker_id not in known)
    if extra:
        groups.append(GroupResult(group_id="custom", name="Custom checks", status="partial",
                                  checker_ids=extra, limitations="Review the individual custom checker results."))
    return tuple(groups)
