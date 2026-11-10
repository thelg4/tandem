"""Token usage across all projects, and Codex plan limits, from the agents' own logs.

Claude Code repeats one API response's usage on every record of that response, so
responses are counted once by request id. Codex reports per-call usage in
``token_count`` events, sometimes twice for the same call; a call counts only when the
cumulative total moves. Codex also records the plan's rate-limit windows there. Claude
Code logs carry no plan-limit data, so for Claude only token counts are available.

Read-only and offline. Only lines that can hold usage are parsed.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from tandem.live.protocol import iso, parse_iso
from tandem.live.tail import recent_logs

WEEK_S = 7 * 86400


@dataclass(slots=True)
class Tokens:
    input: int = 0
    """Everything sent to the model, cached or not."""
    output: int = 0
    """Generated tokens, reasoning included."""

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.input += input_tokens
        self.output += output_tokens

    def as_json(self) -> dict[str, int]:
        return {"input": self.input, "output": self.output}


@dataclass(slots=True)
class AgentUsage:
    today: Tokens = field(default_factory=Tokens)
    week: Tokens = field(default_factory=Tokens)
    last_at: float | None = None

    def add(self, at: float, start_of_day: float, input_tokens: int, output_tokens: int) -> None:
        self.week.add(input_tokens, output_tokens)
        if at >= start_of_day:
            self.today.add(input_tokens, output_tokens)
        self.last_at = at if self.last_at is None else max(self.last_at, at)

    def as_json(self) -> dict[str, object]:
        return {
            "today": self.today.as_json(),
            "week": self.week.as_json(),
            "last_at": iso(self.last_at) if self.last_at is not None else None,
        }


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _window(raw: object) -> dict[str, object] | None:
    w = _dict(raw)
    used = w.get("used_percent")
    if not isinstance(used, int | float) or isinstance(used, bool):
        return None
    minutes = w.get("window_minutes")
    resets = w.get("resets_at")
    return {
        "used_percent": float(used),
        "window_minutes": minutes if isinstance(minutes, int) else None,
        "resets_at": iso(resets) if isinstance(resets, int | float) else None,
    }


def _claude(paths: list[Path], since: float, start_of_day: float) -> AgentUsage:
    usage = AgentUsage()
    seen: set[str] = set()
    for path in paths:
        try:
            fh = path.open("rb")
        except OSError:
            continue
        with fh:
            for line in fh:
                if b'"usage"' not in line or b'"assistant"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(rec, dict) or rec.get("type") != "assistant":
                    continue
                message = _dict(rec.get("message"))
                key = rec.get("requestId") or message.get("id")
                at = parse_iso(rec.get("timestamp"))
                if not isinstance(key, str) or at is None or at < since or key in seen:
                    continue
                seen.add(key)
                u = _dict(message.get("usage"))
                usage.add(
                    at,
                    start_of_day,
                    _int(u.get("input_tokens"))
                    + _int(u.get("cache_creation_input_tokens"))
                    + _int(u.get("cache_read_input_tokens")),
                    _int(u.get("output_tokens")),
                )
    return usage


def _codex(
    paths: list[Path], since: float, start_of_day: float
) -> tuple[AgentUsage, dict[str, object] | None]:
    usage = AgentUsage()
    limits: dict[str, object] | None = None
    limits_at = float("-inf")
    for path in paths:
        try:
            fh = path.open("rb")
        except OSError:
            continue
        last_total: int | None = None
        with fh:
            for line in fh:
                if b'"token_count"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                payload = _dict(rec.get("payload")) if isinstance(rec, dict) else {}
                at = parse_iso(rec.get("timestamp")) if isinstance(rec, dict) else None
                if payload.get("type") != "token_count" or at is None:
                    continue
                limit = _dict(payload.get("rate_limits"))
                primary, secondary = _window(limit.get("primary")), _window(limit.get("secondary"))
                if (primary or secondary) and at > limits_at:
                    limits_at = at
                    plan = limit.get("plan_type")
                    limits = {
                        "primary": primary,
                        "secondary": secondary,
                        "plan": plan if isinstance(plan, str) else None,
                        "at": iso(at),
                    }
                info = _dict(payload.get("info"))
                total = _int(_dict(info.get("total_token_usage")).get("total_tokens"))
                if total == last_total:
                    continue  # the same call reported again
                last_total = total
                if at < since:
                    continue
                last = _dict(info.get("last_token_usage"))
                usage.add(
                    at,
                    start_of_day,
                    _int(last.get("input_tokens")),
                    _int(last.get("output_tokens")),
                )
    return usage, limits


def usage_summary(
    claude_root: Path, codex_root: Path, *, now: float | None = None
) -> dict[str, object]:
    """Tokens today (local time) and over the last 7 days, per agent, plus Codex limits."""
    now = now if now is not None else time.time()
    since = now - WEEK_S
    start_of_day = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    claude = _claude(recent_logs(claude_root, since), since, start_of_day.timestamp())
    codex, limits = _codex(recent_logs(codex_root, since), since, start_of_day.timestamp())
    return {
        "at": iso(now),
        "claude_code": claude.as_json(),
        "codex": {**codex.as_json(), "limits": limits},
    }
