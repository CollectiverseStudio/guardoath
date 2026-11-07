"""Local, bounded population drift telemetry for structured ML payloads.

This is NOT an active GuardOath tenant check or an inference gateway.
No input clipping, no automated mitigation, no provider calls and no stored
per-subject values. A host-approved, fixed versioned reference histogram is
required. An empty or small sample is INSUFFICIENT_DATA, never 'no drift'.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Sequence

MAX_BINS = 64
MAX_OBSERVATIONS = 100_000
MIN_OBSERVATIONS = 30
_PSEUDOCOUNT = Decimal("0.5")


@dataclass(frozen=True)
class ReferenceHistogram:
    """Approved immutable histogram: bin intervals [edge_i, edge_i+1),
    except the final bin includes its upper endpoint.

    Counts are aggregates from a verified baseline. No raw training data.
    """
    reference_id: str
    version: str
    bin_edges: tuple[str, ...]
    counts: tuple[int, ...]


@dataclass(frozen=True)
class DriftResult:
    status: str
    reference_id: str
    reference_version: str
    reference_observations: int
    actual_observations: int
    bin_count: int
    psi: str | None
    smoothing: str
    interpretation: str


def _decimal(value) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("Only bounded Decimal, decimal-string, or integer values are supported")
    if len(str(value)) > 48:
        raise ValueError("Numeric token exceeds the bounded representation")
    try:
        result = Decimal(value)
    except (ValueError, InvalidOperation):
        raise ValueError("Invalid decimal value") from None
    if not result.is_finite() or result.adjusted() > 15 or result.adjusted() < -15:
        raise ValueError("Nonfinite or out-of-range drift metric")
    return result


def _validate_reference(ref: ReferenceHistogram) -> tuple[Decimal, ...]:
    if not isinstance(ref, ReferenceHistogram):
        raise ValueError("Reference histogram is required")
    if (not isinstance(ref.reference_id, str) or not 1 <= len(ref.reference_id) <= 100
            or not isinstance(ref.version, str) or not 1 <= len(ref.version) <= 80):
        raise ValueError("Reference identity and version are required")
    if not 2 <= len(ref.counts) <= MAX_BINS or len(ref.bin_edges) != len(ref.counts) + 1:
        raise ValueError("Reference must have 2..64 bins and matching endpoints")
    edges = tuple(_decimal(x) for x in ref.bin_edges)
    if any(right <= left for left, right in zip(edges, edges[1:])):
        raise ValueError("Bin edges must be strictly increasing")
    if any(type(count) is not int or count < 0 for count in ref.counts):
        raise ValueError("Reference counts must be nonnegative integer aggregates")
    if sum(ref.counts) > MAX_OBSERVATIONS:
        raise ValueError("Reference sample exceeds maximum")
    return edges


def compute_psi(ref: ReferenceHistogram, observations: Sequence,
                *, min_observations: int = MIN_OBSERVATIONS) -> DriftResult:
    """Compare the observed histogram against a host-approved fixed baseline.

    PSI is descriptive only. It is NOT a validation of model safety,
    statistical significance, fairness, truth, or a causal drift diagnosis.
    """
    edges = _validate_reference(ref)
    if type(min_observations) is not int or not MIN_OBSERVATIONS <= min_observations <= MAX_OBSERVATIONS:
        raise ValueError("minimum observations must be 30..100000")
    if not isinstance(observations, (list, tuple)) or len(observations) > MAX_OBSERVATIONS:
        raise ValueError("observations must be a bounded in-memory batch")
    reference_total = sum(ref.counts)
    actual_total = len(observations)
    if reference_total < min_observations or actual_total < min_observations:
        return DriftResult(
            status="insufficient_data",
            reference_id=ref.reference_id,
            reference_version=ref.version,
            reference_observations=reference_total,
            actual_observations=actual_total,
            bin_count=len(ref.counts), psi=None,
            smoothing="Jeffreys pseudocount 0.5 per bin",
            interpretation="insufficient_sample_size",
        )
    counts = [0] * len(ref.counts)
    for value in observations:
        number = _decimal(value)
        if number < edges[0] or number > edges[-1]:
            # Never discard out-of-range observations or silently clip them.
            raise ValueError("Observation falls outside approved reference support")
        index = min(bisect_right(edges, number) - 1, len(counts) - 1)
        counts[index] += 1

    with localcontext() as ctx:
        ctx.prec = 45
        k = Decimal(len(ref.counts))
        expected_denominator = Decimal(reference_total) + _PSEUDOCOUNT * k
        observed_denominator = Decimal(actual_total) + _PSEUDOCOUNT * k
        psi = Decimal(0)
        for expected_count, observed_count in zip(ref.counts, counts):
            expected = (Decimal(expected_count) + _PSEUDOCOUNT) / expected_denominator
            actual = (Decimal(observed_count) + _PSEUDOCOUNT) / observed_denominator
            ratio = actual / expected
            psi += (actual - expected) * ratio.ln()
        if psi < 0 and abs(psi) < Decimal("1e-35"):
            psi = Decimal(0)
        if not psi.is_finite() or psi < 0:
            raise ValueError("Invalid PSI result")
        value = str(psi.quantize(Decimal("0.000001")))
    return DriftResult(
        status="measured", reference_id=ref.reference_id,
        reference_version=ref.version, reference_observations=reference_total,
        actual_observations=actual_total, bin_count=len(ref.counts),
        psi=value, smoothing="Jeffreys pseudocount 0.5 per bin",
        interpretation="descriptive_only_no_automatic_action",
    )
