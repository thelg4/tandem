from __future__ import annotations

from pathlib import Path
from typing import Any

from tandem.live.extract import Activity, Meta, Turn
from tandem.live.state import LiveState

T0 = 1_800_000_000.0


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> float:
        return self.now


def _state(tmp_path: Path) -> tuple[LiveState, Clock, Path]:
    top = tmp_path / "repo"
    top.mkdir()
    clock = Clock()
    st = LiveState(top, clock)
    st.ensure_session("A", "claude_code", pane_id="p1", external=False)
    st.ensure_session("B", "codex", pane_id=None, external=True)
    return st, clock, top


def _edit(top: Path, rel: str, at: float, phase: str = "start") -> Activity:
    return Activity("edit", phase, (str(top / rel),), "Edit", at, "c")  # type: ignore[arg-type]


def _of(msgs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [m for m in msgs if m["type"] == kind]


def test_ensure_session_emits_once_until_changed(tmp_path: Path) -> None:
    top = tmp_path / "r"
    top.mkdir()
    st = LiveState(top, Clock())
    (msg,) = st.ensure_session("A", "claude_code", pane_id="p1", external=False)
    assert msg["type"] == "session" and msg["pane_id"] == "p1" and msg["ended"] is False
    assert st.ensure_session("A", "claude_code", pane_id="p1", external=False) == []
    (ended,) = st.end_session("A")
    assert ended["ended"] is True


def test_reads_never_overlap(tmp_path: Path) -> None:
    st, clock, top = _state(tmp_path)
    for sid in ("A", "B"):
        msgs = st.apply(sid, Activity("read", "start", (str(top / "x.py"),), "Read", T0, "r"))
        assert _of(msgs, "overlap") == []
        assert _of(msgs, "activity")[0]["paths"] == ["x.py"]


def test_overlap_active_then_recent_then_cleared(tmp_path: Path) -> None:
    st, clock, top = _state(tmp_path)
    assert _of(st.apply("A", _edit(top, "x.py", T0 - 30)), "overlap") == []
    (ov,) = _of(st.apply("B", _edit(top, "x.py", T0)), "overlap")
    assert (ov["path"], ov["level"], ov["sessions"]) == ("x.py", "active", ["A", "B"])
    assert st.tick() == []
    clock.now = T0 + 180
    (ov,) = st.tick()
    assert ov["level"] == "recent"
    clock.now = T0 + 16 * 60
    (ov,) = st.tick()
    assert ov["level"] == "cleared"
    assert st.tick() == []


def test_edits_far_apart_are_recent_not_active(tmp_path: Path) -> None:
    st, clock, top = _state(tmp_path)
    st.apply("A", _edit(top, "x.py", T0 - 300))
    (ov,) = _of(st.apply("B", _edit(top, "x.py", T0)), "overlap")
    assert ov["level"] == "recent"


def test_subagent_never_overlaps_parent(tmp_path: Path) -> None:
    st, clock, top = _state(tmp_path)
    st.apply("A", _edit(top, "x.py", T0 - 10))
    assert _of(st.apply("A", _edit(top, "x.py", T0)), "overlap") == []


def test_replay_old_activity_is_silent(tmp_path: Path) -> None:
    st, clock, top = _state(tmp_path)
    old = T0 - 20 * 60
    assert _of(st.apply("A", _edit(top, "x.py", old)), "activity") == []
    assert _of(st.apply("B", _edit(top, "x.py", old)), "overlap") == []
    (status,) = st.sync_pending("A", 1)
    assert status["pending_call"] is True and status["state"] == "idle"


def test_status_changes_only_on_transition(tmp_path: Path) -> None:
    st, clock, top = _state(tmp_path)
    (status,) = st.apply("A", Turn(True, T0))
    assert status == {
        "type": "status",
        "session_id": "A",
        "state": "working",
        "pending_call": False,
        "at": "2027-01-15T08:00:00.000Z",
    }
    assert st.apply("A", Turn(True, T0)) == []
    assert st.sync_pending("A", 0) == []
    assert st.sync_pending("A", 2)[0]["pending_call"] is True
    assert st.sync_pending("A", 1) == []


def test_meta_updates_session(tmp_path: Path) -> None:
    st, clock, top = _state(tmp_path)
    (msg,) = st.apply("A", Meta(title="Fix it", model="m1"))
    assert (msg["type"], msg["title"], msg["model"]) == ("session", "Fix it", "m1")
    assert st.apply("A", Meta(title="Fix it")) == []


def test_paths_outside_repo_dropped(tmp_path: Path) -> None:
    st, clock, top = _state(tmp_path)
    for p in ("/elsewhere/x", str(top / ".." / "x"), str(top)):
        msgs = st.apply("A", Activity("read", "start", (p,), "Read", T0, "r"))
        assert _of(msgs, "activity") == []
    msgs = st.apply("A", Activity("read", "start", (str(top / "a b" / "ü.py"),), "Read", T0, "r"))
    assert _of(msgs, "activity")[0]["paths"] == ["a b/ü.py"]


def test_shell_open_and_edited_recently(tmp_path: Path) -> None:
    st, clock, top = _state(tmp_path)
    st.apply("A", Activity("shell", "start", (), "Bash", T0, "s1"))
    assert st.shell_open() == ["A"]
    st.apply("A", Activity("shell", "done", (), "Bash", T0, "s1"))
    assert st.shell_open() == []
    st.apply("B", _edit(top, "y.py", T0 - 3))
    assert st.edited_recently("y.py", within=5)
    assert not st.edited_recently("y.py", within=2)
    assert not st.edited_recently("z.py", within=5)
