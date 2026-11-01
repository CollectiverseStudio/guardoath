"""Bound Python regex search without changing regex-engine semantics.

A disposable isolated interpreter preserves this runtime's exact stdlib `re`
behavior. subprocess.run kills and reaps a worker that exceeds the deadline.
Patterns/events go over stdin, never command arguments, logs or error messages.
This source-only path is deliberately conservative; it is not hosted integration.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

MAX_REGEX_PATTERN_CHARS = 512
MAX_REGEX_INPUT_CHARS = 32_000
REGEX_TIMEOUT_SECONDS = 1.0


class SigmaEvaluationError(RuntimeError):
    """Evaluation was incomplete; callers must not interpret it as a clean event."""


class SigmaRegexTimeout(SigmaEvaluationError):
    """A worker exceeded the regex execution deadline."""


# No caller-controlled source is interpolated into this program.
_WORKER = 'import json,re,sys; p,s=json.load(sys.stdin); sys.stdout.write("1" if re.search(p,s) is not None else "0")'


def validate_pattern(pattern: object) -> None:
    if not isinstance(pattern, str) or len(pattern) > MAX_REGEX_PATTERN_CHARS:
        raise ValueError('regex pattern exceeds supported bounds')
    try:
        re.compile(pattern)
    except (re.error, OverflowError, RecursionError) as exc:
        raise ValueError('invalid regular expression') from exc


def bounded_search(pattern: str, actual: str) -> bool:
    validate_pattern(pattern)
    if not isinstance(actual, str) or len(actual) > MAX_REGEX_INPUT_CHARS:
        raise SigmaEvaluationError('regex event text exceeds supported bounds')
    try:
        result = subprocess.run(
            [sys.executable, '-I', '-c', _WORKER],
            input=json.dumps([pattern, actual], ensure_ascii=True),
            encoding='utf-8', stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=REGEX_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired:
        raise SigmaRegexTimeout('regex execution deadline exceeded') from None
    except (OSError, subprocess.SubprocessError):
        raise SigmaEvaluationError('regex worker unavailable') from None
    if result.returncode != 0 or result.stdout not in {'0', '1'}:
        raise SigmaEvaluationError('regex worker failed')
    return result.stdout == '1'
