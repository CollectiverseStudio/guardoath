"""Shared coverage catalogue. Planned groups never appear as passed."""

from guardoath.models import GroupResult

GROUPS = (
    ("sensitive_data", "Regulated and sensitive data", ("pii", "encoded_pii"), "Optional encoded_pii checks one labeled Base64/hex layer with bounded scanning; no recursive/unlabeled/file coverage or encoded redaction. Email, credit-card and selected U.S. SSN candidates, plus on-by-default privacy pattern checks for 90 identifier types (bank, tax, health, identity-document, network, phone and postal identifiers for the U.S. and 17 other countries); weak patterns count only with nearby context words. Opt-in explicitly labeled phone, birth date, medical-record, health-plan and bank-account fields. Redaction is policy-controlled. No names or locations; not comprehensive PHI detection, compliance assessment, address recognition or identity verification."),
    ("secrets", "Secrets and confidential information", ("secrets",), "Selected provider credentials, JWTs, private-key markers and embedded URL credentials. Optional quoted assignments, labeled encoding inspection and configured confidential terms. No credential verification, secret redaction, general password detection or proprietary-document classification."),
    ("prompt_security", "Prompt injection and manipulation", ("text_security", "prompt_attack"), "text_security checks hidden characters and encodings; prompt_attack checks the wording of jailbreaks, instruction overrides, prompt extraction and injected instructions on input and output. Both run in GuardOath; wording checks miss reworded or multi-turn attacks and are not a malicious-intent guarantee. Content is not sanitized."),
    ("harmful_content", "Harmful content and abuse", ("safety", "harmful_content", "human_safety"), "harmful_content checks the wording of hate, harassment, threats, sexual content involving minors, violent extremism and requests for violent or sexual content; human_safety checks risks to people (self-harm, emergencies, medicines, children, physical hazards, abuse, scams). Both run in GuardOath and match wording, not intent or meaning: reworded content passes and quoted or educational text can be reported. Not a clinical, crisis or legal assessment. The safety checker remains a synthetic test double."),
    ("structure", "Structure and data validity", ("schema", "timestamps", "graph", "integrity", "csv_formula"), "Optional CSV spreadsheet-formula signals alongside timestamp order, directed-graph structure and trusted SHA-256 checks have their own bounded data contracts; none proves freshness, legality, source authenticity or safe execution. Configured JSON Schema and supported formats only; no guarantee of factual correctness."),
    ("scope", "Scope and intent", (), "Task scope and relevance are not assessed."),
    ("accuracy", "Accuracy and supporting evidence", ("evidence",), "Local citation markers and quotations are checked against a configured source catalog only. No factual truth, source authenticity/freshness, entailment, claim coverage, live URL/reference verification or general grounding evaluator."),
    ("bias", "Bias and fairness", ("bias", "matched_outcomes"), "Local matched_outcomes compares submitted structured decisions with identical declared factors and different configured attributes. No free-text bias classifier, causal/statistical fairness guarantee or truth verification. The bias checker remains a synthetic test double."),
    ("business", "Business rules and commitments", ("cross_field", "completeness", "ledger"), "Optional exact ledger minor-unit sums do not establish accounting correctness or fraud clearance. Configured numeric/date comparisons and labeled text section presence only. No assessment of truth, legal adequacy, medical sufficiency, or meaning."),
    ("actions", "Code and action safety", ("tool_calls", "lexical_guards"), "lexical_guards checks the wording of risky instructions and destructive commands (wording, not intent). Local proposed tool-name allowlist and per-tool argument schemas only. No execution, authorization, side-effect validation, sandbox, code/malware/URL safety assessment or guarantee that an allowed action is appropriate."),
)


def group_results(policy, results, stage):
    indexed = {result.checker_id: result for result in results}
    groups = []
    for group_id, name, checker_ids, limitations in GROUPS:
        configured = [r for r in policy.rules if r.checker_id in checker_ids]
        stage_rules = [r for r in configured if stage in r.stages]
        # Unselected optional checks have no result; retain stage semantics.
        active = [r for r in stage_rules if r.checker_id in indexed]
        checks = [indexed[r.checker_id] for r in active]
        if not checker_ids:
            status = "not_implemented"
        elif not configured:
            status = "disabled"
        elif not stage_rules:
            status = "not_applicable"
        elif not active:
            status = "disabled"
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
            if policy.privacy_patterns:
                from guardoath.privacy_catalog import PATTERN_ENTITIES
                count = len(PATTERN_ENTITIES) - len(policy.disabled_privacy_entities)
                limitations += f" Privacy pattern checks: enabled ({count} types, minimum score {policy.privacy_min_score:g})."
            else:
                limitations += " Privacy pattern checks: disabled."
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
