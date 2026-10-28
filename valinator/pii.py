"""Bounded local pattern checks using Presidio patterns and validators.

No AnalyzerEngine, NLP model downloads, outbound requests, or source-text findings.
This deliberately supports only email addresses and credit-card candidates.
"""

import asyncio
from importlib.metadata import version
import sys
from time import monotonic

from valinator.json_data import parse_json
from valinator.models import CheckerResult, Finding


def build_pii_checker():
    if sys.version_info >= (3, 15):
        raise ValueError("Local PII checks require Python 3.11 through 3.14")
    try:
        import regex
        import tldextract
        from presidio_analyzer.predefined_recognizers import EmailRecognizer, CreditCardRecognizer
    except ImportError:
        raise ValueError("Install valinator[pii] to enable local sensitive-data checks") from None

    package_version = version("presidio-analyzer")
    extractor = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)
    # Load the bundled suffix snapshot at startup, outside the request deadline.
    extractor("example.com")

    class OfflineEmailRecognizer(EmailRecognizer):
        def validate_result(self, pattern_text):
            return extractor(pattern_text).fqdn != ""

    recognizers = (OfflineEmailRecognizer(), CreditCardRecognizer())
    patterns = tuple((recognizer, regex.compile(pattern.regex, recognizer.global_regex_flags))
                     for recognizer in recognizers for pattern in recognizer.patterns)
    checker_version = f"presidio-{package_version}/valinator-patterns-1"

    def segments(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from segments(item)
        elif isinstance(value, list):
            for item in value:
                yield from segments(item)
        elif isinstance(value, int) and not isinstance(value, bool):
            yield str(value)

    def scan(request):
        deadline = monotonic() + 0.2
        texts = (request.content,) if request.content_format == "text" else segments(parse_json(request.content))
        entities = set()
        for text in texts:
            for recognizer, pattern in patterns:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("Local scan deadline")
                # Presidio's general analyze path skips patterns on regex timeout.
                # Drive its patterns directly so timeout is a failed check, not a pass.
                for match in pattern.finditer(text, timeout=remaining):
                    if monotonic() >= deadline:
                        raise TimeoutError("Local scan deadline")
                    if recognizer.validate_result(match.group()) is True:
                        entities.add(recognizer.supported_entities[0])
        return entities

    async def run(request, policy, stage):
        try:
            entities = await asyncio.to_thread(scan, request)
        except (ValueError, RecursionError):
            return CheckerResult(checker_id="pii", checker_version=checker_version,
                                 status="error", error_code="invalid_json_for_pii")
        findings = tuple(Finding(
            rule_id=f"pii.{entity.lower()}", category="privacy",
            severity="high" if entity == "CREDIT_CARD" else "medium",
            explanation=("Potential credit-card number detected by local pattern and checksum checks."
                         if entity == "CREDIT_CARD" else "Potential email address detected by local pattern and domain checks."),
        ) for entity in sorted(entities))
        return CheckerResult(checker_id="pii", checker_version=checker_version,
                             status="flagged" if findings else "passed", findings=findings)

    setattr(run, "checker_version", checker_version)
    return run
