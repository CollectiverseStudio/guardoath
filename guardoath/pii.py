"""Offline sensitive-data checks: GuardOath email, card and SSN rules plus the copied
privacy pattern code, with bounded scanning and opt-in transformations."""

import asyncio
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from threading import BoundedSemaphore, Event
import json
from time import monotonic

from guardoath.json_data import parse_json
from guardoath.privacy_catalog import (CONTEXT_BOOST, CONTEXT_SCORE_FLOOR, CONTEXT_WORDS_BEFORE,
                                       CORE_ENTITIES, PATTERN_ENTITIES)
from guardoath.sensitive_fields import SensitiveFieldLimit, field_spans, label_type
from guardoath.models import Action, CheckerResult, Finding, Stage
from guardoath.privacy_digit_prefilter import minimum_decimal_digits

LABELED_FIELD = "labeled_field"
PATTERN = "pattern"
# Which detections win when two of them cover the same characters (see resolve_overlaps).
OWN_TIER = 0       # GuardOath's own checks: email, card, SSN and labeled fields
PATTERN_TIER = 1   # the copied privacy pattern checks

# PII recognizers run in a small, shared, dedicated local pool instead of asyncio's
# default pool, which is also used by other checkers and API work. A saturated
# default pool must not consume the entire 500-ms privacy scan/recheck deadline
# merely waiting in another component's queue. Four workers bound CPU pressure;
# timed-out scans still enforce their own monotonic deadline and fail closed.
_PII_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="guardoath-pii")
# ThreadPoolExecutor's normal work queue is unbounded. Do not accept a scan that
# will simply wait behind all four occupied workers and burn the shipped 500-ms
# per-checker deadline. Count running AND pending work; a denied submission
# returns a private incomplete result via the existing Engine failure path.
_PII_SLOTS = BoundedSemaphore(4)


class PiiCapacityExceeded(Exception):
    """Local PII capacity is occupied; never interpret this as a clean scan."""


def _warm_pii_workers():
    """Prime exactly the existing four bounded workers, without tenant data.

    Release every probe before awaiting completion so competing first-time
    checker constructors cannot deadlock while sharing the same executor.
    All of this is outside the per-request monotonic deadline and does not
    acquire any of the four _PII_SLOTS used by real scans.
    """
    release = Event()
    try:
        probes = tuple(_PII_EXECUTOR.submit(release.wait) for _ in range(4))
    finally:
        release.set()
    for probe in probes:
        probe.result()



async def _bounded_local_scan(scanner, *args):
    """Execute a PII scan with bounded admission and cancellation-safe release.

    Release the slot when the underlying concurrent Future finishes, NOT when
    an awaiting asyncio Task is cancelled: wait_for() may time out while the
    worker is still running. The worker is responsible for respecting the
    existing monotonic per-scan deadline.
    """
    if not _PII_SLOTS.acquire(blocking=False):
        raise PiiCapacityExceeded
    try:
        future = _PII_EXECUTOR.submit(scanner, *args)
    except Exception:
        _PII_SLOTS.release()
        raise
    future.add_done_callback(lambda _done: _PII_SLOTS.release())
    return await asyncio.wrap_future(future)


def resolve_overlaps(candidates, check_deadline=lambda: None):
    """Return the candidates that are reported, as (start, end, entity, source) tuples.

    candidates are (start, end, entity, source, tier, score). One run of characters is
    reported once, by the most specific check, following the copied analyzer's rules:
    - GuardOath's own detections are always kept, as before the pattern checks existed.
    - A pattern detection whose span lies within (or equals) a GuardOath detection's
      span is dropped: the characters are already reported, by a more specific check.
    - Between pattern detections the copied analyzer's duplicate rule applies across
      types: a detection contained in another one with an equal or higher score is
      dropped. On identical spans the higher score wins; equal scores keep both, since
      nothing tells the two types apart (redaction then labels the span SENSITIVE).
    Partial overlaps are kept. Every dropped span lies inside a kept span, so redaction
    still covers exactly the same characters."""
    # Most short sensitive-field scans have only the own recognizers. Avoid the
    # pattern overlap graph and its repeated containment walks in that case.
    # Preserve first-occurrence ordering and deduplicate the same source/span
    # exactly as the general best-map path does; never discard distinct own labels.
    if not any(tier == PATTERN_TIER for _, _, _, _, tier, _ in candidates):
        return list(dict.fromkeys((start, end, entity, source)
                                  for start, end, entity, source, tier, _ in candidates
                                  if tier == OWN_TIER))
    own = sorted({(start, end) for start, end, _entity, _source, tier, _score in candidates if tier == OWN_TIER})
    best = {}
    for start, end, entity, source, tier, score in candidates:
        key = (start, end, entity, source, tier)
        best[key] = max(score, best.get(key, score))
    kept = [(start, end, entity, source) for (start, end, entity, source, tier) in best if tier == OWN_TIER]
    patterns = []
    own_index, own_end = 0, -1
    for (start, end, entity, source, tier), score in sorted(
            ((key, score) for key, score in best.items() if key[4] == PATTERN_TIER),
            key=lambda item: (item[0][0], -item[0][1])):
        check_deadline()
        while own_index < len(own) and own[own_index][0] <= start:
            own_end = max(own_end, own[own_index][1])
            own_index += 1
        if own_end >= end:
            continue
        patterns.append((start, end, entity, source, score))
    top = {}
    for start, end, _entity, _source, score in patterns:
        top[(start, end)] = max(score, top.get((start, end), score))
    # Sorted by start, longer first: every span that can contain the current one comes first.
    containers = []
    for start, end, entity, source, score in sorted(patterns, key=lambda item: (item[0], -item[1])):
        check_deadline()
        if score < top[(start, end)]:
            continue
        # As in the copied analyzer, only spans that still reach this start can contain it.
        containers = [item for item in containers if item[1] >= start]
        if any(other_end >= end and (other_start, other_end) != (start, end) and other_score >= score
               for other_start, other_end, other_score in containers):
            continue
        kept.append((start, end, entity, source))
        containers.append((start, end, score))
    return kept


def _assemble_redacted_text(text, merged):
    """Build redacted content in one pass from sorted, nonoverlapping spans.

    The caller has already resolved candidates, overlap precedence and labels.
    Copying all unaffected fragments once preserves the exact previous
    reverse-splice result without repeatedly copying the entire input for
    every matched sensitive value.
    """
    fragments = []
    cursor = 0
    for start, end, entity in merged:
        fragments.append(text[cursor:start])
        fragments.append(f"[REDACTED_{entity}]")
        cursor = end
    fragments.append(text[cursor:])
    return "".join(fragments)


def _context_cue_matches_at(words, ends, start, hint_words, context):
    """Match the same five preceding words, current word and field hint as the
    original linear lookup, without walking the entire token list per finding.

    Ends are monotonically sorted with words. The first token whose end is
    strictly greater than start is the original context anchor; bisect_right
    retains that exact boundary behavior for overlapping spans. All tokens
    stay local to a scanned field, never cached between users.
    """
    index = bisect_right(ends, start)
    window = tuple(word for _, _, word in words[max(0, index - CONTEXT_WORDS_BEFORE):index + 1])
    window += tuple(hint_words)
    return any(cue in word for cue in context for word in window)


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


def _last_lookahead_start(pattern, text, *, timeout=None):
    """Return the last overlapping lookahead start using constant extra space.

    A split lookahead needs only the rightmost matching start to decide which
    core matches remain eligible. Collecting every match in a list may allocate
    memory proportional to a large repeated text under the shipped PII budget.
    Iteration preserves the same regex ordering, flags, timeout, and result.
    """
    rightmost = -1
    for match in pattern.finditer(text, overlapped=True, timeout=timeout, concurrent=True):
        rightmost = match.start()
    return rightmost


def _bounded_split_lookahead_matches(core, lookaheads, text, check_deadline):
    """Yield only core matches eligible under every original forward lookahead.

    A missing mandatory lookahead makes a match impossible: do not scan core.
    Once core starts exceed the rightmost eligible start, later starts cannot
    qualify either. Do not slice the input: a valid core match can extend past
    the lookahead's final start. Retain regex ordering and the caller's deadline.
    """
    last_eligible = None
    for lookahead in lookaheads:
        last = _last_lookahead_start(lookahead, text, timeout=check_deadline())
        if last < 0:
            return
        last_eligible = last if last_eligible is None else min(last_eligible, last)
    if last_eligible is None:
        return
    for match in core.finditer(text, timeout=check_deadline(), concurrent=True):
        if match.start() > last_eligible:
            break
        yield match


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


@lru_cache(maxsize=1)
def build_pii_checker():
    """Build the immutable local recognizer catalog once per worker process.

    Each invocation previously reconstructed and compiled the full recognizer
    set, warmed phone metadata, and rebuilt a public-suffix extractor. That
    work competes for CPU with the same process's strictly bounded 500-ms
    scans, notably when applications/tenants repeatedly build Engines.
    Request content, tenant policy, disabled entities, active rule actions,
    deadlines, transformed text and findings remain invocation-local inside
    scan(); none are cached. The worker pool and per-scan deadlines remain
    unchanged. This reduces avoidable startup contention, not OS scheduling
    tail guarantees.
    """
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
    patterns = tuple((recognizer, regex.compile(pattern.regex, recognizer.global_regex_flags),
                      minimum_decimal_digits(pattern.regex, verbose=bool(recognizer.global_regex_flags & regex.VERBOSE)))
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
             split_lookaheads(pattern.regex, recognizer.global_regex_flags, regex),
             minimum_decimal_digits(pattern.regex, verbose=bool(recognizer.global_regex_flags & regex.VERBOSE)))
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
    # Warm every bounded privacy worker during checker initialization, outside
    # the 500-ms request window. The first concurrent caller otherwise pays
    # for three OS thread starts after a previous single-worker warm-up.
    # Event-held jobs force the executor to create all four workers before
    # releasing them; they contain no tenant input and consume no scan slots.
    _warm_pii_workers()
    checker_version = f"privacy-patterns-{SOURCE_VERSION}/guardoath-patterns-7"

    # regex's concurrent=True releases the GIL while scanning immutable input strings.
    # This permits independent PII workers to progress instead of serially holding
    # the interpreter lock. It does NOT change patterns, scores, spans, entity
    # selection, deadlines or required redaction rechecks.
    def scan(request, redact, policy, stage):
        # The policy timeout is the budget for this checker; the scan stops there too.
        deadline = monotonic() + policy.timeout_ms / 1000
        entities = {}
        field_budget = [0]
        # Bounded, request-local memo for fields that proved clean. Repeated
        # ordinary JSON leaves need not run every recognizer again; never
        # reuse positives, transformed values, or labeled-field budget work.
        clean_texts = set()
        sensitive_key = False
        disabled = set(policy.disabled_privacy_entities)
        # Types whose rule the tenant turned off (rule_actions) are not looked for: never reported or redacted.
        off = {rule_id.removeprefix("pii.").upper() for rule_id, action in policy.rule_actions_for(stage).items()
               if action == "off" and rule_id.startswith("pii.")}
        active = [item for item in extra if item[0] not in disabled | off] if policy.privacy_patterns else []
        minimum = policy.privacy_min_score

        def check_deadline():
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("Local scan deadline")
            return remaining

        def process_text(text, hint=""):
            check_deadline()
            # Labeled fields consume a document-wide 64-candidate budget. Never
            # memoize them (or text containing possible label assignments):
            # each occurrence must still count and fail closed at the limit.
            can_reuse = (len(text) <= 256 and
                         (not policy.detect_labeled_sensitive_data or
                          (not label_type(hint) and ":" not in text and "=" not in text)))
            cache_key = (text, hint) if can_reuse else None
            if cache_key is not None and cache_key in clean_texts:
                return text, False
            candidates = []

            def found(entity, source, tier, start, end, score=1.0):
                if entity not in off:
                    candidates.append((start, end, entity, source, tier, score))
            # A proof that a pattern consumes N decimal digits permits only
            # an impossible-match skip. All other patterns still run normally.
            digit_count = sum(character.isdigit() for character in text)
            for recognizer, pattern, required_digits in patterns:
                # A tenant's stage-specific Off rule already suppresses this
                # recognizer's finding. Do not spend the bounded 500-ms scan
                # validating candidates that can never become findings. Keep
                # every enabled core detector and copied-pattern pass intact.
                if recognizer.supported_entities[0] in off:
                    continue
                if digit_count < required_digits:
                    continue
                remaining = check_deadline()
                for match in pattern.finditer(text, timeout=remaining, concurrent=True):
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
                    found(entity, PATTERN, OWN_TIER, match.start(), match.end())
            if active:
                words = None
                word_ends = None
                hint_words = None

                def supported_by_context(start, context):
                    # Up to five preceding words, the current word, and JSON key
                    # hints. Index and tokenize only once per inspected field.
                    nonlocal words, word_ends, hint_words
                    if words is None:
                        words = [(m.start(), m.end(), m.group().lower())
                                 for m in word_pattern.finditer(text, timeout=check_deadline(), concurrent=True)]
                        word_ends = [end for _, end, _ in words]
                    if hint_words is None:
                        hint_words = [m.group().lower()
                                      for m in word_pattern.finditer(hint, timeout=check_deadline(), concurrent=True)]
                    return _context_cue_matches_at(words, word_ends, start, hint_words, context)

                def keep(score, start, context):
                    """The final score when it reaches the policy minimum, else None."""
                    if score <= 0:
                        return None
                    if score < 1 and context and supported_by_context(start, context):
                        score = min(max(score + CONTEXT_BOOST, CONTEXT_SCORE_FLOOR), 1.0)
                    return score if score >= minimum else None

                for entity, parts, custom, compiled, context, phone_digits in active:
                    remaining = check_deadline()
                    if phone_digits and digit_count < phone_digits:
                        continue
                    if custom:
                        for part in parts:
                            check_deadline()
                            for result in part.analyze(text, [entity]):
                                kept_score = keep(result.score, result.start, context)
                                if kept_score is not None:
                                    found(entity, PATTERN, PATTERN_TIER, result.start, result.end, kept_score)
                        check_deadline()
                        continue
                    recognizer = parts[0]
                    for pattern, base_score, shortest, split, required_digits in compiled:
                        if len(text) < shortest or digit_count < required_digits:
                            continue
                        remaining = check_deadline()
                        if split:
                            core, lookaheads = split
                            matches = _bounded_split_lookahead_matches(
                                core, lookaheads, text, check_deadline)
                        else:
                            matches = pattern.finditer(text, timeout=remaining, concurrent=True)
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
                            kept_score = keep(score, match.start(), context)
                            if kept_score is not None:
                                found(entity, PATTERN, PATTERN_TIER, match.start(), match.end(), kept_score)
            if policy.detect_labeled_sensitive_data:
                for start, end, entity in field_spans(text, hint, deadline, field_budget):
                    found(entity, LABELED_FIELD, OWN_TIER, start, end)
            spans = []
            for start, end, entity, source in resolve_overlaps(candidates, check_deadline):
                entities.setdefault(entity, set()).add(source)
                spans.append((start, end, entity))
            if not redact or not spans:
                if not spans and cache_key is not None and len(clean_texts) < 256:
                    clean_texts.add(cache_key)
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
            return _assemble_redacted_text(text, merged), True

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
            entities, transformed = await _bounded_local_scan(
                scan, request, action == Action.REDACT, policy, stage,
            )
        except TimeoutError:
            # A worker-local monotonic deadline is distinct from the Engine\'s
            # outer asyncio.wait_for timeout. Both remain error/incomplete and
            # follow the tenant\'s fail-closed policy; never echo exception text.
            return CheckerResult(checker_id="pii", checker_version=checker_version,
                                 status="error", error_code="pii_scan_deadline")
        except PiiCapacityExceeded:
            return CheckerResult(checker_id="pii", checker_version=checker_version,
                                 status="error", error_code="pii_capacity_exceeded")
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
