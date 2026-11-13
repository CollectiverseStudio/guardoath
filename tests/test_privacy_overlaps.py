"""One run of characters is reported once: the overlap rule of the sensitive-data scan.

GuardOath's own detections (email, card, SSN and labeled fields) are always kept. A copied
privacy pattern match lying within one of them is dropped; between pattern matches the
copied analyzer's duplicate rule decides (contained and not higher scored -> dropped).
"""
import asyncio
import base64
import json
import random

import pytest

pytest.importorskip("tldextract")
pytest.importorskip("phonenumbers")

from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Action, CheckRequest, Policy, Rule, Stage
from guardoath.pii import LABELED_FIELD, OWN_TIER, PATTERN, PATTERN_TIER, resolve_overlaps


@pytest.fixture(scope="module")
def checkers():
    return default_checkers(enable_pii=True)


def check(checkers, content, *, fmt="text", action=Action.FLAG, encoded=False, labeled=True, **settings):
    rules = (Rule(checker_id="pii", on_finding=action),)
    if encoded:
        rules += (Rule(checker_id="encoded_pii", on_finding=Action.FLAG),)
    policy = Policy(application_id="demo", version="v1", timeout_ms=500, rules=rules,
                    detect_labeled_sensitive_data=labeled, **settings)
    request = CheckRequest(application_id="demo", policy_version="v1", content=content, content_format=fmt)
    return asyncio.run(Engine([policy], checkers).check(request, Stage.INPUT))


def rule_ids(result):
    return {f.rule_id for f in result.findings}


# Each value is also matched by a pattern check inside the more specific detection's span.
DUPLICATES = [
    ("Email jane@example.com", "text", "jane@example.com", {"pii.email_address"}, "pii.url"),
    ("DOB: 1980-02-29", "text", "1980-02-29", {"pii.date_of_birth"}, "pii.date_time"),
    ("bank account: 76543210", "text", "76543210", {"pii.bank_account"}, "pii.us_bank_number"),
    ("member_id: PLAN-12345", "text", "PLAN-12345", {"pii.health_plan_id"}, "pii.us_health_insurance_member_id"),
    ("bank_account_number = 123456789012", "text", "123456789012", {"pii.bank_account"}, "pii.kr_driver_license"),
    ("phone_number: 2025550147", "text", "2025550147", {"pii.phone_number"}, "pii.za_income_tax_number"),
    ('{"date_of_birth": "1980-02-29"}', "json", "1980-02-29", {"pii.date_of_birth"}, "pii.date_time"),
    ('{"contact": "jane@example.com"}', "json", "jane@example.com", {"pii.email_address"}, "pii.url"),
]


@pytest.mark.parametrize("content,fmt,value,expected,inner", DUPLICATES)
def test_a_value_inside_a_more_specific_detection_is_reported_once(checkers, content, fmt, value, expected, inner):
    assert rule_ids(check(checkers, content, fmt=fmt)) == expected


@pytest.mark.parametrize("content,fmt,value,expected,inner", [d for d in DUPLICATES if d[4] != "pii.url"])
def test_the_pattern_still_matches_when_nothing_more_specific_covers_the_value(checkers, content, fmt, value, expected, inner):
    # Without labeled-field detection the same value is reported by the pattern check.
    assert inner in rule_ids(check(checkers, content, fmt=fmt, labeled=False))


def test_a_pattern_match_outside_other_detections_is_still_reported(checkers):
    assert rule_ids(check(checkers, "Contact jane@example.com or see https://example.org/help")) == {
        "pii.email_address", "pii.url"}
    assert rule_ids(check(checkers, "DOB: 1980-02-29\nVisit date: 2021-05-20")) == {
        "pii.date_of_birth", "pii.date_time"}


@pytest.mark.parametrize("content,fmt,value,expected,inner", DUPLICATES)
def test_redaction_still_masks_the_whole_value(checkers, content, fmt, value, expected, inner):
    result = check(checkers, content, fmt=fmt, action=Action.REDACT)
    assert result.redaction.status == "preview"
    label = next(iter(expected)).removeprefix("pii.").upper()
    expected_content = content.replace(value, "[REDACTED_" + label + "]")
    if fmt == "json":
        assert json.loads(result.redaction.content) == json.loads(expected_content)
    else:
        assert result.redaction.content == expected_content
    assert all(c.status == "passed" for c in result.redaction.validation_results)


def test_a_labeled_phone_value_keeps_its_own_label(checkers):
    # The labeled value also matches the South African income tax pattern. Before the overlap
    # rule the identical spans of the two types were redacted as SENSITIVE.
    result = check(checkers, "phone_number: 2025550147", action=Action.REDACT)
    assert result.redaction.content == "phone_number: [REDACTED_PHONE_NUMBER]"


def test_between_pattern_checks_the_higher_score_wins_on_the_same_characters(checkers):
    # A, D, M or T plus eight digits is a validated South African passport number (score 1.0)
    # and a weak U.S. passport match with context (0.45).
    result = check(checkers, "my travel document is A12803456", action=Action.REDACT)
    assert rule_ids(result) == {"pii.za_passport"}
    assert result.redaction.content == "my travel document is [REDACTED_ZA_PASSPORT]"
    # Equal scores keep both types; see test_identical_spans_of_two_pattern_types_are_labeled_sensitive.


def test_encoded_payloads_follow_the_same_rule(checkers):
    payload = base64.b64encode(b"Email jane@example.com").decode()
    assert rule_ids(check(checkers, "base64: " + payload, encoded=True)) == {"pii.encoded_email_address"}
    payload = b"DOB: 1980-02-29".hex()
    assert rule_ids(check(checkers, "hex: " + payload, encoded=True)) == {"pii.encoded_date_of_birth"}
    # The raw scan and the encoded scan give the same types for the same text.
    raw = rule_ids(check(checkers, "Email jane@example.com", encoded=True))
    assert {"pii.encoded_" + r.removeprefix("pii.") for r in raw} == {"pii.encoded_email_address"}


def own(start, end, entity="EMAIL_ADDRESS", source=PATTERN):
    return (start, end, entity, source, OWN_TIER, 1.0)


def pattern(start, end, entity, score):
    return (start, end, entity, PATTERN, PATTERN_TIER, score)


def kept(candidates):
    return sorted((start, end, entity) for start, end, entity, _source in resolve_overlaps(candidates))


def test_rule_guardoath_detections_outrank_pattern_matches():
    # Contained or identical: dropped, whatever the pattern score.
    assert kept([own(0, 16), pattern(5, 16, "URL", 1.0)]) == [(0, 16, "EMAIL_ADDRESS")]
    assert kept([own(0, 10, "BANK_ACCOUNT", LABELED_FIELD), pattern(0, 10, "US_BANK_NUMBER", 1.0)]) == [
        (0, 10, "BANK_ACCOUNT")]
    # Partly outside: kept.
    assert kept([own(0, 10), pattern(5, 15, "URL", 0.5)]) == [(0, 10, "EMAIL_ADDRESS"), (5, 15, "URL")]


def test_rule_guardoath_detections_are_never_dropped():
    assert kept([own(0, 16, "CREDIT_CARD"), own(0, 16, "BANK_ACCOUNT", LABELED_FIELD), own(2, 8, "US_SSN")]) == [
        (0, 16, "BANK_ACCOUNT"), (0, 16, "CREDIT_CARD"), (2, 8, "US_SSN")]


def test_rule_between_patterns_follows_the_copied_duplicate_rule():
    # Contained with an equal or lower score: dropped.
    assert kept([pattern(0, 20, "IBAN_CODE", 1.0), pattern(4, 12, "US_BANK_NUMBER", 1.0)]) == [(0, 20, "IBAN_CODE")]
    assert kept([pattern(0, 20, "IBAN_CODE", 1.0), pattern(4, 12, "US_BANK_NUMBER", 0.4)]) == [(0, 20, "IBAN_CODE")]
    # Contained with a higher score: kept.
    assert kept([pattern(0, 20, "DATE_TIME", 0.4), pattern(4, 12, "US_ITIN", 0.9)]) == [
        (0, 20, "DATE_TIME"), (4, 12, "US_ITIN")]
    # Identical spans: the higher score wins; equal scores keep both.
    assert kept([pattern(0, 9, "US_PASSPORT", 0.45), pattern(0, 9, "ZA_PASSPORT", 1.0)]) == [(0, 9, "ZA_PASSPORT")]
    assert kept([pattern(0, 9, "PH_PASSPORT", 0.45), pattern(0, 9, "UK_PASSPORT", 0.45)]) == [
        (0, 9, "PH_PASSPORT"), (0, 9, "UK_PASSPORT")]
    # Partial overlaps: kept.
    assert kept([pattern(0, 10, "UUID", 0.6), pattern(5, 15, "URL", 0.5)]) == [(0, 10, "UUID"), (5, 15, "URL")]
    # The same type twice on the same characters: one result.
    assert kept([pattern(0, 9, "US_ITIN", 0.3), pattern(0, 9, "US_ITIN", 0.5)]) == [(0, 9, "US_ITIN")]


def test_rule_never_changes_which_characters_are_covered():
    rng = random.Random(1729)
    entities = ["URL", "DATE_TIME", "US_BANK_NUMBER", "IBAN_CODE", "ZA_ID_NUMBER"]
    for _ in range(500):
        candidates = []
        for _ in range(rng.randint(1, 12)):
            start = rng.randint(0, 40)
            end = start + rng.randint(1, 15)
            if rng.random() < 0.3:
                candidates.append(own(start, end, rng.choice(["EMAIL_ADDRESS", "BANK_ACCOUNT"])))
            else:
                candidates.append(pattern(start, end, rng.choice(entities), rng.choice([0.05, 0.4, 0.5, 1.0])))
        result = resolve_overlaps(candidates)
        covered = {i for start, end, *_ in candidates for i in range(start, end)}
        assert {i for start, end, *_ in result for i in range(start, end)} == covered
        assert {(s, e, n, src) for s, e, n, src, tier, _ in candidates if tier == OWN_TIER} <= set(result)
        assert set(result) <= {(s, e, n, src) for s, e, n, src, _tier, _ in candidates}


def test_own_only_overlap_fast_path_preserves_distinct_labels_and_deduplicates():
    # Real labelled fields and the own detectors can describe overlapping
    # values; the no-pattern path must keep every distinct source/entity.
    examples = [
        own(0, 18, "EMAIL_ADDRESS"),
        own(0, 18, "EMAIL_ADDRESS"),
        own(0, 18, "CREDIT_CARD"),
        own(3, 10, "BANK_ACCOUNT", LABELED_FIELD),
        own(0, 18, "EMAIL_ADDRESS", LABELED_FIELD),
    ]
    result = resolve_overlaps(examples)
    assert result == [
        (0, 18, "EMAIL_ADDRESS", PATTERN),
        (0, 18, "CREDIT_CARD", PATTERN),
        (3, 10, "BANK_ACCOUNT", LABELED_FIELD),
        (0, 18, "EMAIL_ADDRESS", LABELED_FIELD),
    ]
    assert resolve_overlaps([]) == []
    # Even one genuine pattern must retain the original containment rules.
    assert kept([*examples, pattern(3, 8, "URL", 1.0)]) == [
        (0, 18, "CREDIT_CARD"), (0, 18, "EMAIL_ADDRESS"),
        (0, 18, "EMAIL_ADDRESS"), (3, 10, "BANK_ACCOUNT")
    ]



def test_own_only_overlap_fast_path_stops_at_request_deadline():
    """A dense own-only field must not monopolize the PII worker after expiry."""
    class CountedCandidates(list):
        visited = 0

        def __iter__(self):
            for item in super().__iter__():
                self.visited += 1
                yield item

    candidates = CountedCandidates([own(i, i + 2) for i in range(6000)])
    assert len(resolve_overlaps(candidates)) == 6000
    assert resolve_overlaps([own(0, 10), own(0, 10), own(0, 10, "US_SSN")]) == [
        (0, 10, "EMAIL_ADDRESS", PATTERN), (0, 10, "US_SSN", PATTERN),
    ]

    candidates.visited = 0
    checks = 0

    def deadline():
        nonlocal checks
        checks += 1
        if checks == 3:
            raise TimeoutError("synthetic own-only deadline")

    with pytest.raises(TimeoutError, match="synthetic own-only deadline"):
        resolve_overlaps(candidates, deadline)
    assert checks == 3
    assert candidates.visited == 513  # abort during first scan, not after all 6000

    # Also prove the deduplication pass cannot consume an expired budget
    # after the preceding pattern-detection pass has completed.
    candidates.visited = 0
    checks = 0

    def dedup_deadline():
        nonlocal checks
        checks += 1
        if checks == 27:  # 24 scans, then third deduplication checkpoint
            raise TimeoutError("synthetic own-only dedup deadline")

    with pytest.raises(TimeoutError, match="synthetic own-only dedup deadline"):
        resolve_overlaps(candidates, dedup_deadline)
    assert checks == 27
    assert candidates.visited == 6513  # 6000 scanned + 513 dedup candidates



def test_pattern_containment_index_exactly_matches_independent_reference():
    # Independent original list-scan contract for arbitrary overlaps, scores,
    # exact-span ties and GuardOath's protected own detections.
    def reference(candidates):
        own_spans = sorted({(s, e) for s, e, _name, _src, tier, _score in candidates
                            if tier == OWN_TIER})
        best = {}
        for s, e, name, src, tier, score in candidates:
            key = (s, e, name, src, tier)
            best[key] = max(score, best.get(key, score))
        chosen = [(s, e, name, src) for s, e, name, src, tier in best
                  if tier == OWN_TIER]
        patterns = []
        for (s, e, name, src, tier), score in best.items():
            if tier != PATTERN_TIER:
                continue
            if any(a <= s and b >= e for a, b in own_spans):
                continue
            patterns.append((s, e, name, src, score))
        maximum = {}
        for s, e, _name, _src, score in patterns:
            maximum[(s, e)] = max(score, maximum.get((s, e), score))
        containers = []
        for s, e, name, src, score in sorted(patterns, key=lambda p: (p[0], -p[1])):
            if score < maximum[(s, e)]:
                continue
            containers = [old for old in containers if old[1] >= s]
            if any(old_end >= e and (old_start, old_end) != (s, e)
                   and old_score >= score
                   for old_start, old_end, old_score in containers):
                continue
            chosen.append((s, e, name, src))
            containers.append((s, e, score))
        return chosen

    rng = random.Random(24011)
    names = ["URL", "DATE_TIME", "US_BANK_NUMBER", "ZA_ID_NUMBER"]
    for _ in range(350):
        candidates = []
        for _ in range(rng.randint(1, 48)):
            start = rng.randrange(0, 70)
            end = start + rng.randint(1, 80)
            if rng.random() < .25:
                candidates.append(own(start, end,
                                      rng.choice(["EMAIL_ADDRESS", "BANK_ACCOUNT"])))
            else:
                candidates.append(pattern(start, end, rng.choice(names),
                                          rng.choice([.05, .4, .45, .5, 1.0])))
            if rng.random() < .08:
                candidates.append(candidates[-1])
        assert resolve_overlaps(candidates) == reference(candidates)


def test_thousands_of_partially_overlapping_patterns_preserve_all_findings():
    # Each interval overlaps its neighbors without containing any earlier
    # interval. The former repeated live-container walk took quadratic work.
    candidates = [pattern(i, i + 4000, "URL", .5) for i in range(4000)]
    actual = resolve_overlaps(candidates)
    assert len(actual) == len(candidates)
    assert actual[0] == (0, 4000, "URL", PATTERN)
    assert actual[-1] == (3999, 7999, "URL", PATTERN)
    # Same-position equal-score types are independent findings, while a
    # strictly contained lower-scored pattern is not.
    tied = [pattern(0, 8000, "URL", .7),
            pattern(0, 8000, "DATE_TIME", .7),
            pattern(10, 7900, "US_BANK_NUMBER", .4)]
    assert resolve_overlaps(tied) == [
        (0, 8000, "URL", PATTERN), (0, 8000, "DATE_TIME", PATTERN)]
