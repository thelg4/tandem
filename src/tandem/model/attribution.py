"""Attribution evidence classes (spec §5).

Attribution is an evidence classification, not a probabilistic score. Every
assignment carries the class plus the reason codes that explain why it was
accepted or withheld.
"""

from __future__ import annotations

from enum import StrEnum


class EvidenceClass(StrEnum):
    VERIFIED = "verified"
    """Successful structured edit, uniquely reconstructed span, unambiguous lineage match.
    Included in default metrics."""

    SUPPORTED = "supported"
    """Successful edit with a strong content/path match but missing an exact baseline
    or complete ordering. Shown separately; opt-in for metrics."""

    AMBIGUOUS = "ambiguous"
    """Multiple plausible sessions or unresolved concurrent transitions. No single owner."""

    UNKNOWN = "unknown"
    """No usable edit evidence, unsupported mutation, or missing source. No agent credit."""

    PROVISIONAL = "provisional"
    """Not yet anchored to an eligible committed revision. Timeline only."""

    @property
    def counts_by_default(self) -> bool:
        return self is EvidenceClass.VERIFIED

    @property
    def assigns_owner(self) -> bool:
        return self in (EvidenceClass.VERIFIED, EvidenceClass.SUPPORTED)
