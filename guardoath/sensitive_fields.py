"""Bounded, explicit field heuristics; not semantic PHI or identity recognition."""

from datetime import date
from time import monotonic

import regex


class SensitiveFieldLimit(ValueError):
    """A configured field scan could not complete within its supported limits."""


LABELS = {
    'phone': 'PHONE_NUMBER', 'phone_number': 'PHONE_NUMBER', 'telephone': 'PHONE_NUMBER',
    'dob': 'DATE_OF_BIRTH', 'date_of_birth': 'DATE_OF_BIRTH', 'birth_date': 'DATE_OF_BIRTH',
    'mrn': 'MEDICAL_RECORD_NUMBER', 'medical_record_number': 'MEDICAL_RECORD_NUMBER',
    'member_id': 'HEALTH_PLAN_ID', 'health_plan_id': 'HEALTH_PLAN_ID',
    'insurance_member_id': 'HEALTH_PLAN_ID',
    'bank_account': 'BANK_ACCOUNT', 'bank_account_number': 'BANK_ACCOUNT',
    'account_number': 'BANK_ACCOUNT',
}
LINE = regex.compile(r'(?m)^[ \t]*(?P<label>[A-Za-z][A-Za-z _-]{0,39})[ \t]*[:=][ \t]*(?P<value>[^\r\n]*)\r?$')


def label_type(label):
    return LABELS.get(regex.sub(r'[ -]+', '_', label.strip().lower()))


def candidate(entity, value):
    if entity == 'DATE_OF_BIRTH':
        try:
            if regex.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
                parsed = date.fromisoformat(value)
            elif regex.fullmatch(r'[0-9]{2}/[0-9]{2}/[0-9]{4}', value):
                month, day, year = map(int, value.split('/'))
                parsed = date(year, month, day)
            else:
                return False
            return 1900 <= parsed.year <= 2099
        except ValueError:
            return False
    if entity == 'PHONE_NUMBER':
        # Explicitly labelled, syntactic candidates only; no numbering-plan lookup.
        if not regex.fullmatch(r'\+?[0-9() .-]{8,32}', value):
            return False
        digits = ''.join(c for c in value if c in '0123456789')
        if value.startswith('+'):
            return 8 <= len(digits) <= 15 and digits[0] != '0'
        return len(digits) == 10 and digits[0] in '23456789' and digits[3] in '23456789'
    if entity == 'BANK_ACCOUNT':
        return bool(regex.fullmatch(r'[0-9]{6,17}', value))
    minimum = 4 if entity == 'MEDICAL_RECORD_NUMBER' else 5
    return bool(regex.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{' + str(minimum-1) + r',31}', value)
                and any(c in '0123456789' for c in value))


def field_spans(text, hint, deadline, budget):
    """Return value spans; JSON key hints apply to string/integer leaves in arrays."""
    spans = []

    def consider(entity, start, end):
        budget[0] += 1
        if budget[0] > 64:
            raise SensitiveFieldLimit("field count")
        while start < end and text[start] in ' \t':
            start += 1
        while end > start and text[end-1] in ' \t':
            end -= 1
        if end - start > 128:
            raise SensitiveFieldLimit('field length')
        if end - start >= 2 and text[start] in '\"\'' and text[end-1] == text[start]:
            start += 1
            end -= 1
        if candidate(entity, text[start:end]):
            spans.append((start, end, entity))

    entity = label_type(hint)
    if entity:
        consider(entity, 0, len(text))
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError('Local scan deadline')
    for match in LINE.finditer(text, timeout=remaining):
        if monotonic() >= deadline:
            raise TimeoutError('Local scan deadline')
        entity = label_type(match['label'])
        if entity:
            consider(entity, match.start('value'), match.end('value'))
    return spans
