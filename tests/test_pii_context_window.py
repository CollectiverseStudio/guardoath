"""Synthetic parity checks for the request-local PII context-word index.

The indexed lookup must keep the exact previous five-word/context/hint semantics,
including word-end boundaries and substring-based cues. No private data is stored.
"""
import re

import pytest

from guardoath.pii import _context_cue_matches_at
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
