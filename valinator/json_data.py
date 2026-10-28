"""Shared strict JSON decoding for schema and sensitive-data checks."""

import json
import math

MAX_JSON_BYTES = 262_144
MAX_JSON_DEPTH = 32
MAX_CONTAINER_ITEMS = 1_000


def check_nesting(content: str):
    """Count containers outside strings before invoking a recursive parser."""
    depth = 0
    quoted = escaped = False
    for char in content:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError("JSON nesting limit")
        elif char in "]}":
            depth -= 1


def parse_json(content: str):
    if len(content.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError("JSON byte limit")
    check_nesting(content)
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(_value):
        raise ValueError("Nonfinite JSON number")

    def number(value, converter):
        if len(value) > 128:
            raise ValueError("JSON number limit")
        return converter(value)

    result = json.loads(content, object_pairs_hook=pairs, parse_constant=reject_constant,
                        parse_int=lambda v: number(v, int), parse_float=lambda v: number(v, float))

    def finite(value):
        if isinstance(value, str) and any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError("Unpaired Unicode surrogate")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Nonfinite JSON number")
        if isinstance(value, dict):
            if len(value) > MAX_CONTAINER_ITEMS:
                raise ValueError("JSON object limit")
            for key, item in value.items():
                finite(key)
                finite(item)
        elif isinstance(value, list):
            if len(value) > MAX_CONTAINER_ITEMS:
                raise ValueError("JSON array limit")
            for item in value:
                finite(item)
    finite(result)
    return result
