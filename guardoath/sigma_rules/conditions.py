"""Bounded parser for a safe subset of Sigma detection conditions.

Supports selection names, parentheses, not/and/or and 'all of'/'1 of'
wildcard selectors. No eval, implicit fallback or external code execution.
"""
from __future__ import annotations
import fnmatch
import re
from collections.abc import Mapping
from .matcher import UnsupportedSigmaRule, match_selection

TOKEN = re.compile(r"\s*(\(|\)|1(?=\s+of\b)|[A-Za-z_][A-Za-z0-9_*.-]*)")

def evaluate_condition(condition: str, selections: Mapping[str, object],
                       event: Mapping[str, object]) -> bool:
    if not isinstance(condition, str) or len(condition) > 2048:
        raise UnsupportedSigmaRule("invalid condition")
    tokens = []
    pos = 0
    while pos < len(condition):
        if condition[pos:].isspace():
            break
        match = TOKEN.match(condition, pos)
        if not match:
            raise UnsupportedSigmaRule("unsupported condition token")
        tokens.append(match.group(1))
        pos = match.end()
        if len(tokens) > 256:
            raise UnsupportedSigmaRule("condition too complex")
    if not tokens:
        raise UnsupportedSigmaRule("empty condition")
    index = 0
    def atom(depth: int):
        nonlocal index
        if depth > 16 or index >= len(tokens):
            raise UnsupportedSigmaRule("invalid or overly nested condition")
        word = tokens[index]
        index += 1
        if word == "(":
            result = disjunction(depth + 1)
            if index >= len(tokens) or tokens[index] != ")":
                raise UnsupportedSigmaRule("unbalanced parentheses")
            index += 1
            return result
        if word == "not":
            return not atom(depth + 1)
        if word in {"all", "1"}:
            if index + 1 >= len(tokens) or tokens[index] != "of":
                raise UnsupportedSigmaRule("unsupported quantifier")
            index += 1
            pattern = tokens[index]
            index += 1
            names = [name for name in selections if fnmatch.fnmatchcase(name, pattern)]
            if not names:
                raise UnsupportedSigmaRule("selector matches no fields")
            results = [match_selection(selections[name], event) for name in names]
            return all(results) if word == "all" else any(results)
        if word not in selections:
            raise UnsupportedSigmaRule("unknown selection")
        return match_selection(selections[word], event)
    def conjunction(depth: int):
        nonlocal index
        result = atom(depth)
        while index < len(tokens) and tokens[index] == "and":
            index += 1
            rhs = atom(depth)
            result = result and rhs
        return result
    def disjunction(depth: int):
        nonlocal index
        result = conjunction(depth)
        while index < len(tokens) and tokens[index] == "or":
            index += 1
            rhs = conjunction(depth)
            result = result or rhs
        return result
    result = disjunction(0)
    if index != len(tokens):
        raise UnsupportedSigmaRule("unconsumed condition tokens")
    return result
