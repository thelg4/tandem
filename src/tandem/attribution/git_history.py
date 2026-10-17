"""Wire the attribution engine to a real Git repository."""

from __future__ import annotations

from collections.abc import Callable

from tandem.attribution.engine import AttrEdit, Engine, Result
from tandem.attribution.transitions import to_lines
from tandem.git_reader import CommitMeta, GitReader


def blob_lines(reader: GitReader, max_bytes: int) -> Callable[[str], list[str] | None]:
    cache: dict[str, list[str] | None] = {}

    def load(oid: str) -> list[str] | None:
        if oid not in cache:
            data = reader.read_blob(oid, max_bytes=max_bytes)
            if data is None or b"\0" in data:
                cache[oid] = None  # too large or binary: metadata only
            else:
                cache[oid] = to_lines(data.decode("utf-8", "surrogateescape"))
        return cache[oid]

    return load


def attribute(
    reader: GitReader,
    commits: list[CommitMeta],
    edits: list[AttrEdit],
    *,
    max_blob_bytes: int,
    excluded: Callable[[str], bool],
) -> Result:
    load = blob_lines(reader, max_blob_bytes)

    def baseline_lines(commit: str, path: str) -> list[str] | None:
        oid = reader.blob_at(commit, path)
        return None if oid is None else load(oid)

    baseline_time = None
    if commits and commits[0].parents:
        baseline_time = reader.history(commits[0].parents[0], max_count=1)[0].commit_time
    engine = Engine(
        commits,
        reader.file_deltas,
        load,
        baseline_lines,
        baseline_time=baseline_time,
        excluded=excluded,
    )
    return engine.run(edits)
