"""``tandem watch``: follow agent logs and stream live activity as NDJSON.

The desktop app runs this as a child process. Commands arrive on stdin (panes started,
exited, typed into); EOF on stdin means the app is gone and the watcher exits. Output
goes to stdout, one message per line (see ``protocol``). Read-only throughout.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from tandem import __version__
from tandem.errors import TandemError
from tandem.git_reader import GitReader
from tandem.live.extract import ClaudeExtractor, CodexExtractor, Meta
from tandem.live.gitpoll import DirtyWatcher, head, repo_tree
from tandem.live.protocol import decode_command, encode, iso, parse_iso
from tandem.live.state import LiveState, Msg
from tandem.live.tail import Tail, claude_registry, peek_cwd, recent_logs
from tandem.model.events import AgentFamily

DISCOVERY_S = 2.0
TICK_S = 1.0
RECENT_LOG_S = 600.0
UNATTRIBUTED_GRACE_S = 5.0
BIND_SLACK_S = 2.0
INPUT_BIND_S = 10.0
_EOF = object()


@dataclass(slots=True)
class _Pane:
    pane_id: str
    agent: str
    pid: int
    spawned_at: float
    session: str | None = None
    last_input: float = 0.0


@dataclass(slots=True)
class _Tracked:
    tail: Tail
    sid: str | None
    """None: looked at and out of scope; never read again."""
    extractor: ClaudeExtractor | CodexExtractor | None = None


@dataclass(slots=True)
class Watcher:
    top: Path
    out: TextIO
    claude_root: Path
    codex_root: Path
    claude_config: Path
    clock: Callable[[], float]
    git: GitReader
    state: LiveState = field(init=False)
    panes: dict[str, _Pane] = field(default_factory=dict)
    files: dict[Path, _Tracked] = field(default_factory=dict)
    codex_seen: dict[str, tuple[float, str | None]] = field(default_factory=dict)
    """Codex session id -> (created, originator), for binding panes started later."""
    dirty: DirtyWatcher | None = None
    head: str | None = None

    def __post_init__(self) -> None:
        self.state = LiveState(self.top, self.clock)

    # -- output -------------------------------------------------------------------

    def emit(self, msgs: Iterable[Msg]) -> None:
        for msg in msgs:
            self.out.write(encode(msg) + "\n")

    def diagnostic(self, level: str, code: str, message: str) -> None:
        self.emit([{"type": "diagnostic", "level": level, "code": code, "message": message}])

    def start(self) -> None:
        self.emit(
            [
                {
                    "type": "hello",
                    "version": __version__,
                    "repo_root": str(self.top),
                    "claude_root": str(self.claude_root),
                    "codex_root": str(self.codex_root),
                }
            ]
        )
        self.send_tree()
        for root, name in ((self.claude_root, "Claude Code"), (self.codex_root, "Codex")):
            if not root.is_dir():
                self.diagnostic("info", "log_root_missing", f"{name} log folder not found: {root}")
        try:
            self.dirty = DirtyWatcher(self.git, self.top)
        except TandemError as exc:
            self.diagnostic("warn", "git_status_failed", str(exc))

    def send_tree(self) -> None:
        try:
            files, truncated = repo_tree(self.git, self.top)
            self.head = head(self.git)
        except TandemError as exc:
            self.diagnostic("warn", "git_tree_failed", str(exc))
            return
        self.emit([{"type": "tree", "files": files, "truncated": truncated}])

    # -- commands -----------------------------------------------------------------

    def handle(self, cmd: dict[str, object]) -> None:
        pane_id = str(cmd["pane_id"])
        kind = cmd["type"]
        if kind == "pane_started":
            pid = cmd["pid"]
            spawned = parse_iso(cmd.get("spawned_at")) or self.clock()
            pane = _Pane(pane_id, str(cmd["agent"]), pid if isinstance(pid, int) else 0, spawned)
            self.panes[pane_id] = pane
            claude_sid = cmd.get("claude_session_id")
            if pane.agent == "claude_code" and isinstance(claude_sid, str):
                self._bind(pane, f"claude_code:{claude_sid}")
            elif pane.agent == "codex":
                self._adopt_codex(pane)
            self.discover()
        elif kind == "pane_exited":
            gone = self.panes.pop(pane_id, None)
            if gone is not None and gone.session is not None:
                self.emit(self.state.end_session(gone.session))
        elif kind == "pane_input" and pane_id in self.panes:
            self.panes[pane_id].last_input = self.clock()

    def _bind(self, pane: _Pane, sid: str) -> None:
        if pane.session == sid:
            return
        if pane.session is not None:
            self.emit(self.state.end_session(pane.session))
        pane.session = sid
        self.emit(self.state.ensure_session(sid, pane.agent, pane_id=pane.pane_id, external=False))

    # -- discovery ----------------------------------------------------------------

    def in_scope(self, cwd: str | None) -> bool:
        if not cwd:
            return False
        return self.state.scope.contains(cwd)

    def discover(self) -> None:
        registry = claude_registry(self.claude_config)
        for pane in self.panes.values():
            entry = registry.get(pane.pid) if pane.agent == "claude_code" else None
            if entry is not None and (entry[1] is None or self.in_scope(entry[1])):
                self._bind(pane, f"claude_code:{entry[0]}")
        since = self.clock() - RECENT_LOG_S
        for path in recent_logs(self.claude_root, since):
            if path not in self.files:
                self._track_claude(path)
        for path in recent_logs(self.codex_root, since):
            if path not in self.files:
                self._track_codex(path)

    def _pane_for(self, sid: str) -> _Pane | None:
        return next((p for p in self.panes.values() if p.session == sid), None)

    def _track_claude(self, path: Path) -> None:
        sidechain = path.parent.name == "subagents"
        raw = path.parent.parent.name if sidechain else path.stem
        sid = f"claude_code:{raw}"
        tail = Tail(path, AgentFamily.CLAUDE_CODE)
        if self._pane_for(sid) is None and not self.state.has(sid):
            cwd, _ = peek_cwd(path, AgentFamily.CLAUDE_CODE)
            if cwd is None:
                return  # first records carry no cwd yet; look again on the next pass
            if not self.in_scope(cwd):
                self.files[path] = _Tracked(tail, None)
                return
            self.emit(self.state.ensure_session(sid, "claude_code", pane_id=None, external=True))
        self.files[path] = _Tracked(tail, sid, ClaudeExtractor(sidechain=sidechain))

    def _track_codex(self, path: Path) -> None:
        tail = Tail(path, AgentFamily.CODEX)
        cwd, meta = peek_cwd(path, AgentFamily.CODEX)
        if meta is None or not self.in_scope(cwd):
            # A rollout without session_meta yet is retried on the next pass.
            if meta is not None:
                self.files[path] = _Tracked(tail, None)
            return
        root = meta.get("session_id") or meta.get("id")
        sid = f"codex:{root if isinstance(root, str) and root else path.stem}"
        if sid not in self.codex_seen:
            created = parse_iso(meta.get("timestamp")) or self.clock()
            originator = meta.get("originator")
            self.codex_seen[sid] = (created, originator if isinstance(originator, str) else None)
        if not self.state.has(sid):
            pane = self._codex_pane(meta)
            if pane is not None:
                self._bind(pane, sid)
            else:
                self.emit(self.state.ensure_session(sid, "codex", pane_id=None, external=True))
        self.files[path] = _Tracked(tail, sid, CodexExtractor())

    def _adopt_codex(self, pane: _Pane) -> None:
        """Bind an already-seen terminal Codex session that started with this pane."""
        bound = {p.session for p in self.panes.values()}
        for sid, (created, originator) in sorted(self.codex_seen.items(), key=lambda i: i[1][0]):
            if (
                sid not in bound
                and originator != "Codex Desktop"
                and created >= pane.spawned_at - BIND_SLACK_S
            ):
                self._bind(pane, sid)
                return

    def _codex_pane(self, meta: dict[str, Any]) -> _Pane | None:
        if meta.get("originator") == "Codex Desktop":
            return None
        created = parse_iso(meta.get("timestamp")) or self.clock()
        candidates = [
            p
            for p in self.panes.values()
            if p.agent == "codex" and created >= p.spawned_at - BIND_SLACK_S
        ]
        # A pane typed into just before the rollout appeared is the one that started it.
        typed = sorted(
            (p for p in candidates if p.last_input and abs(created - p.last_input) <= INPUT_BIND_S),
            key=lambda p: p.last_input,
            reverse=True,
        )
        unbound = [p for p in candidates if p.session is None]
        typed_unbound = [p for p in typed if p.session is None]
        if typed_unbound:
            return typed_unbound[0]
        if unbound:
            return min(unbound, key=lambda p: p.spawned_at)
        return typed[0] if typed else None

    # -- polling ------------------------------------------------------------------

    def poll_tails(self) -> None:
        pending: dict[str, int] = {}
        for tracked in self.files.values():
            if tracked.sid is None or tracked.extractor is None:
                continue
            try:
                records, rewritten = tracked.tail.read()
            except OSError as exc:
                self.diagnostic("warn", "log_read_failed", f"stopped following a log: {exc}")
                tracked.sid = None
                continue
            if rewritten:
                tracked.extractor = tracked.extractor.fresh()
            batch: list[Msg] = []
            for rec in records:
                for sig in tracked.extractor.feed(rec):
                    if isinstance(sig, Meta) and sig.root_session is not None:
                        sig = Meta(cwd=sig.cwd, model=sig.model)
                    batch.extend(self.state.apply(tracked.sid, sig))
            self.emit(coalesce(batch))
            pending[tracked.sid] = pending.get(tracked.sid, 0) + tracked.extractor.pending
        for sid, count in pending.items():
            self.emit(self.state.sync_pending(sid, count))

    def poll_git(self) -> None:
        try:
            current = head(self.git)
            if current != self.head:
                self.send_tree()
            changed = self.dirty.poll() if self.dirty is not None else []
        except TandemError as exc:
            self.diagnostic("warn", "git_poll_failed", str(exc))
            return
        shells = self.state.shell_open()
        for path in changed:
            if self.state.edited_recently(path, UNATTRIBUTED_GRACE_S):
                continue
            self.emit(
                [
                    {
                        "type": "change",
                        "path": path,
                        "likely_session": shells[0] if len(shells) == 1 else None,
                        "at": iso(self.clock()),
                    }
                ]
            )


def coalesce(msgs: list[Msg]) -> list[Msg]:
    """Keep only the last ``status`` and ``session`` message per session in one batch.

    Replaying a long log at startup otherwise sends every historical turn transition.
    """
    last: dict[tuple[object, object], int] = {}
    for i, msg in enumerate(msgs):
        if msg["type"] in ("status", "session"):
            last[(msg["type"], msg.get("session_id"))] = i
    return [
        m
        for i, m in enumerate(msgs)
        if m["type"] not in ("status", "session") or last[(m["type"], m.get("session_id"))] == i
    ]


def _reader(inp: TextIO, q: queue.Queue[object]) -> None:
    try:
        for line in inp:
            cmd = decode_command(line)
            if cmd is not None:
                q.put(cmd)
    except (OSError, ValueError):
        pass
    q.put(_EOF)


def run_watch(
    repo: Path,
    out: TextIO,
    inp: TextIO,
    *,
    claude_root: Path,
    codex_root: Path,
    claude_config: Path,
    clock: Callable[[], float] = time.time,
    poll_s: float = 0.25,
    max_loops: int | None = None,
) -> int:
    git = GitReader(repo)
    top = git.discover().top_level
    w = Watcher(top, out, claude_root, codex_root, claude_config, clock, git)
    commands: queue.Queue[object] = queue.Queue()
    threading.Thread(target=_reader, args=(inp, commands), daemon=True).start()
    try:
        w.start()
        w.discover()
        last_discovery = last_tick = time.monotonic()
        loops = 0
        while True:
            stop = False
            while True:
                try:
                    cmd = commands.get_nowait()
                except queue.Empty:
                    break
                if cmd is _EOF:
                    stop = True
                    break
                assert isinstance(cmd, dict)
                w.handle(cmd)
            mono = time.monotonic()
            if mono - last_discovery >= DISCOVERY_S:
                w.discover()
                w.poll_git()
                last_discovery = mono
            w.poll_tails()
            if mono - last_tick >= TICK_S:
                w.emit(w.state.tick())
                last_tick = mono
            out.flush()
            loops += 1
            if stop or (max_loops is not None and loops >= max_loops):
                return 0
            time.sleep(poll_s)
    except BrokenPipeError:
        return 0
