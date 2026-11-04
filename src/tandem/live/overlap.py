"""File-level overlap rules for live watch (spec "Overlap rules").

Only edits count; reads never raise an overlap. Two different sessions that both
edited a path within ``RECENT_S`` of now are ``recent``; if two of those edits are
within ``ACTIVE_S`` of each other and the newer is within ``ACTIVE_S`` of now, the
path is ``active``. Times are record times, so replayed history does not raise fresh
alarms. Messages are emitted only when a path's level changes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from tandem.live.protocol import iso

ACTIVE_S = 120.0
RECENT_S = 900.0

Level = Literal["active", "recent", "cleared"]


class OverlapTracker:
    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._edits: dict[str, dict[str, float]] = {}
        self._levels: dict[str, Level] = {}

    def edit(self, path: str, session: str, at: float) -> list[dict[str, object]]:
        by_session = self._edits.setdefault(path, {})
        by_session[session] = max(at, by_session.get(session, at))
        return self._update(path, self._clock())

    def tick(self) -> list[dict[str, object]]:
        now = self._clock()
        out: list[dict[str, object]] = []
        for path in list(self._edits):
            out.extend(self._update(path, now))
        return out

    def last_edit(self, path: str) -> float | None:
        times = self._edits.get(path)
        return max(times.values()) if times else None

    def _update(self, path: str, now: float) -> list[dict[str, object]]:
        by_session = self._edits[path]
        for sid in [s for s, t in by_session.items() if t < now - RECENT_S]:
            del by_session[sid]
        if not by_session:
            del self._edits[path]
        level = _level(by_session, now)
        previous = self._levels.get(path, "cleared")
        if level == previous:
            return []
        if level == "cleared":
            self._levels.pop(path, None)
        else:
            self._levels[path] = level
        return [
            {
                "type": "overlap",
                "path": path,
                "level": level,
                "sessions": sorted(by_session),
                "at": iso(now),
            }
        ]


def _level(by_session: dict[str, float], now: float) -> Level:
    if len(by_session) < 2:
        return "cleared"
    times = sorted(by_session.values(), reverse=True)
    for i, newer in enumerate(times):
        if newer < now - ACTIVE_S:
            break
        if any(newer - older <= ACTIVE_S for older in times[i + 1 :]):
            return "active"
    return "recent"
