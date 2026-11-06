"""PII concurrent regex scheduling: semantic parity and safe local worker execution.

The matching engine is asked to release the GIL on immutable strings; all
recognizers, deadlines, outcome contracts and required redaction rechecks remain.
These checks intentionally don't assert machine-dependent wall clock speed.
"""
from concurrent.futures import ThreadPoolExecutor

import pytest

regex = pytest.importorskip("regex")


@pytest.mark.parametrize("expression,flags,sample", [
    (r"(?i)\b(?:ssns?|social[\s_-]*security)\b", 0,
     "My SSN is 123-45-6789; what counts as a social security number?"),
    (r"\w+", regex.IGNORECASE, "Núñez 한국어 date_of_birth DOB 2026-11-06"),
    (r"\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b", 0,
     "Synthetic 123-45-6789 and 987-65-4321 are examples."),
    (r"\b(?:(?:\+1\s?)?\d{3}[ -]?)\d{3}[ -]?\d{4}\b", 0,
     "Call +1 202-555-0147 or 202 555 0181"),
    (r"(?i)\b(?:email|mail)\b", 0,
     "Email me with a test@example.com email address."),
])
def test_regex_gil_release_does_not_change_match_offsets_or_text(expression, flags, sample):
    compiled = regex.compile(expression, flags)
    # Original behavior was concurrent=False. Compare all matches, not just
    # one finding, and preserve the exact offsets and Unicode match text.
    def spans(concurrent):
        return [(m.start(), m.end(), m.group()) for m in
                compiled.finditer(sample, concurrent=concurrent, timeout=0.2)]
    assert spans(True) == spans(False)


def test_lookahead_and_overlapped_matches_have_identical_results():
    pattern = regex.compile(r"(?i)(?=(birth|date|ssn))")
    sample = "birthdate DATE ssn"
    def result(concurrent):
        return [(m.span(), m.group(1)) for m in
                pattern.finditer(sample, overlapped=True, concurrent=concurrent, timeout=0.2)]
    assert result(True) == result(False)


def test_simultaneous_immutable_scans_keep_identical_finding_positions():
    word = regex.compile(r"(?i)\b(?:DOB|SSN|Phone)\b")
    sample = "DOB: 1990-10-12; SSN: 123-45-6789; Phone: 202-555-0147"
    expected = [(m.start(), m.end()) for m in word.finditer(sample, concurrent=False)]
    def scan(_):
        return [(m.start(), m.end()) for m in word.finditer(
            sample, concurrent=True, timeout=0.2)]
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert all(result == expected for result in executor.map(scan, range(16)))
