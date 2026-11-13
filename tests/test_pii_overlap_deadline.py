"""PII mixed-pattern overlap preprocessing must respect the existing worker deadline.

Tests are synthetic and inspect only candidate counts; no private values or logs.
A deadline failure must raise, not return a partial set of PII findings.
"""
import inspect

import pytest

from guardoath.pii import OWN_TIER, PATTERN_TIER, resolve_overlaps


def own(start):
    return (start, start + 2, "EMAIL_ADDRESS", "pattern", OWN_TIER, 1.0)


def pattern(start, end, entity="URL"):
    return (start, end, entity, "pattern", PATTERN_TIER, 0.5)


class CountedCandidates(list):
    def __init__(self, values):
        super().__init__(values)
        self.visited = 0

    def __iter__(self):
        for item in super().__iter__():
            self.visited += 1
            yield item


def test_dense_mixed_index_preserves_all_own_findings_and_pattern():
    candidates = [pattern(20000, 20004)] + [own(i * 3) for i in range(6000)]
    output = resolve_overlaps(candidates)
    assert len(output) == len(candidates)
    assert output[0] == (0, 2, "EMAIL_ADDRESS", "pattern")
    assert (20000, 20004, "URL", "pattern") in output
    assert len(set(output)) == len(output)


@pytest.mark.parametrize("expire_on,expected_visits", [
    (3, 258),    # mixed own-span indexing, before considering all candidates
    (28, 6259),  # best-score map, after a complete own-span pass
])
def test_dense_mixed_index_expires_before_an_unbounded_preprocessing_pass(
        expire_on, expected_visits):
    candidates = CountedCandidates(
        [pattern(20000, 20004)] + [own(i * 3) for i in range(6000)])
    ticks = 0

    def deadline():
        nonlocal ticks
        ticks += 1
        if ticks == expire_on:
            raise TimeoutError("synthetic PII overlap worker deadline")

    with pytest.raises(TimeoutError, match="synthetic PII overlap worker deadline"):
        resolve_overlaps(candidates, deadline)
    assert ticks == expire_on
    assert candidates.visited == expected_visits
    assert candidates.visited < len(candidates) * 2


def test_large_identical_pattern_span_still_retains_all_equal_score_labels():
    # Equal-score, identical-span pattern detections are separate findings.
    candidates = [pattern(0, 42, f"SYNTHETIC_{i}") for i in range(6000)]
    output = resolve_overlaps(candidates)
    assert len(output) == len(candidates)
    assert len(set(output)) == 6000
    assert output[0] == (0, 42, "SYNTHETIC_0", "pattern")
    assert output[-1] == (0, 42, "SYNTHETIC_5999", "pattern")


def test_large_identical_span_group_honors_deadline_before_returning_findings():
    # The 6,000-candidate group is indexed before it is evaluated. Expiry
    # during group scanning cannot return a misleading partial result.
    candidates = [pattern(0, 42, f"SYNTHETIC_{i}") for i in range(6000)]
    ticks = 0

    def deadline():
        nonlocal ticks
        ticks += 1
        if ticks == 6160:
            raise TimeoutError("synthetic equal-span PII deadline")

    with pytest.raises(TimeoutError, match="synthetic equal-span PII deadline"):
        resolve_overlaps(candidates, deadline)
    assert ticks == 6160


def test_dense_own_prefix_walk_checks_deadline_inside_single_pattern():
    """A distant pattern cannot traverse 6,000 earlier own spans unchecked.

    Inspect the checkpoint's own_index rather than relying on an incidental
    number of earlier index-building callbacks. No personal values are used.
    """
    candidates = [pattern(20000, 20004)] + [own(i * 3) for i in range(6000)]
    seen = []

    def deadline():
        caller = inspect.currentframe().f_back
        if caller.f_code.co_name != "resolve_overlaps":
            return
        prefix_index = caller.f_locals.get("own_index")
        if prefix_index in (0, 256, 512):
            seen.append(prefix_index)
        if prefix_index == 512:
            raise TimeoutError("synthetic own-prefix PII worker deadline")

    with pytest.raises(TimeoutError, match="synthetic own-prefix PII worker deadline"):
        resolve_overlaps(candidates, deadline)
    assert {0, 256, 512}.issubset(seen)
