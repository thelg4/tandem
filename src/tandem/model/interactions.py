"""Cross-agent interaction labels.

Labels are neutral: a rewrite is not a fix, regression, or mistake.
Exactly one primary label per event feeds headline counts; supporting
tags may overlap and are never summed into the headline.
"""

from __future__ import annotations

from enum import StrEnum


class InteractionLabel(StrEnum):
    REWRITE = "rewrite"
    REMOVAL = "removal"
    EXACT_REVERT = "exact_revert"
    PARTIAL_REVERT = "partial_revert"
    OVERLAP_CANDIDATE = "overlap_candidate"
    """Excluded from verified rewrite totals."""
    CONFLICT_OBSERVED = "conflict_observed"


class InteractionTag(StrEnum):
    TEST_ASSOCIATED_REPAIR = "test_associated_repair"
    """Evidence label only; not proof of intent or sole causation."""
    RECENT = "recent"
    """Inside the configured highlight window. Never a severity signal."""
    SAME_AGENT_CROSS_SESSION = "same_agent_cross_session"
    """Counted separately from cross-agent interactions."""


class WindowMode(StrEnum):
    """How the recent-interaction highlight window combines its predicates."""

    EITHER = "either"
    BOTH = "both"
    HOURS = "hours"
    COMMITS = "commits"
