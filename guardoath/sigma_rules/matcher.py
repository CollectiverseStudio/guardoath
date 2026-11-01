"""Conservative Sigma condition and modifier evaluator.

Only supports the explicitly documented subset; unsupported operators raise,
never silently pass or silently reinterpret a Sigma rule.
"""
from __future__ import annotations
import fnmatch
import re
from collections.abc import Mapping

class UnsupportedSigmaRule(ValueError):
    pass

def match_value(actual: object, expected: object, modifiers: tuple[str, ...] = ()) -> bool:
    if isinstance(expected, list):
        return any(match_value(actual, item, modifiers) for item in expected)
    if not isinstance(actual, str) or not isinstance(expected, str):
        return actual == expected and not modifiers
    supported = {"contains", "startswith", "endswith", "re", "all", "windash"}
    if set(modifiers) - supported:
        raise UnsupportedSigmaRule("unsupported field modifier")
    left, right = actual.casefold(), expected.casefold()
    if "windash" in modifiers:
        left = left.replace("/", "-")
        right = right.replace("/", "-")
    if "re" in modifiers:
        try:
            return re.search(expected, actual, flags=re.IGNORECASE) is not None
        except re.error as exc:
            raise UnsupportedSigmaRule("invalid regular expression") from exc
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
    if isinstance(selection, list):
        return any(match_selection(item, event) for item in selection)
    if not isinstance(selection, Mapping):
        raise UnsupportedSigmaRule("selection must be a mapping or list")
    for field, expected in selection.items():
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
    condition = detection.get("condition")
    if not isinstance(condition, str):
        raise UnsupportedSigmaRule("missing condition")
    selections = {k: v for k, v in detection.items() if k != "condition" and not k.startswith("timeframe")}
    # Deliberately refuse complex conditions until a proper Sigma parser is added.
    tokens = condition.split()
    if len(tokens) == 1 and tokens[0] in selections:
        return match_selection(selections[tokens[0]], event)
    if len(tokens) == 3 and tokens[0] in {"all", "1"} and tokens[1] == "of":
        pattern = tokens[2]
        names = [name for name in selections if fnmatch.fnmatchcase(name, pattern)]
        if not names:
            raise UnsupportedSigmaRule("condition selector matched no selections")
        results = [match_selection(selections[name], event) for name in names]
        return all(results) if tokens[0] == "all" else any(results)
    raise UnsupportedSigmaRule("unsupported condition expression")
