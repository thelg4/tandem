# Tandem

**See who changed what, what stuck, and what one agent quietly undid for the other.**

Tandem is a local-first analysis tool for developers who run Claude Code and Codex on the same Git repository. It combines both agents' session histories with Git evidence to show which agent changed which code, where their edits intersected, and what happened to those changes afterward.

The repository is the unit of analysis, not the session. Every finding links back to the transcript events and Git revisions that support it.

> **Status: pre-alpha.** `tandem scan` reads Claude Code and Codex logs, matches each agent's edits to Git history, attributes lines conservatively, and detects cross-agent interactions (rewrites, removals, exact and partial reverts). `tandem report` shows coverage, attributed lines, and interactions in review order; `tandem explain` and `tandem session` show the evidence behind them. Maturity-aware durability metrics are next.

## Why

Two coding agents on one repo produce two separate conversations and one shared history. An agent can replace the other's implementation without any handoff, and ordinary token or cost totals don't show whether the work lasted. Tandem reconstructs that history and keeps the difference between observed behavior and inferred intent.

## What it reports (planned)

- **Provenance:** which agent and session introduced each line range, with an evidence class (verified, supported, ambiguous, unknown, provisional). Tandem records the strongest attribution the evidence supports and never promises an owner for every line.
- **Cross-agent interactions:** rewrites, removals, and exact reverts of one agent's lines by the other, each with an `explain` view of both source events and the before/after text. Labels are neutral: a rewrite is not called a fix or a mistake.
- **Durability scorecards:** survival at N commits, churn received, exact revert rate, and tokens per surviving line. Each value comes with its numerator, denominator, horizon, and exclusions. There is no composite "winner" score.

High survival describes persistence. It does not prove correctness or show that one model is better.

## Quick start (intended workflow)

```sh
pip install -e .            # distribution name not yet published
tandem doctor .             # check git, repository, log sources, database
tandem audit                # what your agent logs record (structure only, safe to share)
tandem scan .               # read bounded logs and Git history
tandem report .             # coverage, scorecards, interactions
tandem explain <interaction-id>
tandem report . --format json > tandem-report.json
```

Requires Python 3.11+ and Git 2.31+ on macOS or Linux.

## Privacy and safety

- `scan` and `report` make **no network requests**: no telemetry, update checks, pricing lookups, or Git fetches. The test suite fails on any outbound connection attempt.
- Tandem **never modifies** your logs, repository files, refs, index, or worktree. Git is run read-only with hooks, fsmonitor, external diff, and textconv disabled.
- Transcripts and patches are treated as untrusted data. Nothing in them is executed or replayed.
- Results live in a private SQLite database (mode 0600) in your user data directory, outside the analyzed repository. Diffs can contain sensitive text, so treat the database as private local data. It is not encrypted at rest.
- JSON exports leave out prompts, absolute home paths, and full diffs by default.

## Limitations

- Attribution depends on what the agents' logs record. Edits made through shell commands are only attributed when the resulting content and its cause can be established.
- Survival is exact-text: whitespace and indentation changes count as changes.
- Intra-file moves, copies, and semantic equivalence are not detected in the first release.
- Only the first-parent history is walked. With merge commits (for example GitHub pull request merges), each merged branch is analyzed as a single change at its merge commit.
- Supported agent CLI versions will be listed in [docs/compatibility.md](docs/compatibility.md) once the adapters are validated against real logs.

## Development

```sh
python -m pip install -e ".[dev]"
pytest
ruff check .
mypy
```

Project layout follows the module boundaries in [docs/architecture.md](docs/architecture.md). Attribution rules are in [docs/attribution.md](docs/attribution.md), interaction rules in [docs/interactions.md](docs/interactions.md), and metric definitions in [docs/metrics.md](docs/metrics.md).

## License

Not yet chosen.
