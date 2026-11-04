from __future__ import annotations

import json

from tandem.live.protocol import clean, decode_command, encode, iso, parse_iso


def test_encode_adds_version_and_strips_control_characters() -> None:
    line = encode({"type": "hello", "x": "a\x1b[31mb", "nested": {"y": ["c\x07d"]}})
    assert "\n" not in line
    assert json.loads(line) == {"v": 1, "type": "hello", "x": "a[31mb", "nested": {"y": ["cd"]}}


def test_clean_truncates() -> None:
    assert clean("x" * 300) == "x" * 200
    assert clean("abc", limit=2) == "ab"


def test_iso_round_trip() -> None:
    assert iso(0.0) == "1970-01-01T00:00:00.000Z"
    ts = parse_iso("2026-11-04T17:01:28.737Z")
    assert ts is not None
    assert iso(ts) == "2026-11-04T17:01:28.737Z"
    assert parse_iso("nope") is None
    assert parse_iso(None) is None


def test_decode_command_accepts_known_commands() -> None:
    started = decode_command(
        '{"type":"pane_started","pane_id":"p1","agent":"codex","pid":12,'
        '"spawned_at":"2026-11-04T17:01:28.737Z"}'
    )
    assert started is not None and started["pid"] == 12
    claude = decode_command(
        '{"type":"pane_started","pane_id":"p2","agent":"claude_code","pid":3,'
        '"claude_session_id":"abc","spawned_at":"2026-11-04T17:01:28.737Z"}'
    )
    assert claude is not None and claude["claude_session_id"] == "abc"
    assert decode_command('{"type":"pane_exited","pane_id":"p1","at":"x"}') is not None
    assert decode_command('{"type":"pane_input","pane_id":"p1","at":"x"}') is not None


def test_decode_command_rejects_bad_input() -> None:
    assert decode_command('{"type":"rm_rf"}') is None
    assert decode_command("not json") is None
    assert decode_command("[1,2]") is None
    base = '{"type":"pane_started","pane_id":"p1",'
    assert decode_command(base + '"agent":"codex","pid":"12"}') is None
    assert decode_command(base + '"agent":"gemini","pid":1}') is None
