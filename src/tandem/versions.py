"""Independently versioned components (spec §11, "Scan transaction model").

A parser change may require source reimport; a metric policy change should reuse
evidence. Bump the specific version whose behavior changed, and record in the
changelog how existing derived runs become stale (spec §17, definition of done).
"""

from __future__ import annotations

from typing import Final

SCHEMA_VERSION: Final = 4
"""SQLite schema version. Matches the highest migration in storage/migrations."""

EVENT_ENVELOPE_VERSION: Final = "1"
"""Normalized event envelope contract (spec §4)."""

REPORT_FORMAT_VERSION: Final = "0.1"
"""Machine-readable JSON report contract (`tandem report --format json`)."""

ADAPTER_VERSIONS: Final[dict[str, str]] = {
    "claude_code": "0.1.0",
    "codex": "0.1.0",
}

ATTRIBUTION_RULES_VERSION: Final = "0.1.0"
INTERACTION_RULES_VERSION: Final = "0.1.0"
METRIC_DEFINITIONS_VERSION: Final = "0.0.0"


def manifest_versions() -> dict[str, object]:
    """Versions recorded in every analysis run manifest (DB-03)."""
    return {
        "schema": SCHEMA_VERSION,
        "event_envelope": EVENT_ENVELOPE_VERSION,
        "report_format": REPORT_FORMAT_VERSION,
        "adapters": dict(ADAPTER_VERSIONS),
        "attribution_rules": ATTRIBUTION_RULES_VERSION,
        "interaction_rules": INTERACTION_RULES_VERSION,
        "metric_definitions": METRIC_DEFINITIONS_VERSION,
    }
