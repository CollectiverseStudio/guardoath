"""Bounded obfuscation signals, not an injection or semantic safety detector."""

import base64
import binascii
import re

from valinator.json_data import parse_json
from valinator.models import CheckerResult, Finding

MAX_PAYLOADS = 16
MAX_TOKEN_CHARACTERS = 4096
MAX_TOTAL_TOKEN_CHARACTERS = 8192
ENCODED = re.compile(r"\b(base64|hex):[ \t]*([^\s]*)", re.IGNORECASE | re.ASCII)
# Joiners/non-joiners are deliberately excluded: used in scripts and emoji.
SELECTED_INVISIBLE = {0x00AD, 0x034F, 0x061C, 0x180E, 0x200B, 0x200E, 0x200F,
                      0x2060, 0xFEFF}


def hidden(text):
    return any(ord(c) in SELECTED_INVISIBLE or 0x202A <= ord(c) <= 0x202E
               or 0x2066 <= ord(c) <= 0x2069
               or (ord(c) < 32 and c not in '\n\r\t') or 0x7F <= ord(c) <= 0x9F
               for c in text)


def texts(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from texts(item)
    elif isinstance(value, list):
        for item in value:
            yield from texts(item)


async def text_security_check(request, policy, stage):
    try:
        parts = (request.content,) if request.content_format == 'text' else texts(parse_json(request.content))
        found = set()
        count = total = 0
        for text in parts:
            if hidden(text):
                found.add('hidden_controls')
            for match in ENCODED.finditer(text):
                kind, token = match.groups()
                count += 1
                total += len(token)
                if count > MAX_PAYLOADS or len(token) > MAX_TOKEN_CHARACTERS or total > MAX_TOTAL_TOKEN_CHARACTERS:
                    return CheckerResult(checker_id='text_security', checker_version='1', status='error',
                                         error_code='text_inspection_budget_exceeded')
                try:
                    if not token:
                        raise ValueError('Empty encoding')
                    if kind.lower() == 'base64':
                        decoded = base64.b64decode(token, validate=True).decode('utf-8')
                    else:
                        if not re.fullmatch(r'(?:[0-9a-fA-F]{2})+', token):
                            raise ValueError('Invalid hex')
                        decoded = bytes.fromhex(token).decode('utf-8')
                except (ValueError, UnicodeError, binascii.Error):
                    found.add('invalid_encoding')
                    continue
                found.add('encoded_text')
                if hidden(decoded):
                    found.add('encoded_hidden_controls')
    except (ValueError, RecursionError):
        return CheckerResult(checker_id='text_security', checker_version='1', status='error',
                             error_code='invalid_json_for_text_inspection')
    explanations = {
        'hidden_controls': 'Selected invisible, bidirectional, or control characters need review. They can have legitimate uses; content was not modified.',
        'encoded_text': 'Explicitly labeled Base64 or hex UTF-8 text needs review. One layer was decoded locally for selected control-character inspection only; meaning and malicious intent were not assessed.',
        'invalid_encoding': 'An explicit Base64 or hex label has an empty, invalid, or non-UTF-8 payload. No decoded content is returned.',
        'encoded_hidden_controls': 'Selected hidden control characters were found inside a labeled encoded payload. Decoded content is not returned.',
    }
    return CheckerResult(checker_id='text_security', checker_version='1', status='flagged' if found else 'passed',
        findings=tuple(Finding(rule_id=f'text_security.{name}', category='prompt_security', severity='low',
                               explanation=explanations[name]) for name in sorted(found)))


text_security_check.checker_version = '1'
