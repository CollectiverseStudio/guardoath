"""Offline sensitive-data checks: GuardOath email, card and SSN rules plus the copied
privacy pattern code, with bounded scanning and opt-in transformations."""

import asyncio
import json
from time import monotonic

from guardoath.json_data import parse_json
from guardoath.privacy_catalog import (CONTEXT_BOOST, CONTEXT_SCORE_FLOOR, CONTEXT_WORDS_BEFORE,
                                       CORE_ENTITIES, PATTERN_ENTITIES)
from guardoath.sensitive_fields import SensitiveFieldLimit, field_spans
from guardoath.models import Action, CheckerResult, Finding, Stage

LABELED_FIELD = "labeled_field"
PATTERN = "pattern"


def shortest_match(expression):
    """Fewest characters a pattern can match; 0 if unknown. Used only to skip texts that are
    too short to match, which cannot change a result."""
    try:
        from re import _parser
        return max(0, _parser.parse(expression).getwidth()[0])
    except Exception:
        return 0


LOOKAHEAD_PREFIX = r"^\\b\(((?:\(\?=\.\*\?(?:[^()\\]|\\.)+\))+)(.+)\)\\b$"


def split_lookaheads(expression, flags, regex):
    """Patterns shaped like \\b((?=.*?A)(?=.*?B)CORE)\\b, compiled with DOTALL, test their
    lookaheads against the whole rest of the text from every position, which is quadratic
    on long input. Each lookahead holds at a position exactly when a match of A starts there
    or later, so it is checked against the last such start instead. Returns (core pattern,
    lookahead patterns) or None to scan the pattern as written."""
    if not flags & regex.DOTALL:
        return None
    shape = regex.fullmatch(LOOKAHEAD_PREFIX, expression)
    if not shape:
        return None
    lookaheads = regex.findall(r"\(\?=\.\*\?((?:[^()\\]|\\.)+)\)", shape.group(1))
    try:
        return (regex.compile(r"\b(" + shape.group(2) + r")\b", flags),
                tuple(regex.compile(item, flags) for item in lookaheads))
    except regex.error:
        return None


def fewest_phone_digits(recognizer):
    """Fewest digits any number of the recognizer's regions can have, from phone metadata;
    0 for other recognizers. Texts with fewer digits cannot contain a match."""
    regions = getattr(recognizer, "supported_regions", None)
    if not regions:
        return 0
    try:
        import phonenumbers
        lengths = []
        for region in regions:
            description = phonenumbers.PhoneMetadata.metadata_for_region(region).general_desc
            lengths += list(description.possible_length)
            if not recognizer.leniency:
                lengths += list(description.possible_length_local_only or ())
        return min((length for length in lengths if length > 0), default=0)
    except Exception:
        return 0


def build_pii_checker():
    try:
        import regex
        import tldextract
        from guardoath.privacy_patterns import (RECOGNIZER_CLASSES, SOURCE_VERSION, CreditCardRecognizer,
                                                EmailRecognizer, PatternRecognizer, UsSsnRecognizer)
    except ImportError:
        raise ValueError("Install guardoath[pii] (tldextract, phonenumbers) to enable local sensitive-data checks") from None

    extractor = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)
    extractor("example.com")

    class OfflineEmailRecognizer(EmailRecognizer):
        def validate_result(self, pattern_text):
            return extractor(pattern_text).fqdn != ""

    recognizers = (OfflineEmailRecognizer(), CreditCardRecognizer(), UsSsnRecognizer())
    patterns = tuple((recognizer, regex.compile(pattern.regex, recognizer.global_regex_flags))
                     for recognizer in recognizers for pattern in recognizer.patterns)
    context_pattern = regex.compile(r"(?i)\b(?:ssns?|social[\s_-]*security(?:[\s_-]*number)?)\b")

    # The other copied recognizers. Plain pattern recognizers are scanned here with the
    # request deadline; recognizers with their own analyze() (IBAN, phone numbers) run it.
    core_classes = (EmailRecognizer, CreditCardRecognizer, UsSsnRecognizer)
    extra = []
    for recognizer_class in RECOGNIZER_CLASSES:
        if recognizer_class in core_classes:
            continue
        recognizer = recognizer_class()
        entity = recognizer.supported_entities[0]
        if entity not in PATTERN_ENTITIES:
            raise ValueError("Copied privacy recognizer without a catalog entry")
        custom = getattr(type(recognizer), "analyze") is not getattr(PatternRecognizer, "analyze")
        compiled = () if custom else tuple(
            (regex.compile(pattern.regex, recognizer.global_regex_flags), pattern.score, shortest_match(pattern.regex),
             split_lookaheads(pattern.regex, recognizer.global_regex_flags, regex))
            for pattern in recognizer.patterns)
        # One phone recognizer per region, so the request deadline is checked between regions.
        regions = getattr(recognizer, "supported_regions", ()) if custom else ()
        parts = (tuple(type(recognizer)(supported_regions=(region,), leniency=recognizer.leniency)
                       for region in regions) if len(regions) > 1 else (recognizer,))
        context = tuple(word.lower() for word in (recognizer.context or ()))
        extra.append((entity, parts, custom, compiled, context, fewest_phone_digits(recognizer)))
    if {entity for entity, *_ in extra} != set(PATTERN_ENTITIES):
        raise ValueError("Privacy pattern catalog does not match the copied recognizers")
    # Load phone-number metadata now, so the first request does not spend its scan time on it.
    for entity, parts, custom, _compiled, _context, _digits in extra:
        if custom:
            for part in parts:
                part.analyze("Call +1 202-555-0147 or 082 123 4567", [entity])
    word_pattern = regex.compile(r"\w+")
    checker_version = f"privacy-patterns-{SOURCE_VERSION}/guardoath-patterns-4"

    def scan(request, redact, policy):
        # The policy timeout is the budget for this checker; the scan stops there too.
        deadline = monotonic() + policy.timeout_ms / 1000
        entities = {}
        field_budget = [0]
        sensitive_key = False
        disabled = set(policy.disabled_privacy_entities)
        active = [item for item in extra if item[0] not in disabled] if policy.privacy_patterns else []
        minimum = policy.privacy_min_score

        def check_deadline():
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("Local scan deadline")
            return remaining

        def found(entity, source, spans, start, end):
            entities.setdefault(entity, set()).add(source)
            spans.append((start, end, entity))

        def process_text(text, hint=""):
            spans = []
            for recognizer, pattern in patterns:
                remaining = check_deadline()
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
                    found(entity, PATTERN, spans, match.start(), match.end())
            if active:
                words = None

                def supported_by_context(start, context):
                    # Up to five words before the value plus the word where it starts,
                    # and the JSON field name; a context word may appear inside a word.
                    nonlocal words
                    if words is None:
                        words = [(m.start(), m.end(), m.group().lower())
                                 for m in word_pattern.finditer(text, timeout=check_deadline())]
                    index = next((i for i, (_, end, _) in enumerate(words) if end > start), len(words))
                    window = [word for _, _, word in words[max(0, index - CONTEXT_WORDS_BEFORE):index + 1]]
                    window += [m.group().lower() for m in word_pattern.finditer(hint)]
                    return any(cue in word for cue in context for word in window)

                def keep(score, start, context):
                    if score <= 0:
                        return False
                    if score < 1 and context and supported_by_context(start, context):
                        score = min(max(score + CONTEXT_BOOST, CONTEXT_SCORE_FLOOR), 1.0)
                    return score >= minimum

                digits = sum(c.isdigit() for c in text)
                for entity, parts, custom, compiled, context, phone_digits in active:
                    remaining = check_deadline()
                    if phone_digits and digits < phone_digits:
                        continue
                    if custom:
                        for part in parts:
                            check_deadline()
                            for result in part.analyze(text, [entity]):
                                if keep(result.score, result.start, context):
                                    found(entity, PATTERN, spans, result.start, result.end)
                        check_deadline()
                        continue
                    recognizer = parts[0]
                    for pattern, base_score, shortest, split in compiled:
                        if len(text) < shortest:
                            continue
                        remaining = check_deadline()
                        if split:
                            core, lookaheads = split
                            last = []
                            for lookahead in lookaheads:
                                starts = [m.start() for m in lookahead.finditer(text, overlapped=True, timeout=check_deadline())]
                                last.append(starts[-1] if starts else -1)
                            matches = (m for m in core.finditer(text, timeout=remaining) if m.start() <= min(last))
                        else:
                            matches = pattern.finditer(text, timeout=remaining)
                        for match in matches:
                            if monotonic() >= deadline:
                                raise TimeoutError("Local scan deadline")
                            candidate = match.group()
                            if candidate == "":
                                continue
                            score = base_score
                            validation = recognizer.validate_result(candidate)
                            if validation is not None:
                                score = 1.0 if validation else 0
                            if recognizer.invalidate_result(candidate):
                                score = 0
                            if keep(score, match.start(), context):
                                found(entity, PATTERN, spans, match.start(), match.end())
            if policy.detect_labeled_sensitive_data:
                for start, end, entity in field_spans(text, hint, deadline, field_budget):
                    found(entity, LABELED_FIELD, spans, start, end)
            if not redact or not spans:
                return text, bool(spans)
            # Merge overlapping spans to avoid preserving a fragment of a value. A span inside
            # a longer one keeps the longer span's label (an email address contains a domain);
            # on identical spans GuardOath's own labels win over pattern labels. Partial
            # overlaps of different types, and identical spans of two pattern types, become SENSITIVE.
            merged = []
            for start, end, entity in sorted(set(spans), key=lambda s: (s[0], -s[1], s[2] in PATTERN_ENTITIES, s[2])):
                if merged and start < merged[-1][1]:
                    old_start, old_end, old_entity = merged[-1]
                    if end <= old_end:
                        if ((start, end) == (old_start, old_end) and entity != old_entity
                                and old_entity in PATTERN_ENTITIES):
                            merged[-1] = (old_start, old_end, "SENSITIVE")
                        continue
                    merged[-1] = (old_start, end, old_entity if old_entity == entity else "SENSITIVE")
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

    labeled_explanations = {
        "PHONE_NUMBER": "Potential phone number in an explicitly labeled field; syntax only, ownership and reachability not verified.",
        "DATE_OF_BIRTH": "Potential birth date in an explicitly labeled field; calendar syntax only, identity and actual birth not verified.",
        "MEDICAL_RECORD_NUMBER": "Potential medical record identifier in an explicitly labeled field; not verified with any healthcare system.",
        "HEALTH_PLAN_ID": "Potential health-plan member identifier in an explicitly labeled field; membership and PHI status not verified.",
        "BANK_ACCOUNT": "Potential bank account number in an explicitly labeled field; account existence and ownership not verified.",
    }
    pattern_explanations = {
        "CREDIT_CARD": "Potential credit-card number detected by local pattern and checksum checks.",
        "EMAIL_ADDRESS": "Potential email address detected by local pattern and domain checks.",
        "US_SSN": "Potential U.S. Social Security number detected by pattern, context, and invalid-value checks; issuance was not verified.",
        "PHONE_NUMBER": "Potential phone number detected by local number-format and numbering-plan checks; ownership and reachability not verified.",
    }
    for entity, (description, _severity) in PATTERN_ENTITIES.items():
        pattern_explanations.setdefault(entity, f"Potential {description} detected by local privacy pattern checks "
                                                "(pattern, validation where available, and nearby context words); "
                                                "not verified with any issuer.")
    severities = {"EMAIL_ADDRESS": "medium", "CREDIT_CARD": "high", "US_SSN": "high",
                  **{entity: severity for entity, (_description, severity) in PATTERN_ENTITIES.items()},
                  **{entity: "high" for entity in labeled_explanations}}
    if set(CORE_ENTITIES) - set(pattern_explanations):
        raise ValueError("Missing explanation for a core entity")

    def finding(entity, sources):
        # A labeled-field detection keeps its established explanation; one finding per entity.
        explanation = labeled_explanations[entity] if LABELED_FIELD in sources else pattern_explanations[entity]
        return Finding(rule_id=f"pii.{entity.lower()}", category="privacy",
                       severity=severities[entity], explanation=explanation)

    async def run(request, policy, stage):
        rule = next((r for r in policy.rules if r.checker_id == "pii" and stage in r.stages), None)
        action = ((rule.on_input_finding if stage == Stage.INPUT else rule.on_output_finding) or rule.on_finding) if rule else None
        try:
            entities, transformed = await asyncio.to_thread(scan, request, action == Action.REDACT, policy)
        except SensitiveFieldLimit:
            return CheckerResult(checker_id="pii", checker_version=checker_version,
                                 status="error", error_code="pii_field_limit")
        except (ValueError, RecursionError):
            return CheckerResult(checker_id="pii", checker_version=checker_version,
                                 status="error", error_code="invalid_json_for_pii")
        findings = tuple(finding(entity, entities[entity]) for entity in sorted(entities))
        return CheckerResult(checker_id="pii", checker_version=checker_version,
                             status="flagged" if findings else "passed", findings=findings,
                             transformed_content=transformed)

    setattr(run, "checker_version", checker_version)
    return run
