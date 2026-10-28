"""Presence of configured labeled text sections; no semantic assessment."""

import re

from valinator.models import CheckerResult, Finding

# Explicit line labels only. Every label is a boundary, including unknown labels.
LABEL = re.compile(r"^([A-Za-z][A-Za-z0-9 _-]{0,79}):[ \t]*(.*)$")


async def completeness_check(request, policy, stage):
    rules = [rule for rule in policy.required_sections if stage in rule.stages]
    if not rules or request.content_format != "text":
        return CheckerResult(checker_id="completeness", checker_version="1", status="skipped")
    sections = {}
    current = None
    for line in request.content.splitlines():
        match = LABEL.fullmatch(line)
        if match:
            current = match[1].strip().lower()
            sections.setdefault(current, []).append([match[2]])
        elif current is not None:
            sections[current][-1].append(line)
    findings = []
    for rule in rules:
        bodies = sections.get(rule.label.lower(), [])
        # Duplicate sections are ambiguous; refuse to select a convenient instance.
        if len(bodies) != 1 or sum(not c.isspace() for c in "\n".join(bodies[0])) < rule.min_characters:
            findings.append(Finding(rule_id=f"completeness.{rule.rule_id}", category="business",
                severity="medium", explanation=f"Required section '{rule.rule_id}' is missing, repeated, or too short. Presence checks do not assess meaning or accuracy."))
    return CheckerResult(checker_id="completeness", checker_version="1",
        status="flagged" if findings else "passed", findings=tuple(findings))
