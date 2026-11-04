"""SQLite connection, migrations, and the single-writer lock."""

from __future__ import annotations

import fcntl
import os
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import IO

from tandem.errors import ScanBusyError, SchemaCompatibilityError, StorageError
from tandem.versions import SCHEMA_VERSION


def _migrations() -> list[tuple[int, str]]:
    pkg = resources.files("tandem.storage") / "migrations"
    found = []
    for entry in pkg.iterdir():
        name = entry.name
        if name.endswith(".sql") and name[:4].isdigit():
            found.append((int(name[:4]), entry.read_text(encoding="utf-8")))
    return sorted(found)


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _secure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)


def connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    """Open the database, creating it with restrictive permissions if needed."""
    if readonly:
        if not path.exists():
            raise StorageError(f"no Tandem database at {path}; run `tandem scan` first")
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        _secure_parent(path)
        if not path.exists():
            fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(fd)
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode=WAL")  # concurrent readers, one writer
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    return conn


def migrate(conn: sqlite3.Connection, db_path: Path | None = None) -> int:
    """Apply pending migrations transactionally, backing up an existing database first."""
    current = schema_version(conn)
    if current > SCHEMA_VERSION:
        raise SchemaCompatibilityError(
            f"database schema v{current} is newer than this Tandem (supports v{SCHEMA_VERSION}); "
            "upgrade Tandem or point --db at a different file"
        )
    pending = [(v, sql) for v, sql in _migrations() if v > current]
    if not pending:
        return current
    if db_path is not None and current > 0 and db_path.exists():
        backup = db_path.with_suffix(db_path.suffix + f".v{current}.bak")
        shutil.copy2(db_path, backup)
        os.chmod(backup, 0o600)
    for version, sql in pending:
        try:
            conn.execute("BEGIN")
            # executescript would COMMIT implicitly; run statements one at a time instead.
            for statement in _split_sql(sql):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {int(version)}")
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            conn.execute("ROLLBACK")
            raise StorageError(f"migration {version:04d} failed: {exc}") from exc
    return schema_version(conn)


def _split_sql(script: str) -> Iterator[str]:
    buf: list[str] = []
    for line in script.splitlines():
        stripped = line.split("--", 1)[0]
        buf.append(stripped)
        candidate = "\n".join(buf).strip()
        if candidate.endswith(";") and sqlite3.complete_statement(candidate):
            yield candidate
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        yield tail


@contextmanager
def writer_lock(db_path: Path) -> Iterator[None]:
    """Single writer per database. A second scan exits with an actionable message."""
    _secure_parent(db_path)
    lock_path = db_path.with_suffix(db_path.suffix + ".lock")
    fh: IO[str] = lock_path.open("a")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ScanBusyError(
                f"another `tandem scan` is running against {db_path}; wait for it to finish"
            ) from exc
        yield
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()
