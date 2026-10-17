"""Metric value contract, checked against the synthetic worked example in docs/metrics.md."""

from __future__ import annotations

from fractions import Fraction

import pytest

from tandem.model.metrics import MetricName, MetricStatus, MetricValue


def test_worked_example_ratios() -> None:
    survival = MetricValue.from_counts(MetricName.SURVIVAL, 70, 100, 5)
    churn = MetricValue.from_counts(MetricName.CHURN_RECEIVED, 20, 100, 5)
    revert = MetricValue.from_counts(MetricName.EXACT_REVERT_RATE, 1, 10, 5)
    tokens = MetricValue.from_counts(MetricName.TOKENS_PER_SURVIVING_LINE, 14_000, 70, 5)
    assert survival.ratio == Fraction(7, 10)
    assert churn.ratio == Fraction(1, 5)
    assert revert.ratio == Fraction(1, 10)
    assert tokens.ratio == 200
    assert survival.display() == "70 / 100 = 70.0%"


def test_zero_denominator_is_not_applicable_not_zero() -> None:
    m = MetricValue.from_counts(MetricName.SURVIVAL, 0, 0, 5)
    assert m.status is MetricStatus.NOT_APPLICABLE
    assert m.ratio is None
    assert m.display() == "n/a"


def test_pending_never_renders_as_a_number() -> None:
    m = MetricValue(MetricName.SURVIVAL, MetricStatus.PENDING, None, None, 5)
    assert m.display() == "pending"


def test_negative_counts_rejected() -> None:
    with pytest.raises(ValueError):
        MetricValue.from_counts(MetricName.SURVIVAL, -1, 10, 5)
