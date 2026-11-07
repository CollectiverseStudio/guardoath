"""Exact bounded PSI tests: no external dependencies, no silent clipping."""
from dataclasses import replace
from decimal import Decimal

import pytest

from guardoath.statistical_drift import (
    MIN_OBSERVATIONS, MAX_OBSERVATIONS, ReferenceHistogram, compute_psi,
)


def baseline():
    return ReferenceHistogram(reference_id="approved-2026", version="v2",
                              bin_edges=("0", "10", "20", "30"),
                              counts=(10, 10, 10))


def sample():
    return [Decimal("5")] * 10 + [Decimal("15")] * 10 + [Decimal("25")] * 10


def test_identical_distribution_has_zero_psi_without_modifying_values():
    samples = sample()
    original = tuple(samples)
    result = compute_psi(baseline(), samples)
    assert result.status == "measured"
    assert result.psi == "0.000000"
    assert result.reference_version == "v2"
    assert result.actual_observations == result.reference_observations == 30
    assert tuple(samples) == original


def test_actual_distribution_shift_is_measured_with_same_source_baseline():
    changed = [Decimal("5")] * 1 + [Decimal("15")] * 1 + [Decimal("25")] * 28
    result = compute_psi(baseline(), changed)
    assert result.status == "measured"
    assert Decimal(result.psi) > 0
    assert result.interpretation == "descriptive_only_no_automatic_action"


@pytest.mark.parametrize("samples", [[], [1], [1] * 29])
def test_insufficient_actual_data_is_not_claimed_to_be_zero_drift(samples):
    result = compute_psi(baseline(), samples)
    assert result.status == "insufficient_data"
    assert result.psi is None


def test_insufficient_reference_data_is_not_clean_or_measured():
    ref = replace(baseline(), counts=(0, 1, 2))
    result = compute_psi(ref, sample())
    assert result.status == "insufficient_data" and result.psi is None


def test_empty_bins_have_finite_psi_with_explicit_smoothing():
    ref = replace(baseline(), counts=(30, 0, 0))
    result = compute_psi(ref, [Decimal("25")] * 30)
    assert result.status == "measured"
    assert Decimal(result.psi).is_finite()
    assert Decimal(result.psi) > 0
    assert result.smoothing == "Jeffreys pseudocount 0.5 per bin"


@pytest.mark.parametrize("reference", [
    ReferenceHistogram("source", "v1", ("0", "10", "10"), (15, 15)),
    ReferenceHistogram("source", "v1", ("20", "10", "30"), (15, 15)),
    ReferenceHistogram("source", "v1", ("0", "10", "30"), (True, 29)),
    ReferenceHistogram("source", "v1", ("0", "10", "30"), (-1, 31)),
    ReferenceHistogram("source", "v1", ("0", "10", "30"), (5,)),
    ReferenceHistogram("", "v1", ("0", "10", "30"), (15, 15)),
])
def test_invalid_reference_is_rejected(reference):
    with pytest.raises(ValueError):
        compute_psi(reference, sample())


@pytest.mark.parametrize("observations", [
    [False] * 30, ["nan"] * 30, [float("inf")] * 30,
    ["1000"] * 30, [-1] * 30, ["0e999999"] * 30,
    list(range(MAX_OBSERVATIONS + 1)), "not-an-array",
])
def test_invalid_or_outside_baseline_observation_is_rejected(observations):
    with pytest.raises(ValueError):
        compute_psi(baseline(), observations)


def test_exact_last_edge_belongs_to_last_bin():
    result = compute_psi(baseline(), [Decimal("30")] * 30)
    assert result.status == "measured"
    assert result.actual_observations == 30


def test_invalid_minimum_and_reference_size_fail_closed():
    for bad in (False, -1, 0, MIN_OBSERVATIONS - 1, MAX_OBSERVATIONS + 1, 1.5):
        with pytest.raises(ValueError):
            compute_psi(baseline(), sample(), min_observations=bad)
    with pytest.raises(ValueError):
        compute_psi(replace(baseline(), counts=(100_001, 0, 0)), sample())
