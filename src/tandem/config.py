"""Declarative policy configuration.

Precedence: explicit CLI option > user-approved repo config > global user config
> defaults. Configuration is TOML and cannot execute code. Repo configuration may
change filters and metric policies but can never widen local log roots or enable
execution.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

from tandem.errors import InvalidInputError
from tandem.model.attribution import EvidenceClass
from tandem.model.interactions import WindowMode
from tandem.paths import user_config_dir

REPO_CONFIG_NAME = ".tandem.toml"

DEFAULT_EXCLUDE_GLOBS: tuple[str, ...] = (
    # Lockfiles
    "**/package-lock.json",
    "**/yarn.lock",
    "**/pnpm-lock.yaml",
    "**/poetry.lock",
    "**/uv.lock",
    "**/Cargo.lock",
    "**/go.sum",
    "**/Gemfile.lock",
    "**/composer.lock",
    # Vendored and generated
    "**/node_modules/**",
    "**/vendor/**",
    "**/third_party/**",
    "**/dist/**",
    "**/build/**",
    "**/*.min.js",
    "**/*.min.css",
    "**/*_pb2.py",
    "**/*.generated.*",
)

# Keys a repository-local config is never allowed to set.
REPO_FORBIDDEN_KEYS = frozenset(
    {"claude_root", "codex_root", "include_worktrees", "allow_execution"}
)


@dataclass(frozen=True, slots=True)
class Policy:
    horizon_commits: int = 5
    window_hours: float = 24.0
    window_commits: int = 5
    window_mode: WindowMode = WindowMode.EITHER
    confidence: EvidenceClass = EvidenceClass.VERIFIED
    include_worktrees: bool = False
    exclude_globs: tuple[str, ...] = DEFAULT_EXCLUDE_GLOBS
    max_blob_bytes: int = 2 * 1024 * 1024
    max_record_bytes: int = 8 * 1024 * 1024
    git_timeout_seconds: float = 60.0
    max_history_commits: int = 2000
    """Newest first-parent commits analyzed; older history is reported as truncated."""
    claude_root: Path | None = None
    codex_root: Path | None = None
    allow_execution: bool = False
    """Always False for now; only an explicit CLI choice will be able to change it."""
    sources: tuple[str, ...] = field(default=("defaults",))

    def validate(self) -> Policy:
        if self.max_history_commits < 1:
            raise InvalidInputError("max_history_commits must be >= 1")
        if self.horizon_commits < 1:
            raise InvalidInputError("horizon_commits must be >= 1")
        if self.window_hours <= 0 or self.window_commits < 1:
            raise InvalidInputError("interaction window values must be positive")
        if self.confidence not in (EvidenceClass.VERIFIED, EvidenceClass.SUPPORTED):
            raise InvalidInputError("confidence filter must be 'verified' or 'supported'")
        return self


_FIELD_NAMES = {f.name for f in fields(Policy)} - {"sources"}


def _coerce(key: str, value: Any) -> Any:
    if key == "window_mode":
        return WindowMode(value)
    if key == "confidence":
        return EvidenceClass(value)
    if key == "exclude_globs":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise InvalidInputError("exclude_globs must be a list of strings")
        return tuple(value)
    if key in ("claude_root", "codex_root"):
        return Path(str(value)).expanduser()
    return value


def _apply(policy: Policy, values: dict[str, Any], origin: str, *, repo: bool) -> Policy:
    unknown = set(values) - _FIELD_NAMES
    if unknown:
        raise InvalidInputError(f"{origin}: unknown setting(s): {', '.join(sorted(unknown))}")
    if repo:
        forbidden = set(values) & REPO_FORBIDDEN_KEYS
        if forbidden:
            raise InvalidInputError(
                f"{origin}: repository config cannot set {', '.join(sorted(forbidden))}"
            )
    try:
        coerced = {k: _coerce(k, v) for k, v in values.items()}
    except ValueError as exc:
        raise InvalidInputError(f"{origin}: {exc}") from exc
    return replace(policy, **coerced, sources=(*policy.sources, origin))


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise InvalidInputError(f"{path}: invalid TOML: {exc}") from exc
    section = data.get("tandem", data)
    if not isinstance(section, dict):
        raise InvalidInputError(f"{path}: [tandem] must be a table")
    return section


def load_policy(
    repo_root: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
    *,
    trust_repo_config: bool = False,
    global_config: Path | None = None,
) -> Policy:
    """Resolve the effective policy.

    ``trust_repo_config`` stands in for user approval of a repository's
    ``.tandem.toml``. Persisted approval (keyed by config digest) is a later work item.
    """
    policy = Policy()
    gpath = global_config or (user_config_dir() / "config.toml")
    if gpath.is_file():
        policy = _apply(policy, _read_toml(gpath), str(gpath), repo=False)
    if repo_root is not None and trust_repo_config:
        rpath = repo_root / REPO_CONFIG_NAME
        if rpath.is_file():
            policy = _apply(policy, _read_toml(rpath), str(rpath), repo=True)
    overrides = {k: v for k, v in (cli_overrides or {}).items() if v is not None}
    if overrides:
        policy = _apply(policy, overrides, "cli", repo=False)
    return policy.validate()
