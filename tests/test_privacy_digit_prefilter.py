"""Soundness checks for the privacy scanner's non-authoritative regex prefilter.

A prefilter may skip only mathematically impossible matches. These tests
deliberately exercise patterns with an optional digit branch, mixed classes,
lookaheads, unicode digits and unsupported third-party regex syntax.
"""
import pytest
import regex

from guardoath.privacy_digit_prefilter import minimum_decimal_digits


@pytest.mark.parametrize("pattern,expected", [
    (r"\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b", 9),
    (r"\b4\d{3}[- ]?\d{12}\b", 16),
    (r"\d{2,4}", 2),
    (r"(?:\d{4}|\w+)", 0),
    (r"[A-Z0-9]{5}", 0),
    (r"(?=.*?[0-9]{4})[\w@#$]{10}", 0),
    (r"\b(?:GIR\s?0AA|[A-Z][0-9]{2})\b", 1),
    (r"\b(?:[0-9]{9}|[A-Z]{9})\b", 0),
    (r"\p{Lu}[0-9]{3}", 0),
    (r"(?<=\\w)[0-9]{2}", 2),
])
def test_minimum_consumed_digits_is_a_conservative_bound(pattern, expected):
    assert minimum_decimal_digits(pattern) == expected


@pytest.mark.parametrize("pattern,examples", [
    (r"\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b",
     ["Use @support to ask a question.", "999-12-3456", "No digits here."]),
    (r"\b(?:GIR\s?0AA|[A-Z][0-9]{2})\b",
     ["GIR 0AA", "ABC", "A22", "Hi @support"]),
    (r"\b4\d{3}[- ]?\d{12}\b",
     ["4111111111111111", "Use @support", "4" * 16]),
    (r"(?:\d{4}|\w+)",
     ["alpha", "1234", "@support", ""]),
    (r"(?=.*?[0-9]{4})[\w@#$]{10}",
     ["ABC1234DEF", "@support", "ABCD1234XX"]),
])
def test_impossible_match_filter_never_discards_an_actual_match(pattern, examples):
    minimum = minimum_decimal_digits(pattern)
    compiled = regex.compile(pattern)
    for content in examples:
        if sum(character.isdigit() for character in content) < minimum:
            assert compiled.search(content) is None


def test_unknown_regex_constructs_and_verbose_syntax_are_not_filtered():
    assert minimum_decimal_digits(r"\p{Lu}[0-9]{3}") == 0
    assert minimum_decimal_digits(r"[0-9]{3} # comment with 123", verbose=True) == 0
    assert minimum_decimal_digits(r"(?e)(?:[0-9]{3}){e<=1}") == 0


def test_unicode_digits_do_not_create_an_unsafe_ascii_assumption():
    # isdigit() counts at least the decimal characters covered by \d,
    # so unicode input cannot make a valid pattern match disappear.
    pattern = r"\d{3}"
    content = "１２３"
    assert minimum_decimal_digits(pattern) == 3
    assert sum(character.isdigit() for character in content) >= 3
    assert regex.search(pattern, content) is not None
