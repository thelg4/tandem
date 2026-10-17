"""Metric value contract (spec §8, DB-04).

Every value carries its numerator, denominator, horizon, and exclusion counts.
A zero denominator is "not applicable", never 0 or infinity, and SQL null is
never rendered as zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction


class MetricStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    PENDING = "pending"
    NOT_APPLICABLE = "not_applicable"


class MetricName(StrEnum):
    SURVIVAL = "survival_at_n"
    CHURN_RECEIVED = "churn_received"
    EXACT_REVERT_RATE = "exact_revert_rate"
    TOKENS_PER_SURVIVING_LINE = "tokens_per_surviving_line"


# Presentation defaults, not significance thresholds (spec §8).
MIN_MATURE_LINES_FOR_COMPARISON = 30
MIN_SESSIONS_FOR_COMPARISON = 3


@dataclass(frozen=True, slots=True)
class MetricValue:
    name: MetricName
    status: MetricStatus
    numerator: int | None
    denominator: int | None
    horizon_commits: int
    exclusions: dict[str, int] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @property
    def ratio(self) -> Fraction | None:
        """Exact ratio, or None when not computable. Pooled counts, never averaged rates."""
        if self.numerator is None or not self.denominator:
            return None
        return Fraction(self.numerator, self.denominator)

    @classmethod
    def from_counts(
        cls,
        name: MetricName,
        numerator: int,
        denominator: int,
        horizon_commits: int,
        *,
        partial: bool = False,
        exclusions: dict[str, int] | None = None,
    ) -> MetricValue:
        if denominator < 0 or numerator < 0:
            raise ValueError("metric counts cannot be negative")
        if denominator == 0:
            status = MetricStatus.NOT_APPLICABLE
        elif partial:
            status = MetricStatus.PARTIAL
        else:
            status = MetricStatus.COMPLETE
        return cls(
            name=name,
            status=status,
            numerator=numerator,
            denominator=denominator,
            horizon_commits=horizon_commits,
            exclusions=dict(exclusions or {}),
        )

    def display(self) -> str:
        if self.status in (MetricStatus.PENDING, MetricStatus.UNAVAILABLE):
            return self.status.value
        ratio = self.ratio
        if ratio is None:
            return "n/a"
        return f"{self.numerator} / {self.denominator} = {float(ratio) * 100:.1f}%"
