"""Offline Presidio patterns with bounded scanning and opt-in transformations."""

import asyncio
from importlib.metadata import version
import json
import sys
from time import monotonic

from valinator.json_data import parse_json
from valinator.sensitive_fields import SensitiveFieldLimit, field_spans
from valinator.models import Action, CheckerResult, Finding, Stage


def build_pii_checker():
    if sys.version_info >= (3, 15):
        raise ValueError("Local PII checks require Python 3.11 through 3.14")
    try:
        import regex
        import tldextract
        from presidio_analyzer.predefined_recognizers import EmailRecognizer, CreditCardRecognizer, UsSsnRecognizer
    except ImportError:
        raise ValueError("Install valinator[pii] to enable local sensitive-data checks") from None

    package_version = version("presidio-analyzer")
    extractor = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)
    extractor("example.com")

    class OfflineEmailRecognizer(EmailRecognizer):
        def validate_result(self, pattern_text):
            return extractor(pattern_text).fqdn != ""

    recognizers = (OfflineEmailRecognizer(), CreditCardRecognizer(), UsSsnRecognizer())
    patterns = tuple((recognizer, regex.compile(pattern.regex, recognizer.global_regex_flags))
                     for recognizer in recognizers for pattern in recognizer.patterns)
    context_pattern = regex.compile(r"(?i)\b(?:ssns?|social[\s_-]*security(?:[\s_-]*number)?)\b")
    checker_version = f"presidio-{package_version}/valinator-patterns-3"

    def scan(request, redact, labeled):
        deadline = monotonic() + 0.2
        entities = set()
        field_budget = [0]
        sensitive_key = False

        def process_text(text, hint=""):
            spans = []
            for recognizer, pattern in patterns:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("Local scan deadline")
                for match in pattern.finditer(text, timeout=remaining):
                    if monotonic() >= deadline:
                        raise TimeoutError("Local scan deadline")
                    entity = recognizer.supported_entities[0]
                    candidate = match.group()
                    if entity == "US_SSN":
                        digits = "".join(c for c in candidate if c.isdigit())
                        if len(digits) != 9 or int(digits[:3]) >= 900 or recognizer.invalidate_result(candidate):
                            continue
                        standard = regex.fullmatch(r"[0-9]{3}-[0-9]{2}-[0-9]{4}", candidate)
                        nearby = hint + " " + text[max(0, match.start()-40):match.start()] + " " + text[match.end():match.end()+40]
                        if not standard and not context_pattern.search(nearby, timeout=.01):
                            continue
                    elif recognizer.validate_result(candidate) is not True:
                        continue
                    entities.add(entity)
                    spans.append((match.start(), match.end(), entity))
            if labeled:
                extra = field_spans(text, hint, deadline, field_budget)
                entities.update(entity for _, _, entity in extra)
                spans.extend(extra)
            if not redact or not spans:
                return text, bool(spans)
            # Merge overlapping spans to avoid preserving a fragment of a value.
            merged = []
            for start, end, entity in sorted(set(spans)):
                if merged and start < merged[-1][1]:
                    old_start, old_end, old_entity = merged.pop()
                    merged.append((old_start, max(old_end, end), old_entity if old_entity == entity else "SENSITIVE"))
                else:
                    merged.append((start, end, entity))
            for start, end, entity in reversed(merged):
                text = text[:start] + f"[REDACTED_{entity}]" + text[end:]
            return text, True

        def transform(value, hint=""):
            nonlocal sensitive_key
            if isinstance(value, str):
                return process_text(value, hint)[0]
            if isinstance(value, dict):
                result = {}
                for key, item in value.items():
                    _, detected = process_text(key)
                    sensitive_key = sensitive_key or detected
                    result[key] = transform(item, key)
                return result
            if isinstance(value, list):
                return [transform(item, hint) for item in value]
            if isinstance(value, int) and not isinstance(value, bool):
                text, detected = process_text(str(value), hint)
                return text if redact and detected else value
            return value

        if request.content_format == "text":
            transformed, _ = process_text(request.content)
        else:
            transformed = json.dumps(transform(parse_json(request.content)), ensure_ascii=False, separators=(",", ":"))
        # Renaming JSON keys can cause collisions or change schema meaning.
        # Do not release content containing a sensitive key.
        return entities, transformed if redact and entities and not sensitive_key else None

    async def run(request, policy, stage):
        rule = next((r for r in policy.rules if r.checker_id == "pii" and stage in r.stages), None)
        action = ((rule.on_input_finding if stage == Stage.INPUT else rule.on_output_finding) or rule.on_finding) if rule else None
        try:
            entities, transformed = await asyncio.to_thread(scan, request, action == Action.REDACT, policy.detect_labeled_sensitive_data)
        except SensitiveFieldLimit:
            return CheckerResult(checker_id="pii", checker_version=checker_version,
                                 status="error", error_code="pii_field_limit")
        except (ValueError, RecursionError):
            return CheckerResult(checker_id="pii", checker_version=checker_version,
                                 status="error", error_code="invalid_json_for_pii")
        explanations = {
            "PHONE_NUMBER": "Potential phone number in an explicitly labeled field; syntax only, ownership and reachability not verified.",
            "DATE_OF_BIRTH": "Potential birth date in an explicitly labeled field; calendar syntax only, identity and actual birth not verified.",
            "MEDICAL_RECORD_NUMBER": "Potential medical record identifier in an explicitly labeled field; not verified with any healthcare system.",
            "HEALTH_PLAN_ID": "Potential health-plan member identifier in an explicitly labeled field; membership and PHI status not verified.",
            "BANK_ACCOUNT": "Potential bank account number in an explicitly labeled field; account existence and ownership not verified.",
            "CREDIT_CARD": "Potential credit-card number detected by local pattern and checksum checks.",
            "EMAIL_ADDRESS": "Potential email address detected by local pattern and domain checks.",
            "US_SSN": "Potential U.S. Social Security number detected by pattern, context, and invalid-value checks; issuance was not verified.",
        }
        findings = tuple(Finding(rule_id=f"pii.{entity.lower()}", category="privacy",
                                severity="medium" if entity == "EMAIL_ADDRESS" else "high",
                                explanation=explanations[entity]) for entity in sorted(entities))
        return CheckerResult(checker_id="pii", checker_version=checker_version,
                             status="flagged" if findings else "passed", findings=findings,
                             transformed_content=transformed)

    setattr(run, "checker_version", checker_version)
    return run
