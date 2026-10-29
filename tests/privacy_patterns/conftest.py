"""Fixtures for the copied privacy pattern tests."""

import pytest


@pytest.fixture(scope="session")
def max_score() -> float:
    from guardoath.privacy_patterns import EntityRecognizer
    return EntityRecognizer.MAX_SCORE


class _NoNlpEngine:
    """Stand-in for the upstream spaCy fixture used by the phone tests.

    The copied phone recognizers ignore NLP artifacts, so ``None`` is passed in
    their place and the upstream test functions run unchanged without spaCy.
    """

    def process_text(self, text, language):
        return None


@pytest.fixture(scope="session")
def spacy_nlp_engine():
    return _NoNlpEngine()
