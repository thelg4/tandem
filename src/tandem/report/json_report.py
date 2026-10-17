"""Versioned machine-readable report (spec §13, UX-06).

Emitted to stdout by ``tandem report --format json``; diagnostics go to stderr.
Exports exclude prompts, absolute home paths, secrets and full diffs by default,
and always carry coverage and caveats with conclusions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tandem.model.metrics import MetricValue
from tandem.versions import REPORT_FORMAT_VERSION, manifest_versions


def redact_path(path: str | Path) -> str:
    """Replace the user's home directory so exports do not leak it."""
    text = str(path)
    home = str(Path.home())
    return "~" + text[len(home) :] if text == home or text.startswith(home + "/") else text


def metric_to_json(metric: MetricValue) -> dict[str, Any]:
    ratio = metric.ratio
    return {
        "name": metric.name.value,
        "status": metric.status.value,
        "numerator": metric.numerator,
        "denominator": metric.denominator,
        "ratio": None if ratio is None else float(ratio),
        "horizon_commits": metric.horizon_commits,
        "exclusions": metric.exclusions,
        "notes": list(metric.notes),
    }


def build_report(
    *,
    repository: dict[str, Any],
    run: dict[str, Any] | None,
    coverage: dict[str, Any],
    metrics: list[MetricValue],
    interactions: list[dict[str, Any]],
    filters: dict[str, Any],
    caveats: list[str],
) -> dict[str, Any]:
    return {
        "format_version": REPORT_FORMAT_VERSION,
        "versions": manifest_versions(),
        "repository": repository,
        "run": run,
        "filters": filters,
        "coverage": coverage,
        "metrics": [metric_to_json(m) for m in metrics],
        "interactions": interactions,
        "caveats": caveats,
    }


def dumps(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False)
