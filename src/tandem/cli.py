"""Command-line interface.

Commands: audit, scan, report, session, explain, doctor, demo.
Exit codes: 0 ok (including a marked partial report), 2 invalid input, 3 unavailable
data, 4 processing failure. Code 1 marks a command not implemented in this build.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from tandem import __version__
from tandem.config import load_policy
from tandem.errors import ExitCode, InvalidInputError, TandemError
from tandem.git_reader import MIN_GIT_VERSION, GitReader
from tandem.model.attribution import EvidenceClass
from tandem.model.interactions import WindowMode
from tandem.paths import database_path
from tandem.sources.discovery import iter_source_files, resolve_roots
from tandem.storage import db
from tandem.versions import SCHEMA_VERSION


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _repo_key(common_dir: Path) -> str:
    """Stable local identity for the per-repository database file."""
    return hashlib.sha256(str(common_dir).encode("utf-8", "surrogateescape")).hexdigest()[:24]


def _git_for(path: str, timeout: float = 60.0) -> GitReader:
    target = Path(path).expanduser()
    if not target.exists():
        raise InvalidInputError(f"path does not exist: {target}")
    return GitReader(target if target.is_dir() else target.parent, timeout=timeout)


def _db_path(args: argparse.Namespace, common_dir: Path) -> Path:
    return (
        Path(args.db).expanduser()
        if getattr(args, "db", None)
        else database_path(_repo_key(common_dir))
    )


# -- commands -------------------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    ok = True

    def line(status: str, label: str, detail: str = "") -> None:
        print(f"[{status:^5}] {label}" + (f": {detail}" if detail else ""))

    git = _git_for(args.path)
    try:
        found = git.check_version()
        line("ok", "git", ".".join(map(str, found)))
    except TandemError as exc:
        line("fail", "git", f"{exc} (need >= {'.'.join(map(str, MIN_GIT_VERSION))})")
        return int(ExitCode.UNAVAILABLE)

    try:
        info = git.discover()
        line("ok", "repository", str(info.top_level))
        if info.is_linked_worktree:
            line("info", "worktree", f"linked worktree of {info.common_dir}")
        if info.is_shallow:
            line("warn", "history", "shallow clone; older history will be reported as unavailable")
        if info.head is None:
            line("warn", "history", "no commits yet")
    except TandemError as exc:
        line("fail", "repository", str(exc))
        return int(ExitCode.UNAVAILABLE)

    try:
        load_policy(info.top_level)
        line("ok", "config", "defaults plus global config")
    except TandemError as exc:
        line("fail", "config", str(exc))
        ok = False

    for root in resolve_roots(_opt_path(args.claude_root), _opt_path(args.codex_root)):
        if not root.available:
            line("warn", f"{root.agent.value} logs", f"not found at {root.path}")
            continue
        count = sum(1 for _ in iter_source_files(root))
        line("ok", f"{root.agent.value} logs", f"{count} JSONL file(s) under {root.path}")

    path = _db_path(args, info.common_dir)
    if path.exists():
        try:
            conn = db.connect(path, readonly=True)
            version = db.schema_version(conn)
            conn.close()
            if version > SCHEMA_VERSION:
                line(
                    "fail",
                    "database",
                    f"schema v{version} is newer than supported v{SCHEMA_VERSION}",
                )
                ok = False
            else:
                line("ok", "database", f"{path} (schema v{version})")
        except Exception as exc:  # noqa: BLE001 - doctor reports, it does not crash
            line("fail", "database", f"{path}: {exc}")
            ok = False
    else:
        line("info", "database", f"will be created at {path} on first scan")

    line("info", "adapters", "run `tandem audit` to check what your agent logs record")
    return int(ExitCode.OK if ok else ExitCode.PROCESSING_FAILURE)


def _opt_path(value: str | None) -> Path | None:
    return Path(value).expanduser() if value else None


def _policy_overrides(args: argparse.Namespace) -> dict[str, Any]:
    keys = ("horizon_commits", "window_hours", "window_commits", "window_mode", "confidence")
    return {k: getattr(args, k, None) for k in keys}


def cmd_audit(args: argparse.Namespace) -> int:
    from tandem.audit import audit_root, render_json, render_text

    if args.limit is not None and args.limit < 1:
        raise InvalidInputError("--limit must be at least 1")
    roots = resolve_roots(_opt_path(args.claude_root), _opt_path(args.codex_root))
    audits = [audit_root(root, limit=args.limit) for root in roots]
    print(render_json(audits) if args.format == "json" else render_text(audits))
    if not any(a.root_found for a in audits):
        return int(ExitCode.UNAVAILABLE)
    return int(ExitCode.OK)


def cmd_scan(args: argparse.Namespace) -> int:
    from tandem.ingest import Scanner, ScanOptions

    git = _git_for(args.repo or args.path)
    info = git.discover()
    policy = load_policy(info.top_level, _policy_overrides(args))
    path = _db_path(args, info.common_dir)
    options = ScanOptions(
        ref=args.ref,
        claude_root=_opt_path(args.claude_root),
        codex_root=_opt_path(args.codex_root),
        include_worktrees=args.include_worktrees,
        rebuild=args.rebuild,
        since=args.since,
    )
    summary = Scanner(info.top_level, policy, path).run(options)
    sources = ", ".join(f"{v} {k}" for k, v in sorted(summary.sources.items())) or "none found"
    sessions = ", ".join(f"{v} {k}" for k, v in sorted(summary.sessions.items())) or "none"
    _err(f"tandem: scanned {summary.target_commit[:12]} ({summary.state})")
    _err(f"tandem: sources: {sources}")
    _err(f"tandem: sessions touching this repo: {sessions}; new events: {summary.events_inserted}")
    if summary.quarantined_records:
        _err(f"tandem: {summary.quarantined_records} malformed record(s) quarantined")
    if summary.attribution is not None:
        a = summary.attribution
        _err(
            f"tandem: attribution over {a.commits_analyzed} commit(s): {a.placed} of {a.edits} "
            f"edit(s) placed; {a.verified_lines} of {a.inserted_lines} inserted line(s) verified; "
            f"{a.cross_agent_interactions} cross-agent interaction(s)"
        )
    if args.strict and summary.state != "complete":
        _err("tandem: --strict: scan coverage is partial")
        return int(ExitCode.UNAVAILABLE)
    return int(ExitCode.OK)


def cmd_report(args: argparse.Namespace) -> int:
    from tandem.report.coverage import CAVEATS, Filters, load, render_text
    from tandem.report.json_report import build_report, dumps

    git = _git_for(args.path)
    info = git.discover()
    load_policy(info.top_level, _policy_overrides(args))
    path = _db_path(args, info.common_dir)
    if not path.exists():
        _err("tandem: no scan yet for this repository; run `tandem scan` first")
        return int(ExitCode.UNAVAILABLE)
    wanted_horizon = getattr(args, "horizon_commits", None)
    filters = Filters(agent=args.agent, session=args.session, file=args.file)
    conn = db.connect(path, readonly=True)
    try:
        cov = load(conn, filters)
    finally:
        conn.close()
    if cov is None:
        _err("tandem: no completed scan for this repository; run `tandem scan` first")
        return int(ExitCode.UNAVAILABLE)
    stored = {c["horizon_commits"] for c in cov.durability.values()}
    if wanted_horizon is not None and stored and wanted_horizon not in stored:
        # Horizon changes need a new derived run, not relabeled numbers.
        _err(
            f"tandem: metrics were computed with horizon {min(stored)}; run "
            f"`tandem scan --horizon-commits {wanted_horizon}` to derive them for {wanted_horizon}"
        )
        return int(ExitCode.INVALID_INPUT)
    if args.format == "json":
        report = build_report(
            repository=cov.repository,
            run=cov.run,
            coverage=cov.to_json(),
            metrics=[
                {"scope_kind": "agent", "scope_id": scope, **value}
                for scope, card in cov.durability.items()
                for value in card["metrics"].values()
            ],
            interactions=cov.interactions,
            filters=filters.to_json(),
            caveats=list(CAVEATS),
        )
        print(dumps(report))
    else:
        print(render_text(cov, head=info.head, filters=filters))
    if args.strict and cov.run["state"] != "complete":
        _err("tandem: --strict: latest scan coverage is partial")
        return int(ExitCode.UNAVAILABLE)
    return int(ExitCode.OK)


def _open_latest(args: argparse.Namespace) -> sqlite3.Connection | None:
    git = _git_for(args.path)
    info = git.discover()
    path = _db_path(args, info.common_dir)
    if not path.exists():
        _err("tandem: no scan yet for this repository; run `tandem scan` first")
        return None
    return db.connect(path, readonly=True)


def cmd_session(args: argparse.Namespace) -> int:
    from tandem.report.views import find_session, render_session, session_view

    conn = _open_latest(args)
    if conn is None:
        return int(ExitCode.UNAVAILABLE)
    try:
        found = find_session(conn, args.session_id)
        if isinstance(found, str):
            _err(f"tandem: {found}")
            return int(ExitCode.INVALID_INPUT)
        view = session_view(conn, found)
    finally:
        conn.close()
    print(
        json.dumps(view, indent=2, sort_keys=True)
        if args.format == "json"
        else render_session(view)
    )
    return int(ExitCode.OK)


def cmd_explain(args: argparse.Namespace) -> int:
    from tandem.report.views import explain, find_interaction, render_explain

    conn = _open_latest(args)
    if conn is None:
        return int(ExitCode.UNAVAILABLE)
    try:
        found = find_interaction(conn, args.interaction_id)
        if isinstance(found, str):
            _err(f"tandem: {found}")
            return int(ExitCode.INVALID_INPUT)
        detail = explain(conn, found)
    finally:
        conn.close()
    print(
        json.dumps(detail, indent=2, sort_keys=True)
        if args.format == "json"
        else render_explain(detail)
    )
    return int(ExitCode.OK)


def cmd_demo(args: argparse.Namespace) -> int:
    from tandem.demo import run

    return run(Path(args.keep) if args.keep else None)


# -- parser ---------------------------------------------------------------------------


def _add_source_roots(p: argparse.ArgumentParser) -> None:
    p.add_argument("--claude-root", metavar="DIR", help="Claude Code transcript root")
    p.add_argument("--codex-root", metavar="DIR", help="Codex rollout root")
    p.add_argument("--db", metavar="FILE", help="use this database instead of the default")


def _add_policy(p: argparse.ArgumentParser) -> None:
    p.add_argument("--horizon-commits", type=int, metavar="N", help="survival horizon (default 5)")
    p.add_argument(
        "--window-hours", type=float, metavar="H", help="highlight window hours (default 24)"
    )
    p.add_argument(
        "--window-commits", type=int, metavar="N", help="highlight window commits (default 5)"
    )
    p.add_argument("--window-mode", choices=[m.value for m in WindowMode], help="default: either")
    p.add_argument(
        "--confidence",
        choices=[EvidenceClass.VERIFIED.value, EvidenceClass.SUPPORTED.value],
        help="minimum evidence class for metrics (default verified)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tandem",
        description=(
            "See who changed what, what stuck, and what one agent quietly undid for the other."
        ),
    )
    parser.add_argument("--version", action="version", version=f"tandem {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("doctor", help="check git, repository, log sources and database")
    p.add_argument("path", nargs="?", default=".")
    _add_source_roots(p)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser(
        "audit", help="summarize local agent log structure (no prompts, code, or paths)"
    )
    p.add_argument("--claude-root", metavar="DIR", help="Claude Code transcript root")
    p.add_argument("--codex-root", metavar="DIR", help="Codex rollout root")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("--limit", type=int, metavar="N", help="audit at most N files per agent")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("scan", help="read bounded logs and Git history; derive a report")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--repo", metavar="PATH", help="repository path (overrides PATH)")
    p.add_argument("--ref", default="HEAD", help="revision to analyze (default HEAD)")
    p.add_argument("--since", metavar="REV", help="history start")
    p.add_argument("--include-worktrees", action="store_true", help="include sibling worktree logs")
    p.add_argument("--rebuild", action="store_true", help="re-derive from scratch")
    p.add_argument("--strict", action="store_true", help="treat partial coverage as an error")
    _add_source_roots(p)
    _add_policy(p)
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("report", help="show the latest completed run")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("--agent", help="filter by agent family")
    p.add_argument("--session", help="filter by session id")
    p.add_argument("--file", help="filter by repository-relative path")
    p.add_argument("--strict", action="store_true", help="treat partial coverage as an error")
    p.add_argument("--db", metavar="FILE", help="use this database instead of the default")
    _add_policy(p)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("session", help="show one session's edits, attribution and interactions")
    p.add_argument("session_id", help="Tandem session ID, provider session ID, or a prefix")
    p.add_argument("path", nargs="?", default=".", help="repository (default: current directory)")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("--db", metavar="FILE", help="use this database instead of the default")
    p.set_defaults(func=cmd_session)

    p = sub.add_parser("explain", help="show the full evidence chain for one interaction")
    p.add_argument("interaction_id", help="interaction ID or a unique prefix")
    p.add_argument("path", nargs="?", default=".", help="repository (default: current directory)")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("--db", metavar="FILE", help="use this database instead of the default")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser(
        "demo", help="run on a synthetic repository with invented Claude Code and Codex logs"
    )
    p.add_argument("--keep", metavar="DIR", help="build the demo in DIR and keep it")
    p.set_defaults(func=cmd_demo)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse uses 2 for usage errors, matching INVALID_INPUT
        return int(exc.code or 0)
    try:
        return int(args.func(args))
    except TandemError as exc:
        _err(f"tandem: {exc}")
        return int(exc.exit_code)
    except KeyboardInterrupt:
        _err("tandem: interrupted; the previous complete report is unchanged")
        return int(ExitCode.PROCESSING_FAILURE)
