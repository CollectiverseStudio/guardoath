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
(sections) that the rule reports, and a look-alike it does not. For a comparison, the example is reported by that
comparison only and the look-alike by none of the record's comparisons on that comparison's stages (2026-11-05: the
values are solved for all of them together and checked with the checker's own value rules). When no such document
exists (contradictory comparisons, a field compared as a number and as a date, a field that is both a value and an
object), the example or look-alike is None and example_unavailable / look_alike_unavailable gives the reason.
"""
from __future__ import annotations

from collections import deque
from datetime import date, timedelta
import json
import re

RECORD_RULE_ID = r"^[a-z][a-z0-9_]{0,79}$"
COMPARISON_PREFIX = "business."
SECTION_PREFIX = "completeness."
SYMBOLS = {"lt": "<", "le": "\u2264", "eq": "=", "ge": "\u2265", "gt": ">", "ne": "\u2260"}
WORDS = {"lt": "less than", "le": "less than or equal to", "eq": "equal to", "ge": "greater than or equal to",
         "gt": "greater than", "ne": "different from"}
FILLER = "Details provided for this section."


def stage_names(stages) -> str:
    names = [getattr(stage, "value", stage) for stage in stages]
    return " and ".join(names) if names else "no stage"


def _segments(pointer: str) -> list[str]:
    return [segment.replace("~1", "/").replace("~0", "~") for segment in pointer[1:].split("/")]


# Examples for comparisons (2026-11-05). One document gives every field one value, chosen so that every other comparison
# on the record that shares a stage with this one passes: in the look-alike this comparison passes too, in the example it
# fails. The values come from solving the comparisons together (difference constraints, below), and every document is
# checked with the cross_field checker's own value rules (guardoath.business) before it is shown. When no such document
# exists, the example or look-alike is None and *_unavailable gives a stable reason. Before 2026-11-05 each comparison's
# values were placed one after another, so a later comparison could overwrite a field an earlier one needed, and a
# look-alike could be reported by the record's other comparisons (docs/field-comparison-portfolio-evidence.md).
#
# Comparisons that share no field are independent: each group of comparisons linked by shared fields is solved once and
# reused, so only the target's own group is solved again for its example. There is no limit on how many comparisons a
# record has (2026-11-05); the work and the listed text per record are bounded instead (MAX_RECORD_WORK,
# MAX_LISTED_BYTES): past them an example is None with search_limit or listing_limit, never a wrong document. Every
# document is also parsed with the checker's own JSON limits (json_limit).
NUMBER_BASE = 100
DATE_BASE = date(2026, 6, 1)
MAX_SOLVES = 512  # "different from" comparisons are tried both ways; this bounds that search per document.
MAX_RECORD_WORK = 5_000_000  # constraint relaxations per record (record_rules), so a GET of the record stays fast.
MAX_LISTED_BYTES = 2_000_000  # example text per record in one response (the API response limit is 6 MB).
MAX_CONTENT_CHARS = 32_000  # what one check accepts (guardoath.models.CheckRequest.content); longer is no example.
NEGATION = {"lt": "ge", "le": "gt", "eq": "ne", "ge": "lt", "gt": "le", "ne": "eq"}
UNAVAILABLE = {
    "mixed_types": "A field is compared as a number in one comparison and as a date in another, so no document can "
                   "satisfy both.",
    "field_inside_field": "A field holds a value for one comparison and other fields for another comparison, so no "
                          "document can satisfy both.",
    "cannot_all_pass": "The comparisons on this record that share a stage with this one cannot all pass at once, so no "
                       "look-alike exists.",
    "cannot_fail_alone": "This comparison cannot fail while the record's other comparisons on its stages pass, so no "
                         "example exists.",
    "search_limit": "Building this example needs more work than one listing allows (many related comparisons).",
    "listing_limit": "This record's examples are larger than one listing holds; the first comparisons' examples are "
                     "listed.",
    "content_limit": "A document with every field these comparisons use is longer than one check accepts (32,000 "
                     "characters), so no example can be checked.",
    "json_limit": "A document with every field these comparisons use breaks the JSON limits of one check (at most "
                  "1,000 fields in one object, nested at most 32 deep), so no example can be checked, and no message "
                  "can pass all of these comparisons. Nest the fields in more objects.",
}


class _Unavailable(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _unavailable(code: str) -> dict:
    return {"code": code, "reason": UNAVAILABLE[code]}


def _shares_stage(a, b) -> bool:
    return bool(set(a.stages) & set(b.stages))


def _difference_edges(constraints):
    """x_left <op> x_right as x_a - x_b <= c edges (b -> a, weight c); 'ne' is returned separately."""
    edges, different = [], []
    for left, right, op in constraints:
        if op == "lt":
            edges.append((right, left, -1))
        elif op == "le":
            edges.append((right, left, 0))
        elif op == "gt":
            edges.append((left, right, -1))
        elif op == "ge":
            edges.append((left, right, 0))
        elif op == "eq":
            edges += [(right, left, 0), (left, right, 0)]
        else:
            different.append((left, right))
    return edges, different


class _Budget:
    def __init__(self):
        self.work = MAX_RECORD_WORK
        self.listed = MAX_LISTED_BYTES


def _solve(variables, edges, budget, start=None):
    """Integer levels with level[b] <= level[a] + weight for every edge (a, b, weight), or None when none exist (a
    negative cycle). Queue-based relaxation from start (a solution of nearby constraints, or all zeros); a level that
    keeps falling along a path longer than the number of fields means a negative cycle."""
    level = {v: (start or {}).get(v, 0) for v in variables}
    out = {}
    for a, b, weight in edges:
        out.setdefault(a, []).append((b, weight))
    steps = dict.fromkeys(variables, 0)
    queue, queued, limit = deque(variables), set(variables), len(variables)
    while queue:
        a = queue.popleft()
        queued.discard(a)
        for b, weight in out.get(a, ()):
            budget.work -= 1
            if budget.work < 0:
                raise _Unavailable("search_limit")
            if level[a] + weight < level[b]:
                level[b] = level[a] + weight
                steps[b] = steps[a] + 1
                if steps[b] > limit:
                    return None
                if b not in queued:
                    queue.append(b)
                    queued.add(b)
    return level


def _levels(variables, constraints, budget, start=None):
    edges, different = _difference_edges(constraints)
    solves = [MAX_SOLVES]

    def search(edges, start):
        solves[0] -= 1
        if solves[0] < 0:
            raise _Unavailable("search_limit")
        level = _solve(variables, edges, budget, start)
        if level is None:
            return None
        clash = next(((a, b) for a, b in different if level[a] == level[b]), None)
        if clash is None:
            return level
        a, b = clash
        return search(edges + [(b, a, -1)], level) or search(edges + [(a, b, -1)], level)

    return search(edges, start)


def _comparison_document(values: dict[tuple, object]) -> dict:
    """Build *real* array/object JSON for tenant rule examples.

    JSON Pointer numeric segments are ambiguous: a numeric object key and an
    array index have the same spelling. Prefer an array only if ALL children
    at that prefix are canonical, bounded array indices. Mixed/nonnumeric
    siblings remain object keys. Keep the root an object for deterministic
    examples, even when its first key is numeric. This never changes rule
    evaluation: the shared business.resolve accepts both container types.
    """
    from guardoath.json_data import MAX_CONTAINER_ITEMS

    children: dict[tuple, set[str]] = {}
    for path in values:
        for position in range(1, len(path)):
            children.setdefault(path[:position], set()).add(path[position])
    arrays = {prefix for prefix, names in children.items()
              if all(re.fullmatch(r"0|[1-9][0-9]*", name)
                     and int(name) < MAX_CONTAINER_ITEMS for name in names)}
    document: dict = {}
    for path, value in sorted(values.items()):
        container = document
        for position, segment in enumerate(path):
            last = position == len(path) - 1
            if isinstance(container, list):
                index = int(segment)
                if index >= MAX_CONTAINER_ITEMS:
                    raise ValueError("Invalid generated JSON array index")
                if len(container) <= index:
                    container.extend([None] * (index + 1 - len(container)))
                existing = container[index]
            else:
                existing = container.get(segment)
            if last:
                if isinstance(container, list):
                    container[index] = value
                else:
                    container[segment] = value
            else:
                wanted = [] if path[:position + 1] in arrays else {}
                if existing is None:
                    existing = wanted
                    if isinstance(container, list):
                        container[index] = existing
                    else:
                        container[segment] = existing
                elif not isinstance(existing, type(wanted)):
                    raise ValueError("Conflicting generated JSON container layout")
                container = existing
    return document


def _field_document(fields: dict, values: dict) -> str:
    """Serialize a non-reference advanced rule's samples with genuine indexed arrays.

    This is display/sample generation only. The shared Engine still decides
    whether a submitted document satisfies a tenant rule. Never call this for
    reference-backed rules: their private reference samples must stay hidden.
    """
    return json.dumps(_comparison_document({
        tuple(_segments(pointer)): values[role]
        for role, pointer in fields.items() if role in values
    }), separators=(",", ":"))


def _passes(payload, comparison) -> bool:
    """The cross_field checker's own value rules (guardoath.business) for one comparison."""
    from decimal import DecimalException
    from guardoath.business import OPERATORS, resolve, typed
    try:
        left, right = (typed(resolve(payload, p), comparison.value_type) for p in (comparison.left, comparison.right))
        return bool(OPERATORS[comparison.operator](left, right))
    except (ValueError, KeyError, IndexError, TypeError, OverflowError, DecimalException):
        return False


class _Examples:
    """Example documents for one record's comparisons. Groups of comparisons that share fields are solved once."""

    def __init__(self, comparisons):
        self.comparisons = tuple(comparisons)
        self.paths = {}
        for comparison in self.comparisons:
            for pointer in (comparison.left, comparison.right):
                self.paths.setdefault(pointer, tuple(_segments(pointer)))
        self.budget = _Budget()
        self._layouts = {}
        self._solutions = {}
        self._smallest = {}  # active set -> size of the smallest document listed for it
        self._active = {}  # a comparison's stages -> the comparisons that share one of them
        self._json_limited = set()  # active sets whose documents break the checker's JSON limits

    def _layout(self, active: tuple):
        """Groups of active comparisons linked by shared fields, or an _Unavailable code for the whole active set."""
        if active in self._layouts:
            return self._layouts[active]
        kinds = {}
        for index in active:
            comparison = self.comparisons[index]
            for pointer in (comparison.left, comparison.right):
                if kinds.setdefault(self.paths[pointer], comparison.value_type) != comparison.value_type:
                    self._layouts[active] = "mixed_types"
                    return "mixed_types"
        ordered = sorted(kinds)
        # Paths that start with another path form a block right after it in sorted order.
        if any(ordered[i + 1][:len(ordered[i])] == ordered[i] for i in range(len(ordered) - 1)):
            self._layouts[active] = "field_inside_field"
            return "field_inside_field"
        parent = {}

        def find(x):
            while parent.setdefault(x, x) != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for index in active:
            comparison = self.comparisons[index]
            parent[find(self.paths[comparison.left])] = find(self.paths[comparison.right])
        groups = {}
        for index in active:
            groups.setdefault(find(self.paths[self.comparisons[index].left]), []).append(index)
        groups = tuple(tuple(group) for group in groups.values())
        layout = (kinds, {index: group for group in groups for index in group}, groups)
        self._layouts[active] = layout
        return layout

    def _group_values(self, group: tuple, kinds, negate=None):
        """Field values for one group (negate = index of the comparison that must fail), checked; or a code."""
        key = (group, negate)
        if key in self._solutions:
            return self._solutions[key]
        constraints, impossible = [], False
        for index in group:
            comparison = self.comparisons[index]
            op = NEGATION[comparison.operator] if index == negate else comparison.operator
            left, right = self.paths[comparison.left], self.paths[comparison.right]
            if left == right:
                impossible = impossible or op in ("lt", "gt", "ne")
                continue
            constraints.append((left, right, op))
        variables = sorted({p for index in group for p in (self.paths[self.comparisons[index].left],
                                                           self.paths[self.comparisons[index].right])})
        # An example starts from the group's look-alike levels: only the changed comparison has to move.
        base = self._solutions.get((group, None)) if negate is not None else None
        start = base[1] if isinstance(base, tuple) else None
        try:
            level = None if impossible else _levels(variables, constraints, self.budget, start)
        except _Unavailable as reason:
            self._solutions[key] = reason.code
            return reason.code
        if level is None:
            result = "cannot_fail_alone" if negate is not None else "cannot_all_pass"
        else:
            values = {path: NUMBER_BASE + level[path] if kinds[path] == "number"
                      else (DATE_BASE + timedelta(days=level[path])).isoformat() for path in variables}
            payload = _comparison_document(values)
            from guardoath.business import exact_number
            payload = json.loads(json.dumps(payload), parse_float=exact_number)
            ok = all(_passes(payload, self.comparisons[index]) == (index != negate) for index in group)
            result = (values, level) if ok else ("cannot_fail_alone" if negate is not None else "cannot_all_pass")
        self._solutions[key] = result
        return result

    def document(self, target: int, failing: bool):
        """(text, None) or (None, unavailable) for comparisons[target] failing (example) or passing (look-alike)."""
        comparison = self.comparisons[target]
        stages = frozenset(comparison.stages)
        active = self._active.get(stages)
        if active is None:
            # Every comparison that shares a stage with this one (rule ids are unique on a valid record).
            active = self._active[stages] = tuple(i for i, other in enumerate(self.comparisons)
                                                  if stages & set(other.stages))
        layout = self._layout(active)
        if isinstance(layout, str):
            return None, _unavailable(layout)
        kinds, group_of, groups = layout
        if self._smallest.get(active, 0) > MAX_CONTENT_CHARS:
            return None, _unavailable("content_limit")
        if active in self._json_limited:
            return None, _unavailable("json_limit")
        if self._smallest.get(active, 0) > self.budget.listed:
            return None, _unavailable("listing_limit")
        values = {}
        for group in groups:
            own = group == group_of[target]
            solved = self._group_values(group, kinds, target if failing and own else None)
            if isinstance(solved, str):
                code = solved if own or solved == "search_limit" else (
                    "cannot_fail_alone" if failing else "cannot_all_pass")
                return None, _unavailable(code)
            values.update(solved[0])
        document = _comparison_document(values)
        text = json.dumps(document, indent=2)
        if len(text) > MAX_CONTENT_CHARS:
            text = json.dumps(document, separators=(",", ":"))  # compact when the readable form is too long
        size = len(text.encode("utf-8"))
        self._smallest[active] = min(self._smallest.get(active, size), size)
        if len(text) > MAX_CONTENT_CHARS:
            return None, _unavailable("content_limit")
        # The checker parses with the same strict limits (guardoath.json_data.parse_json: fields per object, depth),
        # so a document it would refuse is never offered (2026-11-05: 1,000 comparisons on 2,000 top-level fields).
        from guardoath.json_data import parse_json
        try:
            parse_json(text)
        except ValueError:
            self._json_limited.add(active)
            return None, _unavailable("json_limit")
        if size > self.budget.listed:
            return None, _unavailable("listing_limit")
        self.budget.listed -= size
        return text, None


def _comparison_examples(comparison, comparisons, context=None):
    """(example, look_alike, example_unavailable, look_alike_unavailable) for one comparison on its record."""
    comparisons = tuple(comparisons)
    if context is None or context.comparisons != comparisons:
        context = _Examples(comparisons)
    target = next(i for i, other in enumerate(comparisons) if other is comparison or other == comparison)
    look_alike, look_alike_missing = context.document(target, False)
    example, example_missing = context.document(target, True)
    return example, look_alike, example_missing, look_alike_missing


def comparison_rule(comparison, comparisons=(), context=None) -> dict:
    sign = SYMBOLS[comparison.operator]
    title = f"Comparison {comparison.rule_id}: {comparison.left} {sign} {comparison.right} ({comparison.value_type})"
    example, look_alike, example_unavailable, look_alike_unavailable = _comparison_examples(
        comparison, tuple(comparisons) or (comparison,), context)
    return {
        "rule_id": COMPARISON_PREFIX + comparison.rule_id, "control_id": "cross_field", "title": title,
        "use_case": (f"Turn this on to check, in submitted JSON, that {comparison.left} is {WORDS[comparison.operator]} "
                     f"{comparison.right} ({comparison.value_type}). Defined on this tenant record. It catches: {title}."),
        "severity": "medium", "example": example, "look_alike": look_alike,
        "example_unavailable": example_unavailable, "look_alike_unavailable": look_alike_unavailable, "needs_setting": None,
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


# FC-031/032/033/034/036/037/041/046 accepted synthetic examples.
# These are independently configurable business.* tenant rules under the existing
# cross_field check; not public static catalog rules or authoritative references.
ARITHMETIC_EXAMPLES = {
    "invoice_line_subtotal": ({"lines": [{"quantity": 2, "unit_price": 40}, {"quantity": 1, "unit_price": 30}], "subtotal": 110}, "subtotal", 100),
    "invoice_total": ({"subtotal": 100, "discount": 10, "tax": 8, "shipping": 5, "total": 103}, "total", 110),
    "tax_tolerance": ({"tax": 8, "base": 100, "rate": 0.08, "tolerance": 0.01}, "tax", 12),
    "discount_fraction_cap": ({"discount": 10, "eligible_subtotal": 100, "max_fraction": 0.2}, "discount", 30),
    "inventory_conservation": ({"opening": 10, "received": 5, "shipped": 8, "lost": 1, "closing": 6}, "closing", 9),
    "shipment_quantity_sum": ({"lines": [{"quantity": 3}, {"quantity": 4}], "total": 7}, "total", 8),
    "manufacturing_tolerance": ({"measured": 10.4, "nominal": 10, "tolerance": 0.5}, "measured", 10.6),
    "carbon_intensity": ({"emissions": 700, "output": 100, "max_intensity": 8}, "emissions", 1000),
}


def arithmetic_rule(rule) -> dict:
    clean, variable, unsafe_value = ARITHMETIC_EXAMPLES[rule.kind]
    clean = dict(clean)
    unsafe = {**clean, variable: unsafe_value}
    def document(values):
        return _field_document(rule.fields, values)
    return {
        "rule_id": COMPARISON_PREFIX + rule.rule_id,
        "control_id": "cross_field",
        "title": f"Advanced arithmetic {rule.rule_id}: {rule.kind}",
        "use_case": f"Validate exact bounded {rule.kind.replace('_', ' ')} relationships in submitted JSON.",
        "severity": "high",
        "example": document(unsafe),
        "look_alike": document(clean),
        "example_unavailable": None,
        "look_alike_unavailable": None,
        "needs_setting": None,
        "needs_checks": [],
        "content_format": "json",
        "configuration": f"{rule.kind} with pointers " + ", ".join(
            f"{name}={pointer}" for name, pointer in rule.fields.items()) + " · runs on " + stage_names(rule.stages),
        "stages": [getattr(stage, "value", stage) for stage in rule.stages],
        "record_rule": True,
    }


IDENTITY_EXAMPLES = {
    "order_id_match": (
        {"order_id": "ORDER1", "shipment_order_id": "ORDER1"},
        {"order_id": "ORDER1", "shipment_order_id": "ORDER2"},
    ),
    "serial_unique": (
        {"serials": ["SERIAL1", "SERIAL2"]},
        {"serials": ["SERIAL1", "SERIAL1"]},
    ),
}


def identity_rule(rule) -> dict:
    def document(values):
        return _field_document(rule.fields, values)
    samples = IDENTITY_EXAMPLES.get(rule.kind)
    unavailable = None if samples else {
        "code": "private_tenant_reference",
        "reason": "A reference-backed rule cannot disclose tenant-trusted values as sample JSON.",
    }
    return {
        "rule_id": COMPARISON_PREFIX + rule.rule_id,
        "control_id": "cross_field",
        "title": f"Identity constraint {rule.rule_id}: {rule.kind}",
        "use_case": f"Validate {rule.kind.replace('_', ' ')} using the tenant's configured policy.",
        "severity": "high",
        "example": document(samples[1]) if samples else None,
        "look_alike": document(samples[0]) if samples else None,
        "example_unavailable": unavailable,
        "look_alike_unavailable": unavailable,
        "needs_setting": None,
        "needs_checks": [],
        "content_format": "json",
        "configuration": f"{rule.kind} with pointers " + ", ".join(
            f"{role}={pointer}" for role, pointer in rule.fields.items()) +
            " · runs on " + stage_names(rule.stages) +
            (" · trusted reference set stored on tenant (values private)" if rule.trusted_values else ""),
        "stages": [getattr(stage, "value", stage) for stage in rule.stages],
        "record_rule": True,
    }


CONVERSION_TIME_EXAMPLES = {
    "unit_load_kg_cap": (
        {"load": 1000, "load_unit": "lb", "max_kg": 600},
        {"load": 1500, "load_unit": "lb", "max_kg": 600},
    ),
    "cold_chain_excursion": (
        {"excursion_start": "2026-11-06T10:00:00Z", "excursion_end": "2026-11-06T10:15:00Z",
         "allowed_minutes": 30},
        {"excursion_start": "2026-11-06T10:00:00Z", "excursion_end": "2026-11-06T10:45:00Z",
         "allowed_minutes": 30},
    ),
    # Populate the optional return in *both* generated examples. When a
    # customer's delivery timestamp lives within the same recorded return
    # object as return_at, omitting return_at makes the supposed clean sample
    # fail the actual tenant rule. A three-event message remains independently
    # valid where the fourth event is genuinely absent.
    "lifecycle_chronology": (
        {"order_at": "2026-11-06T09:00:00Z", "shipment_at": "2026-11-06T10:00:00Z",
         "delivery_at": "2026-11-06T11:00:00Z", "return_at": "2026-11-06T13:00:00Z"},
        {"order_at": "2026-11-06T09:00:00Z", "shipment_at": "2026-11-06T12:00:00Z",
         "delivery_at": "2026-11-06T11:00:00Z", "return_at": "2026-11-06T13:00:00Z"},
    ),
}


def conversion_time_rule(rule) -> dict:
    def document(values):
        return _field_document(rule.fields, values)
    samples = CONVERSION_TIME_EXAMPLES.get(rule.kind)
    unavailable = None if samples else {
        "code": "private_tenant_reference",
        "reason": "FX reference-backed conversion cannot display tenant-owned rate or reference as a sample.",
    }
    return {
        "rule_id": COMPARISON_PREFIX + rule.rule_id,
        "control_id": "cross_field",
        "title": f"Conversion/time constraint {rule.rule_id}: {rule.kind}",
        "use_case": f"Validate bounded {rule.kind.replace('_', ' ')} from tenant-configured JSON fields.",
        "severity": "high",
        "example": document(samples[1]) if samples else None,
        "look_alike": document(samples[0]) if samples else None,
        "example_unavailable": unavailable,
        "look_alike_unavailable": unavailable,
        "needs_setting": None,
        "needs_checks": [],
        "content_format": "json",
        "configuration": f"{rule.kind} with pointers " + ", ".join(
            f"{role}={pointer}" for role, pointer in rule.fields.items()) +
            " · runs on " + stage_names(rule.stages) +
            (" · versioned tenant FX reference (values private)" if rule.kind == "currency_conversion_cap" else ""),
        "stages": [getattr(stage, "value", stage) for stage in rule.stages],
        "record_rule": True,
    }


def trusted_reference_rule(rule) -> dict:
    """Dynamic tenant rule metadata; never expose reference values or signature."""
    return {
        "rule_id": COMPARISON_PREFIX + rule.rule_id,
        "control_id": "cross_field",
        "title": f"Host-attested reference {rule.rule_id}: {rule.kind}",
        "use_case": "Check submitted values against a host-signed, versioned, time-bounded reference snapshot.",
        "severity": "high",
        "example": None, "look_alike": None,
        "example_unavailable": {"code": "private_host_reference",
                                "reason": "Signed authoritative reference data is private; no sample is invented."},
        "look_alike_unavailable": {"code": "private_host_reference",
                                   "reason": "No verified benign reference sample is invented."},
        "needs_setting": None, "needs_checks": [],
        "content_format": "json",
        "configuration": f"{rule.kind} · host-attested reference · runs on {stage_names(rule.stages)}",
        "stages": [getattr(stage, "value", stage) for stage in rule.stages],
        "record_rule": True,
    }


def statistical_drift_rule(rule) -> dict:
    """Private dynamic PSI rule; never publish reference counts or samples."""
    return {
        "rule_id": COMPARISON_PREFIX + rule.rule_id,
        "control_id": "cross_field",
        "title": f"Versioned batch distribution drift: {rule.rule_id}",
        "use_case": "Compare submitted bounded numeric batches against a tenant-approved histogram and PSI threshold.",
        "severity": "high",
        "example": None, "look_alike": None,
        "example_unavailable": {"code": "private_statistical_reference",
                                "reason": "Tenant reference distribution cannot be invented or disclosed."},
        "look_alike_unavailable": {"code": "private_statistical_reference",
                                   "reason": "No verified benign population sample is disclosed."},
        "needs_setting": None, "needs_checks": [],
        "content_format": "json",
        "configuration": f"batch PSI · versioned tenant baseline · runs on {stage_names(rule.stages)}",
        "stages": [getattr(stage, "value", stage) for stage in rule.stages],
        "record_rule": True,
    }


def record_rules(record) -> dict[str, dict]:
    """The record's own rules, by rule id: its comparisons, then its required sections."""
    rules, context = {}, _Examples(record.comparisons)
    for comparison in record.comparisons:
        rule = comparison_rule(comparison, record.comparisons, context)
        rules[rule["rule_id"]] = rule
    for advanced in getattr(record, "advanced_arithmetic", ()):
        rule = arithmetic_rule(advanced)
        rules[rule["rule_id"]] = rule
    for identity in getattr(record, "advanced_identity", ()):
        rule = identity_rule(identity)
        rules[rule["rule_id"]] = rule
    for conversion_time in getattr(record, "advanced_conversion_time", ()):
        rule = conversion_time_rule(conversion_time)
        rules[rule["rule_id"]] = rule
    for trusted in getattr(record, "advanced_trusted_reference", ()):
        rule = trusted_reference_rule(trusted)
        rules[rule["rule_id"]] = rule
    for drift in getattr(record, "advanced_statistical_drift", ()):
        rule = statistical_drift_rule(drift)
        rules[rule["rule_id"]] = rule
    for section in record.required_sections:
        rule = section_rule(section, record.required_sections)
        rules[rule["rule_id"]] = rule
    return rules


def record_rule_ids(record) -> dict[str, str]:
    """Rule id -> check id, for the rule ids a record's own comparisons and sections define."""
    return {**{COMPARISON_PREFIX + c.rule_id: "cross_field" for c in record.comparisons},
            **{COMPARISON_PREFIX + c.rule_id: "cross_field" for c in getattr(record, "advanced_arithmetic", ())},
            **{COMPARISON_PREFIX + c.rule_id: "cross_field" for c in getattr(record, "advanced_identity", ())},
            **{COMPARISON_PREFIX + c.rule_id: "cross_field" for c in getattr(record, "advanced_conversion_time", ())},
            **{COMPARISON_PREFIX + c.rule_id: "cross_field" for c in getattr(record, "advanced_trusted_reference", ())},
            **{COMPARISON_PREFIX + c.rule_id: "cross_field" for c in getattr(record, "advanced_statistical_drift", ())},
            **{SECTION_PREFIX + s.rule_id: "completeness" for s in record.required_sections}}


def bad_record_rule_ids(record, static_ids) -> list[str]:
    """Problems with the record's own rule ids, worded without repeating submitted values."""
    problems = []
    for kind, items, prefix in (("comparison", record.comparisons, COMPARISON_PREFIX),
                                ("arithmetic comparison", getattr(record, "advanced_arithmetic", ()), COMPARISON_PREFIX),
                                ("identity comparison", getattr(record, "advanced_identity", ()), COMPARISON_PREFIX),
                                ("conversion/time comparison", getattr(record, "advanced_conversion_time", ()), COMPARISON_PREFIX),
                                ("trusted reference comparison", getattr(record, "advanced_trusted_reference", ()), COMPARISON_PREFIX),
                                ("statistical drift comparison", getattr(record, "advanced_statistical_drift", ()), COMPARISON_PREFIX),
                                ("required section", record.required_sections, SECTION_PREFIX)):
        for item in items:
            if not re.fullmatch(RECORD_RULE_ID, item.rule_id):
                problems.append(f"A {kind} rule_id on a tenant record uses lowercase letters, digits and _ "
                                "(it names a rule a tenant can set)")
            elif prefix + item.rule_id in static_ids:
                problems.append(f"A {kind} rule_id names a rule of another check; choose a different rule_id")
    return sorted(set(problems))
