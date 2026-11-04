"""Per-session live state: status, open shell calls, and the messages they produce.

Paths arrive absolute from the extractors and leave relative to the worktree top level;
anything outside it is dropped. Activity older than the overlap window (record time)
updates status and pending calls but produces no map or overlap messages, so replaying
logs at startup never raises fresh alarms.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from tandem.live.extract import Activity, Meta, Signal, Turn
from tandem.live.overlap import RECENT_S, OverlapTracker
from tandem.live.protocol import iso

Msg = dict[str, object]


@dataclass(slots=True)
class _Session:
    agent: str
    pane_id: str | None
    external: bool
    ended: bool = False
    cwd: str | None = None
    model: str | None = None
    title: str | None = None
    working: bool = False
    pending: bool = False
    shells: set[str] = field(default_factory=set)


class LiveState:
    def __init__(self, top: Path, clock: Callable[[], float]) -> None:
        self.top = top
        self._top = str(top)
        self._clock = clock
        self._sessions: dict[str, _Session] = {}
        self.overlaps = OverlapTracker(clock)

    def has(self, sid: str) -> bool:
        return sid in self._sessions

    def pane_of(self, sid: str) -> str | None:
        s = self._sessions.get(sid)
        return s.pane_id if s else None

    def ensure_session(
        self, sid: str, agent: str, *, pane_id: str | None, external: bool
    ) -> list[Msg]:
        s = self._sessions.get(sid)
        if s is None:
            self._sessions[sid] = _Session(agent, pane_id, external)
            return [self._session_msg(sid)]
        if (s.pane_id, s.external, s.ended) == (pane_id, external, False):
            return []
        s.pane_id, s.external, s.ended = pane_id, external, False
        return [self._session_msg(sid)]

    def end_session(self, sid: str) -> list[Msg]:
        s = self._sessions.get(sid)
        if s is None or s.ended:
            return []
        s.ended, s.pane_id, s.working, s.pending = True, None, False, False
        s.shells.clear()
        return [self._session_msg(sid)]

    def apply(self, sid: str, sig: Signal) -> list[Msg]:
        s = self._sessions.get(sid)
        if s is None:
            return []
        if isinstance(sig, Meta):
            return self._meta(sid, s, sig)
        now = self._clock()
        at = sig.at if sig.at is not None else now
        if isinstance(sig, Turn):
            if sig.working == s.working:
                return []
            s.working = sig.working
            return [self._status_msg(sid, at)]
        return self._activity(sid, s, sig, at, now)

    def sync_pending(self, sid: str, pending: int) -> list[Msg]:
        s = self._sessions.get(sid)
        if s is None or s.pending == (pending > 0):
            return []
        s.pending = pending > 0
        return [self._status_msg(sid, self._clock())]

    def tick(self) -> list[Msg]:
        return self.overlaps.tick()

    def edited_recently(self, path: str, within: float) -> bool:
        last = self.overlaps.last_edit(path)
        return last is not None and last >= self._clock() - within

    def shell_open(self) -> list[str]:
        return sorted(sid for sid, s in self._sessions.items() if s.shells)

    # -- internals ----------------------------------------------------------------

    def _activity(self, sid: str, s: _Session, act: Activity, at: float, now: float) -> list[Msg]:
        if act.kind == "shell" and act.call_id:
            if act.phase == "start":
                s.shells.add(act.call_id)
            else:
                s.shells.discard(act.call_id)
        if at < now - RECENT_S:
            return []
        paths = [rel for p in act.paths if (rel := self._relative(p)) is not None]
        if act.paths and not paths:
            return []
        out: list[Msg] = [
            {
                "type": "activity",
                "session_id": sid,
                "kind": act.kind,
                "phase": act.phase,
                "paths": paths,
                "tool": act.tool,
                "at": iso(at),
                "id": act.call_id,
            }
        ]
        if act.kind == "edit" and act.phase != "failed":
            for path in paths:
                out.extend(self.overlaps.edit(path, sid, at))
        return out

    def _relative(self, path: str) -> str | None:
        norm = os.path.normpath(path)
        if not norm.startswith(self._top + os.sep):
            return None
        return os.path.relpath(norm, self._top)

    def _meta(self, sid: str, s: _Session, meta: Meta) -> list[Msg]:
        before = (s.cwd, s.model, s.title)
        s.cwd = meta.cwd or s.cwd
        s.model = meta.model or s.model
        s.title = (meta.title or "")[:80] or s.title
        return [] if (s.cwd, s.model, s.title) == before else [self._session_msg(sid)]

    def _session_msg(self, sid: str) -> Msg:
        s = self._sessions[sid]
        return {
            "type": "session",
            "session_id": sid,
            "agent": s.agent,
            "pane_id": s.pane_id,
            "external": s.external,
            "ended": s.ended,
            "cwd": s.cwd,
            "model": s.model,
            "title": s.title,
        }

    def _status_msg(self, sid: str, at: float) -> Msg:
        s = self._sessions[sid]
        return {
            "type": "status",
            "session_id": sid,
            "state": "working" if s.working else "idle",
            "pending_call": s.pending,
            "at": iso(at),
        }
