from __future__ import annotations

import sqlite3
import stat
from pathlib import Path

import pytest

from tandem.errors import ScanBusyError, SchemaCompatibilityError
from tandem.storage import db
from tandem.versions import SCHEMA_VERSION


def test_migrate_creates_schema(tmp_path: Path) -> None:
    path = tmp_path / "t.sqlite3"
    conn = db.connect(path)
    assert db.migrate(conn, path) == SCHEMA_VERSION
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "sources",
        "events",
        "edits",
        "lineage",
        "interactions",
        "analysis_runs",
        "metrics",
        "latest_run",
    } <= tables


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "t.sqlite3"
    conn = db.connect(path)
    db.migrate(conn, path)
    assert db.migrate(conn, path) == SCHEMA_VERSION


def test_foreign_keys_enforced(tmp_path: Path) -> None:
    path = tmp_path / "t.sqlite3"
    conn = db.connect(path)
    db.migrate(conn, path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO worktrees (worktree_id, repo_id, path) VALUES ('w', 'missing', '/x')"
        )


def test_metric_status_rejects_unknown_values(tmp_path: Path) -> None:
    path = tmp_path / "t.sqlite3"
    conn = db.connect(path)
    db.migrate(conn, path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO sources (source_id, provider, path, parser_version, status) "
            "VALUES ('s', 'codex', '/x', '0', 'bogus')"
        )


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "t.sqlite3"
    conn = db.connect(path)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(SchemaCompatibilityError):
        db.migrate(conn, path)


def test_database_file_is_private(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "t.sqlite3"
    db.connect(path).close()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_second_writer_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "t.sqlite3"
    with db.writer_lock(path), pytest.raises(ScanBusyError), db.writer_lock(path):
        pass
