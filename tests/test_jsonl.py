from __future__ import annotations

import json
from pathlib import Path

from tandem.sources.jsonl import SourceState, read_incremental


def _write(path: Path, *lines: str, mode: str = "w") -> None:
    with path.open(mode, encoding="utf-8") as fh:
        fh.write("".join(lines))


def rec(n: int) -> str:
    return json.dumps({"n": n}) + "\n"


def test_reads_complete_records(tmp_path: Path) -> None:
    src = tmp_path / "s.jsonl"
    _write(src, rec(1), rec(2))
    result = read_incremental(src)
    assert result.state is SourceState.NEW
    assert [r.value["n"] for r in result.records] == [1, 2]
    assert result.processed_offset == src.stat().st_size


def test_rescan_same_snapshot_adds_nothing(tmp_path: Path) -> None:
    src = tmp_path / "s.jsonl"
    _write(src, rec(1), rec(2))
    first = read_incremental(src)
    second = read_incremental(src, first.checkpoint)
    assert second.state is SourceState.UNCHANGED
    assert second.records == []


def test_append_reads_only_new_records(tmp_path: Path) -> None:
    src = tmp_path / "s.jsonl"
    _write(src, rec(1))
    first = read_incremental(src)
    _write(src, rec(2), mode="a")
    second = read_incremental(src, first.checkpoint)
    assert second.state is SourceState.APPENDED
    assert [r.value["n"] for r in second.records] == [2]


def test_unfinished_tail_is_retried_next_scan(tmp_path: Path) -> None:
    src = tmp_path / "s.jsonl"
    _write(src, rec(1), '{"n": 2')
    first = read_incremental(src)
    assert [r.value["n"] for r in first.records] == [1]
    assert first.pending_tail_bytes > 0
    _write(src, "}\n", mode="a")
    second = read_incremental(src, first.checkpoint)
    assert [r.value["n"] for r in second.records] == [2]


def test_malformed_interior_record_is_quarantined(tmp_path: Path) -> None:
    src = tmp_path / "s.jsonl"
    _write(src, rec(1), "not json\x1b[31m\n", rec(3))
    result = read_incremental(src)
    assert [r.value["n"] for r in result.records] == [1, 3]
    assert len(result.quarantined) == 1
    assert "\x1b" not in result.quarantined[0].excerpt  # terminal controls escaped


def test_rewritten_prefix_triggers_rebuild(tmp_path: Path) -> None:
    src = tmp_path / "s.jsonl"
    _write(src, rec(1), rec(2))
    first = read_incremental(src)
    _write(src, rec(9), rec(2), rec(3))
    second = read_incremental(src, first.checkpoint)
    assert second.state is SourceState.REWRITTEN
    assert [r.value["n"] for r in second.records] == [9, 2, 3]


def test_truncation_triggers_rebuild(tmp_path: Path) -> None:
    src = tmp_path / "s.jsonl"
    _write(src, rec(1), rec(2))
    first = read_incremental(src)
    _write(src, rec(1))
    assert read_incremental(src, first.checkpoint).state is SourceState.REWRITTEN


def test_snapshot_limit_excludes_later_bytes(tmp_path: Path) -> None:
    src = tmp_path / "s.jsonl"
    _write(src, rec(1))
    limit = src.stat().st_size
    _write(src, rec(2), mode="a")
    result = read_incremental(src, snapshot_limit=limit)
    assert [r.value["n"] for r in result.records] == [1]


def test_missing_source_is_unavailable(tmp_path: Path) -> None:
    result = read_incremental(tmp_path / "gone.jsonl")
    assert result.state is SourceState.MISSING
    assert result.checkpoint is None


def test_oversized_record_is_quarantined(tmp_path: Path) -> None:
    src = tmp_path / "s.jsonl"
    big = json.dumps({"blob": "x" * 500}) + "\n"
    _write(src, rec(1), big, rec(3))
    result = read_incremental(src, max_record_bytes=100)
    assert [r.value["n"] for r in result.records] == [1, 3]
    assert result.quarantined[0].reason == "record_too_large"
