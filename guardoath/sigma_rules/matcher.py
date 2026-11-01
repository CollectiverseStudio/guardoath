"""Conservative Sigma condition and modifier evaluator.

Only supports the explicitly documented subset; unsupported operators raise,
never silently pass or silently reinterpret a Sigma rule.
"""
from __future__ import annotations
import fnmatch
from collections.abc import Mapping
from .bounded_regex import bounded_search, validate_pattern
from .budgets import evaluation_budget

class UnsupportedSigmaRule(ValueError):
    pass

def validate_selection(selection: object, depth: int = 0) -> None:
    """Check every branch without depending on a particular event's values."""
    if depth > 16:
        raise UnsupportedSigmaRule("selection too deeply nested")
    if isinstance(selection, list):
        if not selection:
            raise UnsupportedSigmaRule("empty selection list")
        for item in selection:
            validate_selection(item, depth + 1)
        return
    if not isinstance(selection, Mapping) or not selection:
        raise UnsupportedSigmaRule("selection must be a nonempty mapping or list")
    for field, expected in selection.items():
        if not isinstance(field, str) or not field or not field.split("|")[0]:
            raise UnsupportedSigmaRule("invalid selection field")
        modifiers = field.split("|")[1:]
        supported = {"contains", "startswith", "endswith", "re", "all", "windash"}
        if len(set(modifiers)) != len(modifiers) or set(modifiers) - supported:
            raise UnsupportedSigmaRule("unsupported field modifier")
        # The legacy helper's slash replacement is not Sigma's windash
        # expansion. Do not admit such rules until that expansion is faithful.
        if "windash" in modifiers:
            raise UnsupportedSigmaRule("windash import is unsupported")
        if len(set(modifiers) & {"contains", "startswith", "endswith", "re"}) > 1:
            raise UnsupportedSigmaRule("unsupported modifier combination")
        if "re" in modifiers and ("windash" in modifiers or "all" in modifiers):
            raise UnsupportedSigmaRule("unsupported regex modifier combination")
        values = expected if isinstance(expected, list) else [expected]
        if not values or any(not isinstance(item, str) for item in values):
            raise UnsupportedSigmaRule("only strings and nonempty string lists supported")
        if "all" in modifiers and not isinstance(expected, list):
            raise UnsupportedSigmaRule("all requires a list")
        if "re" not in modifiers and any("*" in item or "?" in item for item in values):
            raise UnsupportedSigmaRule("Sigma wildcard import is unsupported")
        if "re" in modifiers:
            for item in values:
                try:
                    validate_pattern(item)
                except ValueError:
                    raise UnsupportedSigmaRule("invalid or oversized regular expression") from None

def match_value(actual: object, expected: object, modifiers: tuple[str, ...] = ()) -> bool:
    with evaluation_budget():
        return _match_value(actual, expected, modifiers)

def _match_value(actual: object, expected: object, modifiers: tuple[str, ...] = ()) -> bool:
    if isinstance(expected, list):
        return any(_match_value(actual, item, modifiers) for item in expected)
    supported = {"contains", "startswith", "endswith", "re", "all", "windash"}
    if set(modifiers) - supported:
        raise UnsupportedSigmaRule("unsupported field modifier")
    if not isinstance(actual, str) or not isinstance(expected, str):
        return actual == expected and not modifiers
    left, right = actual.casefold(), expected.casefold()
    if "windash" in modifiers:
        left = left.replace("/", "-")
        right = right.replace("/", "-")
    if "re" in modifiers:
        try:
            # Sigma regex values are case sensitive unless they specify flags.
            return bounded_search(expected, actual)
        except ValueError:
            raise UnsupportedSigmaRule("invalid or oversized regular expression") from None
    if "contains" in modifiers:
        return right in left
    if "startswith" in modifiers:
        return left.startswith(right)
    if "endswith" in modifiers:
        return left.endswith(right)
    if "*" in right or "?" in right:
        return fnmatch.fnmatchcase(left, right)
    return left == right

def match_selection(selection: object, event: Mapping[str, object]) -> bool:
    with evaluation_budget():
        return _validated_selection(selection, event)

def _validated_selection(selection: object, event: Mapping[str, object]) -> bool:
    if not isinstance(event, Mapping):
        raise UnsupportedSigmaRule("event must be a mapping")
    validate_selection(selection)
    return _match_selection(selection, event)

def _match_selection(selection: object, event: Mapping[str, object]) -> bool:
    if isinstance(selection, list):
        return any(_match_selection(item, event) for item in selection)
    if not isinstance(selection, Mapping):
        raise UnsupportedSigmaRule("selection must be a mapping or list")
    for field, expected in selection.items():
        if not isinstance(field, str) or not field:
            raise UnsupportedSigmaRule("invalid selection field")
        name, *modifiers = field.split("|")
        actual = event.get(name)
        if "all" in modifiers and isinstance(expected, list):
            others = tuple(x for x in modifiers if x != "all")
            matched = all(match_value(actual, item, others) for item in expected)
        else:
            matched = match_value(actual, expected, tuple(x for x in modifiers if x != "all"))
        if not matched:
            return False
    return True

def match_detection(detection: Mapping[str, object], event: Mapping[str, object]) -> bool:
    with evaluation_budget():
        return _match_detection(detection, event)

def _match_detection(detection: Mapping[str, object], event: Mapping[str, object]) -> bool:
    if not isinstance(detection, Mapping) or not isinstance(event, Mapping):
        raise UnsupportedSigmaRule("detection and event must be mappings")
    for name, selection in detection.items():
        if not isinstance(name, str) or not name:
            raise UnsupportedSigmaRule("invalid selection name")
        if name == "timeframe":
            raise UnsupportedSigmaRule("timeframe is unsupported")
        if name != "condition":
            validate_selection(selection)
    condition = detection.get("condition")
    if not isinstance(condition, str):
        raise UnsupportedSigmaRule("missing condition")
    selections = {k: v for k, v in detection.items() if k != "condition"}
    from .conditions import evaluate_condition
    return evaluate_condition(condition, selections, event)
