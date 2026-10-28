"""Shared strict JSON decoding for schema and sensitive-data checks."""

import json
import math


def parse_json(content: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(_value):
        raise ValueError("Nonfinite JSON number")

    result = json.loads(content, object_pairs_hook=pairs, parse_constant=reject_constant)

    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Nonfinite JSON number")
        if isinstance(value, dict):
            for item in value.values():
                finite(item)
        elif isinstance(value, list):
            for item in value:
                finite(item)
    finite(result)
    return result
