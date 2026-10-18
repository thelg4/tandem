"""Platform storage locations.

Tandem-owned data lives outside the analyzed repository, with one database per
repository identity. Nothing here touches the network or the repository.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "tandem"


def user_data_dir() -> Path:
    override = os.environ.get("TANDEM_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


def user_config_dir() -> Path:
    override = os.environ.get("TANDEM_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


def database_path(repo_identity: str) -> Path:
    """One database per repository identity (a local UUID, never a remote URL)."""
    safe = "".join(ch for ch in repo_identity if ch.isalnum() or ch in "-_")
    if not safe:
        raise ValueError("repository identity must contain safe characters")
    return user_data_dir() / "repos" / f"{safe}.sqlite3"


def default_claude_root() -> Path:
    """Commonly ~/.claude/projects. Explicit roots always win."""
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(config_dir).expanduser() if config_dir else Path.home() / ".claude"
    return base / "projects"


def default_codex_root() -> Path:
    """Commonly ~/.codex/sessions. Explicit roots always win."""
    codex_home = os.environ.get("CODEX_HOME")
    base = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
    return base / "sessions"
