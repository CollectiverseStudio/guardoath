"""Synthetic parity checks for the request-local PII context-word index.

The indexed lookup must keep the exact previous five-word/context/hint semantics,
including word-end boundaries and substring-based cues. No private data is stored.
"""
import re

import pytest

from guardoath.pii import (_context_cue_matches_at, _count_digits_with_deadline,
                         _context_tokens_with_deadline)
from guardoath.privacy_catalog import CONTEXT_WORDS_BEFORE


def _words(text):
    return [(match.start(), match.end(), match.group().lower())
            for match in re.finditer(r"\w+", text)]


def _linear_original(words, start, hint_words, cues):
    """Pre-optimization oracle, retained only in this independent regression."""
    index = next((i for i, (_begin, end, _word) in enumerate(words) if end > start),
                 len(words))
    window = [word for _, _, word
              in words[max(0, index - CONTEXT_WORDS_BEFORE):index + 1]]
    window += list(hint_words)
    return any(cue in word for cue in cues for word in window)


@pytest.mark.parametrize("text,hint,cues", [
    ("birth date 12/31/1990", "", ("birth", "birthday")),
    ("birth date 12/31/1990", "", ("not-present", "birth")),
    ("general note 12/31/1990", "date_of_birth", ("not-present", "birth")),
    ("general note 12/31/1990", "date_of_birth", ("birth",)),
    ("ignore this sample 12/31/1990", "", ("birth",)),
    ("alpha_beta gamma", "", ("alpha",)),
    ("Unicode geprüft 漢字 2026", "birth_date", ("birth",)),
    ("", "", ("birth",)),
    ("onlyone", "date field", ("date",)),
    ("alpha beta gamma", "", ()),
])
def test_indexed_context_matches_original_at_every_character_and_boundary(text, hint, cues):
    words = _words(text)
    ends = [end for _begin, end, _word in words]
    hints = [word.lower() for word in re.findall(r"\w+", hint)]
    for start in range(-1, len(text) + 2):
        assert _context_cue_matches_at(words, ends, start, hints, cues) == (
            _linear_original(words, start, hints, cues)
        )


def test_large_field_matches_original_with_many_candidate_positions():
    # Same result with thousands of tokens and multiple candidate hits. This
    # checks correctness, NOT a runner-dependent latency target.
    text = " ".join(["word"] * 2400 + ["birth", "12/31/1990"] + ["other"] * 2400)
    words = _words(text)
    ends = [end for _begin, end, _word in words]
    for start in [-1, 0, 150, len(text) // 2, len(text) - 1, len(text), len(text) + 1]:
        for cue in [("birth",), ("word",), ("other",), ("none",)]:
            assert _context_cue_matches_at(words, ends, start, [], cue) == (
                _linear_original(words, start, [], cue)
            )


def test_context_window_boundary_does_not_use_more_than_configured_prior_words():
    words = _words("birth " + " ".join(["neutral"] * (CONTEXT_WORDS_BEFORE + 3)))
    ends = [end for _, end, _ in words]
    assert _context_cue_matches_at(words, ends, words[1][0], [], ("birth",))
    assert not _context_cue_matches_at(words, ends, words[-1][0], [], ("birth",))
    assert _context_cue_matches_at(words, ends, words[-1][0], ["birth_date"], ("birth",))


def test_bounded_digit_prefilter_keeps_exact_unicode_isdigit_contract():
    # Preserve the original Unicode str.isdigit() predicate; this is NOT a
    # replacement with ASCII-only decimal recognition.
    texts = ["", "12345", "²٣４9", "abc Ⅳ ² ٤ 9", "🧪" * 2100 + "²٣４9" * 5000]
    for text in texts:
        checks = []
        actual = _count_digits_with_deadline(text, lambda: checks.append(True))
        assert actual == sum(character.isdigit() for character in text)
        if len(text) > 4096:
            assert len(checks) >= 2


def test_bounded_digit_prefilter_expires_before_finishing_wide_field():
    calls = 0
    class TrackedText(str):
        slices = 0
        def __getitem__(self, index):
            if isinstance(index, slice):
                self.slices += 1
            return super().__getitem__(index)

    text = TrackedText("3" * 32000)
    def deadline():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise TimeoutError("synthetic worker deadline")

    with pytest.raises(TimeoutError, match="synthetic worker deadline"):
        _count_digits_with_deadline(text, deadline)
    assert calls == 3
    assert text.slices == 2   # no partial verdict after a missed budget
    assert text.slices < 8


def test_bounded_context_index_preserves_original_token_positions_and_words():
    regex = pytest.importorskip("regex")
    pattern = regex.compile(r"\\w+")
    text = "First BIRTH alpha_2 " + ("token " * 3000) + "last"
    ticks = []
    actual = _context_tokens_with_deadline(pattern, text, lambda: ticks.append(True) or 10.0)
    assert actual == _words(text)
    assert len(ticks) >= 11
    ends = [end for _, end, _ in actual]
    for position in (0, 4, 10, len(text) // 2, len(text)):
        assert _context_cue_matches_at(actual, ends, position, [], ("birth",)) == (
            _linear_original(_words(text), position, [], ("birth",)))


def test_bounded_context_index_stops_at_deadline_without_partial_tokens():
    regex = pytest.importorskip("regex")
    calls = 0
    def deadline():
        nonlocal calls
        calls += 1
        if calls == 4:
            raise TimeoutError("synthetic token budget")
        return 10.0
    with pytest.raises(TimeoutError, match="synthetic token budget"):
        _context_tokens_with_deadline(regex.compile(r"\\w+"), "word " * 5000, deadline)
    assert calls == 4
