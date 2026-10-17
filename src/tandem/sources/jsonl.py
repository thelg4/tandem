"""Incremental, bounded JSONL reading (spec §3 ING-06, ING-07, "Stable scan boundary").

- Reads only up to a byte limit captured when the scan began.
- Resumes after the last complete record; an unfinished trailing record is
  left for the next scan, never parsed as a partial.
- Malformed interior records are quarantined with a bounded excerpt; reading continues.
- A changed prefix (truncation, replacement, rewrite) is detected through a
  fingerprint of the bytes already processed, so callers rebuild instead of
  silently appending duplicates.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

EXCERPT_BYTES = 240
PREFIX_FINGERPRINT_BYTES = 64 * 1024


class SourceState(StrEnum):
    NEW = "new"
    UNCHANGED = "unchanged"
    APPENDED = "appended"
    REWRITTEN = "rewritten"
    """Truncated, replaced, or prefix changed: rebuild this source generation (ING-07)."""
    MISSING = "missing"
    """Marked unavailable; previous evidence is kept, not erased (ING-07)."""


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """What a previous scan recorded for one source file."""

    processed_offset: int
    prefix_digest: str
    """sha256 of the first min(processed_offset, PREFIX_FINGERPRINT_BYTES) bytes."""


@dataclass(frozen=True, slots=True)
class Record:
    offset: int
    length: int
    value: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Quarantined:
    offset: int
    length: int
    reason: str
    excerpt: str


@dataclass(slots=True)
class ReadResult:
    state: SourceState
    snapshot_limit: int
    records: list[Record] = field(default_factory=list)
    quarantined: list[Quarantined] = field(default_factory=list)
    processed_offset: int = 0
    pending_tail_bytes: int = 0

    @property
    def checkpoint(self) -> Checkpoint | None:
        return None if self.state is SourceState.MISSING else self._checkpoint

    _checkpoint: Checkpoint | None = None


def prefix_digest(path: Path, upto: int) -> str:
    n = min(upto, PREFIX_FINGERPRINT_BYTES)
    h = hashlib.sha256()
    with path.open("rb") as fh:
        h.update(fh.read(n))
    return h.hexdigest()


def classify(path: Path, previous: Checkpoint | None, snapshot_limit: int) -> SourceState:
    if previous is None:
        return SourceState.NEW
    if snapshot_limit < previous.processed_offset:
        return SourceState.REWRITTEN
    if prefix_digest(path, previous.processed_offset) != previous.prefix_digest:
        return SourceState.REWRITTEN
    if snapshot_limit == previous.processed_offset:
        return SourceState.UNCHANGED
    return SourceState.APPENDED


def _excerpt(raw: bytes) -> str:
    text = raw[:EXCERPT_BYTES].decode("utf-8", "replace")
    # Escape control characters so diagnostics cannot drive the terminal (NFR-03).
    return "".join(ch if ch.isprintable() else f"\\x{ord(ch):02x}" for ch in text)


def _iter_lines(
    path: Path, start: int, limit: int, max_record: int
) -> Iterator[tuple[int, bytes, bool]]:
    """Yield (offset, line_without_newline, complete). Oversized lines are yielded truncated."""
    with path.open("rb") as fh:
        fh.seek(start)
        offset = start
        while offset < limit:
            budget = min(limit - offset, max_record + 1)
            chunk = fh.readline(budget)
            if not chunk:
                break
            complete = chunk.endswith(b"\n")
            if not complete and len(chunk) > max_record:
                # Oversized record: skip to the next newline within the limit.
                skipped = len(chunk)
                while not complete and offset + skipped < limit:
                    more = fh.readline(min(limit - offset - skipped, 1 << 20))
                    if not more:
                        break
                    skipped += len(more)
                    complete = more.endswith(b"\n")
                yield offset, chunk[:max_record], complete and False
                offset += skipped
                continue
            yield offset, chunk.rstrip(b"\r\n") if complete else chunk, complete
            offset += len(chunk)


def read_incremental(
    path: Path,
    previous: Checkpoint | None = None,
    *,
    snapshot_limit: int | None = None,
    max_record_bytes: int = 8 * 1024 * 1024,
) -> ReadResult:
    """Read new complete records from ``path``.

    ``snapshot_limit`` should be the file size captured at scan start; records past
    it belong to the next scan.
    """
    if not path.is_file():
        return ReadResult(state=SourceState.MISSING, snapshot_limit=0)

    limit = path.stat().st_size if snapshot_limit is None else snapshot_limit
    state = classify(path, previous, limit)
    start = 0 if state in (SourceState.NEW, SourceState.REWRITTEN) else previous.processed_offset  # type: ignore[union-attr]
    result = ReadResult(state=state, snapshot_limit=limit, processed_offset=start)

    for offset, raw, complete in _iter_lines(path, start, limit, max_record_bytes):
        length_with_newline = None
        if not complete:
            if len(raw) >= max_record_bytes:
                result.quarantined.append(
                    Quarantined(offset, len(raw), "record_too_large", _excerpt(raw))
                )
                continue
            # Unfinished trailing record: retry on the next scan.
            result.pending_tail_bytes = limit - offset
            break
        length_with_newline = len(raw) + 1
        result.processed_offset = offset + length_with_newline
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            result.quarantined.append(
                Quarantined(
                    offset, len(raw), f"malformed_json: {exc.__class__.__name__}", _excerpt(raw)
                )
            )
            continue
        if not isinstance(value, dict):
            result.quarantined.append(Quarantined(offset, len(raw), "not_an_object", _excerpt(raw)))
            continue
        result.records.append(Record(offset, len(raw), value))

    result._checkpoint = Checkpoint(
        processed_offset=result.processed_offset,
        prefix_digest=prefix_digest(path, result.processed_offset),
    )
    return result
