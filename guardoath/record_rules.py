"""Rules that come from a tenant's own record (2026-11-04).

Each field comparison on a record (cross_field) is one rule, ``business.<rule_id>``, and each required section
(completeness) is one rule, ``completeness.<rule_id>``. They are listed with the same fields as the static catalog
(guardoath.rule_catalog.rule_payload), so the dashboard shows them like every other rule, and a tenant can give each
its own setting in rule_actions.

Tenant isolation: these rules exist only on the record that defines them. They are never added to the global
catalog (RULES) or to the public GET /v1/controls; a record's rule_actions may name only the static rules and its
own comparisons and sections; each tenant's engine policy is built from its own record. Rule ids on a record are
lowercase (``^[a-z][a-z0-9_]{0,79}$``) and may not name a static rule (business.ledger_currency, for example).

Examples are generated from the configuration and are synthetic: a JSON document (comparisons) or labeled text
(sections) that the rule reports, and a look-alike it does not. Other comparisons or sections on the same record get
passing values where their fields do not overlap, so a look-alike usually reports nothing at all.
"""
from __future__ import annotations

import json
import re

RECORD_RULE_ID = r"^[a-z][a-z0-9_]{0,79}$"
COMPARISON_PREFIX = "business."
SECTION_PREFIX = "completeness."
SYMBOLS = {"lt": "<", "le": "\u2264", "eq": "=", "ge": "\u2265", "gt": ">", "ne": "\u2260"}
WORDS = {"lt": "less than", "le": "less than or equal to", "eq": "equal to", "ge": "greater than or equal to",
         "gt": "greater than", "ne": "different from"}
NUMBERS = {-1: 99, 0: 100, 1: 101}
DATES = {-1: "2026-11-03", 0: "2026-11-04", 1: "2026-11-05"}
# For each operator: the left value (relative to the right one) that fails the comparison, and one that passes.
FAIL_PASS = {"lt": (0, -1), "le": (1, 0), "eq": (1, 0), "ge": (-1, 0), "gt": (0, 1), "ne": (0, 1)}
FILLER = "Details provided for this section."


def stage_names(stages) -> str:
    names = [getattr(stage, "value", stage) for stage in stages]
    return " and ".join(names) if names else "no stage"


def _segments(pointer: str) -> list[str]:
    return [segment.replace("~1", "/").replace("~0", "~") for segment in pointer[1:].split("/")]


def _place(document: dict, pointer: str, value) -> bool:
    """Set the value at pointer only where nothing is placed yet (no overwrite, no value in the way)."""
    segments = _segments(pointer)
    target = document
    for segment in segments[:-1]:
        if segment not in target:
            break
        target = target[segment]
        if not isinstance(target, dict):
            return False
    else:
        if segments[-1] in target:
            return False
    return _set(document, pointer, value)

def _values(comparison, which: int):
    table = DATES if comparison.value_type == "date" else NUMBERS
    return table[FAIL_PASS[comparison.operator][which]], table[0]


def _comparison_document(comparison, comparisons, failing: bool) -> str | None:
    document: dict = {}
    for other in comparisons:
        if other.rule_id == comparison.rule_id:
            continue
        left, right = _values(other, 1)
        probe = json.loads(json.dumps(document))
        if _place(probe, other.left, left) and _place(probe, other.right, right):
            document = probe
    left, right = _values(comparison, 0 if failing else 1)
    for pointer, value in ((comparison.left, left), (comparison.right, right)):
        if not _set(document, pointer, value):
            return None
    return json.dumps(document, indent=2)


def _set(document: dict, pointer: str, value) -> bool:
    """Set the value at pointer, overwriting a value placed for another comparison; refuses to replace an object."""
    segments = _segments(pointer)
    target = document
    for segment in segments[:-1]:
        nxt = target.get(segment)
        if nxt is None:
            nxt = target[segment] = {}
        if not isinstance(nxt, dict):
            return False
        target = nxt
    if isinstance(target.get(segments[-1]), dict):
        return False
    target[segments[-1]] = value
    return True


def comparison_rule(comparison, comparisons=()) -> dict:
    sign = SYMBOLS[comparison.operator]
    title = f"Comparison {comparison.rule_id}: {comparison.left} {sign} {comparison.right} ({comparison.value_type})"
    example = _comparison_document(comparison, comparisons or (comparison,), True)
    look_alike = _comparison_document(comparison, comparisons or (comparison,), False)
    if example is not None and example == look_alike:
        example = look_alike = None
    return {
        "rule_id": COMPARISON_PREFIX + comparison.rule_id, "control_id": "cross_field", "title": title,
        "use_case": (f"Turn this on to check, in submitted JSON, that {comparison.left} is {WORDS[comparison.operator]} "
                     f"{comparison.right} ({comparison.value_type}). Defined on this tenant record. It catches: {title}."),
        "severity": "medium", "example": example, "look_alike": look_alike, "needs_setting": None,
        "needs_checks": [], "content_format": "json",
        "configuration": (f"left {comparison.left} \u00b7 operator {comparison.operator} ({WORDS[comparison.operator]}) "
                          f"\u00b7 right {comparison.right} \u00b7 {comparison.value_type} \u00b7 runs on "
                          f"{stage_names(comparison.stages)}"),
        "stages": [getattr(stage, "value", stage) for stage in comparison.stages], "record_rule": True,
    }


def _section_text(sections, leave_out: str | None) -> str:
    lines, seen = [], {}
    for section in sections:
        key = section.label.lower()
        seen[key] = (section.label, max(seen.get(key, ("", 0))[1], section.min_characters))
    for key, (label, minimum) in seen.items():
        if key == leave_out:
            continue
        repeat = -(-minimum // len(FILLER.replace(" ", "")))
        lines.append(f"{label}: " + " ".join([FILLER] * max(1, repeat)))
    return "\n".join(lines) if lines else "No labeled sections here."


def section_rule(section, sections=()) -> dict:
    title = f"Required section {section.rule_id}: \u201c{section.label}\u201d (at least {section.min_characters} characters)"
    sections = tuple(sections) or (section,)
    return {
        "rule_id": SECTION_PREFIX + section.rule_id, "control_id": "completeness", "title": title,
        "use_case": (f"Turn this on to check that a message has exactly one \u201c{section.label}:\u201d section with at "
                     f"least {section.min_characters} characters. Defined on this tenant record. It catches: {title}."),
        "severity": "medium", "example": _section_text(sections, section.label.lower()),
        "look_alike": _section_text(sections, None), "needs_setting": None, "needs_checks": [],
        "content_format": "text",
        "configuration": f"label \u201c{section.label}\u201d \u00b7 at least {section.min_characters} characters \u00b7 runs on "
                         f"{stage_names(section.stages)}",
        "stages": [getattr(stage, "value", stage) for stage in section.stages], "record_rule": True,
    }


def record_rules(record) -> dict[str, dict]:
    """The record's own rules, by rule id: its comparisons, then its required sections."""
    rules = {}
    for comparison in record.comparisons:
        rule = comparison_rule(comparison, record.comparisons)
        rules[rule["rule_id"]] = rule
    for section in record.required_sections:
        rule = section_rule(section, record.required_sections)
        rules[rule["rule_id"]] = rule
    return rules


def record_rule_ids(record) -> dict[str, str]:
    """Rule id -> check id, for the rule ids a record's own comparisons and sections define."""
    return {**{COMPARISON_PREFIX + c.rule_id: "cross_field" for c in record.comparisons},
            **{SECTION_PREFIX + s.rule_id: "completeness" for s in record.required_sections}}


def bad_record_rule_ids(record, static_ids) -> list[str]:
    """Problems with the record's own rule ids, worded without repeating submitted values."""
    problems = []
    for kind, items, prefix in (("comparison", record.comparisons, COMPARISON_PREFIX),
                                ("required section", record.required_sections, SECTION_PREFIX)):
        for item in items:
            if not re.fullmatch(RECORD_RULE_ID, item.rule_id):
                problems.append(f"A {kind} rule_id on a tenant record uses lowercase letters, digits and _ "
                                "(it names a rule a tenant can set)")
            elif prefix + item.rule_id in static_ids:
                problems.append(f"A {kind} rule_id names a rule of another check; choose a different rule_id")
    return sorted(set(problems))
