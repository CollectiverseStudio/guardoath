"""Per-request core PII validation memo is bounded, private and verdict-only.

These tests do not replace 500-ms cross-platform latency probes or Engine
redaction/revalidation acceptance. Caching *findings* or sharing across calls
would violate stage/tenant isolation.
"""
import asyncio

import pytest

from guardoath.pii import _validate_core_candidate


class CountingRecognizer:
    def __init__(self, entity="EMAIL_ADDRESS"):
        self.supported_entities = (entity,)
        self.calls = 0

    def validate_result(self, candidate):
        self.calls += 1
        return candidate.startswith("valid")


def test_duplicate_positive_and_negative_candidates_keep_verdict_and_cache_once():
    validator = CountingRecognizer()
    private_cache = {}
    for candidate, expected in [("valid@sample.test", True), ("invalid@sample.test", False)]:
        for _ in range(100):
            assert _validate_core_candidate(validator, candidate, private_cache) is expected
    assert validator.calls == 2
    assert len(private_cache) == 2


def test_cache_is_per_scan_and_not_shared_between_tenants_or_recheck():
    validator = CountingRecognizer()
    first_pass = {}
    recheck = {}
    assert _validate_core_candidate(validator, "valid@sample.test", first_pass) is True
    assert _validate_core_candidate(validator, "valid@sample.test", recheck) is True
    assert validator.calls == 2
    assert first_pass is not recheck


def test_entity_namespace_prevents_cross_recognizer_verdict_reuse():
    shared_scan = {}
    email = CountingRecognizer("EMAIL_ADDRESS")
    card = CountingRecognizer("CREDIT_CARD")
    assert _validate_core_candidate(email, "valid-synthetic", shared_scan) is True
    assert _validate_core_candidate(card, "valid-synthetic", shared_scan) is True
    assert email.calls == card.calls == 1
    assert len(shared_scan) == 2


def test_cache_is_bounded_and_long_values_never_persist():
    validator = CountingRecognizer()
    cache = {}
    for i in range(260):
        _validate_core_candidate(validator, f"valid{i}", cache)
    assert len(cache) == 256
    assert validator.calls == 260
    # The first short candidate remains cached; no eviction can cause hidden
    # scan-state changes, while a value admitted after the cap gets rechecked.
    assert _validate_core_candidate(validator, "valid0", cache) is True
    assert validator.calls == 260
    _validate_core_candidate(validator, "valid259", cache)
    assert validator.calls == 261
    long_value = "valid" + "x" * 300
    for _ in range(2):
        assert _validate_core_candidate(validator, long_value, cache) is True
    assert validator.calls == 263
    assert len(cache) == 256


def test_validator_exceptions_are_not_cached_as_clean_or_safe():
    class FailsOnce(CountingRecognizer):
        def validate_result(self, candidate):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("synthetic validation failure")
            return True

    validator = FailsOnce()
    cache = {}
    with pytest.raises(RuntimeError, match="synthetic"):
        _validate_core_candidate(validator, "valid", cache)
    assert not cache
    assert _validate_core_candidate(validator, "valid", cache) is True
    assert validator.calls == 2


@pytest.mark.parametrize("stage", ["input", "output"])
def test_actual_pii_checker_still_redacts_every_repeated_email_and_does_not_leak(stage):
    pytest.importorskip("tldextract")
    pytest.importorskip("phonenumbers")
    from guardoath.models import Action, CheckRequest, Policy, Rule, Stage
    from guardoath.pii import build_pii_checker

    policy = Policy(application_id="synthetic-test", version="v1", timeout_ms=500,
                    rules=(Rule(checker_id="pii", on_finding=Action.REDACT),))
    source = "Contact synthetic@example.com; backup synthetic@example.com."
    request = CheckRequest(application_id="synthetic-test", policy_version="v1",
                           content=source, content_format="text")
    checker = build_pii_checker()
    selected = Stage.INPUT if stage == "input" else Stage.OUTPUT
    result = asyncio.run(checker(request, policy, selected))
    assert result.status == "flagged"
    assert any(f.rule_id == "pii.email_address" for f in result.findings)
    assert result.transformed_content is not None
    assert result.transformed_content.count("[REDACTED_EMAIL_ADDRESS]") == 2
    assert "synthetic@example.com" not in result.model_dump_json()
