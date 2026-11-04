"""Stored durability scorecards: reading and rendering."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from tandem.model.metrics import MetricName, MetricStatus, MetricValue
from tandem.report.json_report import metric_to_json


def _counts(d: dict[str, int]) -> str:
    return ", ".join(f"{k} {n}" for k, n in sorted(d.items(), key=lambda kv: -kv[1])) or "none"


def _tally(mapping: dict[str, str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in mapping.values():
        out[value] = out.get(value, 0) + 1
    return out


METRIC_LABELS = {
    "survival_at_n": "survival at {h}",
    "churn_received": "churn received",
    "exact_revert_rate": "exact revert rate",
    "tokens_per_surviving_line": "tokens per surviving line",
}


def durability(
    conn: sqlite3.Connection,
    run_id: int,
    kind: str,
    *,
    agent: str | None = None,
    scope: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Stored scorecards for one run: {scope_id: {context..., "metrics": {name: value}}}."""
    out: dict[str, dict[str, Any]] = {}
    for r in conn.execute(
        "SELECT * FROM scorecards WHERE run_id = ?1 AND scope_kind = ?2 "
        "AND (?3 IS NULL OR agent = ?3) AND (?4 IS NULL OR scope_id = ?4) ORDER BY scope_id",
        (run_id, kind, agent, scope),
    ):
        out[r["scope_id"]] = {
            "agent": r["agent"],
            "horizon_commits": r["horizon_commits"],
            "mature_lines": r["mature_lines"],
            "pending_lines": r["pending_lines"],
            "sessions": r["sessions"],
            "comparable": bool(r["comparable"]),
            "usage_excluded": json.loads(r["usage_notes"] or "{}"),
            "metrics": {},
        }
    for r in conn.execute(
        "SELECT * FROM metrics WHERE run_id = ? AND scope_kind = ?", (run_id, kind)
    ):
        if r["scope_id"] not in out:
            continue
        extra = json.loads(r["exclusions"] or "{}")
        value = MetricValue(
            MetricName(r["name"]),
            MetricStatus(r["status"]),
            r["numerator"],
            r["denominator"],
            r["horizon_commits"],
            extra.get("exclusions", {}),
            tuple(extra.get("notes", [])),
        )
        out[r["scope_id"]]["metrics"][r["name"]] = {
            **metric_to_json(value),
            "display": value.display(),
            "definition_version": r["definition_version"],
        }
    return out


def durability_section(cards: dict[str, dict[str, Any]]) -> list[str]:
    first = next(iter(cards.values()))
    lines = [
        f"Durability (horizon {first['horizon_commits']} first-parent commits; verified lines only)"
    ]
    for scope, card in cards.items():
        m = card["metrics"]
        lines.append(
            f"  {scope}: {card['mature_lines']} mature line(s), {card['pending_lines']} pending"
        )
        for name, label in METRIC_LABELS.items():
            if name in m:
                text = m[name]["display"]
                if m[name]["status"] == "partial":
                    text += "  (partial)"
                lines.append(f"    {label.format(h=card['horizon_commits'])}: {text}")
        extra = []
        if "churn_received_interactions" in m:
            extra.append(
                f"{m['churn_received_interactions']['numerator']} interaction(s) "
                f"received affecting {m['churn_received_lines']['numerator']} line(s)"
            )
        if m.get("partial_revert_groups", {}).get("numerator"):
            extra.append(f"{m['partial_revert_groups']['numerator']} partial revert(s)")
        if extra:
            lines.append("    " + "; ".join(extra))
        if card["usage_excluded"]:
            lines.append(
                f"    usage excluded for {len(card['usage_excluded'])} session(s): "
                + _counts(_tally(card["usage_excluded"]))
            )
        if not card["comparable"]:
            lines.append(
                "    small sample (under 30 mature lines or 3 sessions): not for comparison"
            )
    lines.append("")
    return lines
