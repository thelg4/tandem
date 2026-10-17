# Tandem

**See who changed what, what stuck, and what one agent quietly undid for the other.**

[![CI](https://github.com/thelg4/tandem/actions/workflows/ci.yml/badge.svg)](https://github.com/thelg4/tandem/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![Dependencies: none](https://img.shields.io/badge/runtime%20deps-0-brightgreen)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Tandem is a local, read-only analysis tool for repositories where both **Claude Code** and **Codex** write code. It reads both agents' session logs and the Git history, works out which agent wrote which lines, and finds the places where one agent rewrote, removed, or reverted the other's work. Then it measures what lasted.

Two agents on one repo produce two separate conversations and one shared history. Neither conversation shows that Codex quietly deleted the helper Claude added an hour ago, and token totals don't say whether any of the work survived. Tandem reconstructs that shared history, and every number it reports links back to the log records and commits behind it.

## Try it without your own logs

```sh
uvx --from git+https://github.com/thelg4/tandem tandem demo
```

The demo builds a throwaway repository with invented Claude Code and Codex logs, then runs the real pipeline on it. In the scripted story, Claude Code writes a rate limiter, Codex rewrites its refill logic, a later Claude session adds a helper that Codex removes, and Codex partly reverts a third change. An excerpt of the report:

```text
Attribution coverage
  12 first-parent commit(s) analyzed from the first commit
  verified lines: 61 of 70 (87%) inserted
  not explained by agent logs: 9

Cross-agent interactions (verified): 3 (8 line(s) affected, 3 recent)
  codex changed claude_code's lines: exact_revert 1, partial_revert 1, rewrite 1
  Review first (review order, not severity):
    e13e12b127f3  limiter.py:24  codex -> claude_code's lines  exact_revert, 4 line(s), 1 commit(s) later, verified  [recent]
    1909bc353ca9  limiter.py:12  codex -> claude_code's lines  partial_revert, 1 line(s), 1 commit(s) later, verified  [recent]
    fa1ba278bcc9  limiter.py:17  codex -> claude_code's lines  rewrite, 3 line(s), 2 commit(s) later, verified  [recent]

Durability (horizon 5 first-parent commits; verified lines only)
  claude_code: 43 mature line(s), 0 pending
    survival at 5: 33 / 43 = 76.7%
    churn received: 8 / 43 = 18.6%
    exact revert rate: 1 / 4 = 25.0%
    tokens per surviving line: 38,120 / 33 = 1,155 per line
    small sample (under 30 mature lines or 3 sessions): not for comparison
```

The demo then shows the evidence behind the first interaction. Interaction IDs depend on where the logs are stored, so they differ on every run.

```text
$ tandem explain e13e12b1
Interaction e13e12b127f3  (rules 0.1.0)
  exact_revert · verified · limiter.py line 24
  codex changed 4 line(s) that claude_code wrote; 1 commit(s) later, 11 min later

Earlier edit: claude_code session claude_code:c2b8f6e1
  Edit at 2026-09-14T09:30:00.000Z; commit da7257f0a61f
  evidence: claude_code.tool_use_result, linked to tool call by call_id
Later edit: codex session codex:x8e4b2d5
  apply_patch at 2026-09-14T09:41:00.000Z; commit 647c78d69a1a
  evidence: codex.file_change, linked to tool call by call_id

Change
  earlier edit replaced 0 line(s) with 4
  later edit replaced 4 line(s) with 0 at line 24
  the later edit put back exactly the text the earlier edit replaced
```

With [pipx](https://pipx.pypa.io): `pipx run --spec git+https://github.com/thelg4/tandem tandem demo`.

## What it measures

- **Provenance.** Each agent edit in a log is rebuilt exactly against the file's state at that moment, then followed through first-parent history line by line. A line gets an evidence class: `verified` (rebuilt exactly and committed), `supported`, `ambiguous`, `unknown`, or `provisional` (not committed yet). Lines no log explains, such as hand edits, shell commands, formatters, or other tools, stay unknown. Tandem never guesses an owner for them.
- **Cross-agent interactions.** When one agent's edit changes lines another agent wrote, Tandem records an interaction with exactly one label: `exact_revert`, `partial_revert`, `rewrite`, `removal`, or `overlap_candidate` (order uncertain). Labels describe what changed, not intent: a rewrite may be an improvement.
- **Durability.** Survival at N commits, churn received, exact revert rate, and tokens per surviving line, per agent and per session. Lines count only once they are N commits old, so recent work is never scored as a success. Every value carries its numerator, denominator, horizon, and exclusions. Counts are pooled, never averaged, and there is no composite score or winner.

The rules are written down: [attribution](docs/attribution.md), [interactions](docs/interactions.md), [metrics](docs/metrics.md).

## Use it on your repository

```sh
pipx install git+https://github.com/thelg4/tandem   # or: uv tool install git+https://github.com/thelg4/tandem
cd path/to/repo
tandem audit     # what your local agent logs contain (structure and counts only, safe to share)
tandem scan      # read the logs and Git history (incremental; reruns are fast)
tandem report    # coverage, attribution, interactions, durability
tandem explain <interaction-id>
tandem session <session-id>
tandem report --format json > tandem-report.json
```

Tandem finds logs in `~/.claude/projects` and `~/.codex/sessions`; `--claude-root` and `--codex-root` override that. It needs Python 3.11+ and Git 2.31+ on macOS or Linux.

> **Keep your Claude Code transcripts.** Claude Code deletes transcripts after 30 days by default. Work from older sessions can't be attributed, so it shows up as unexplained lines. Set `"cleanupPeriodDays": 3650` in `~/.claude/settings.json` to keep them. Tandem warns when an agent's logs start after the history it analyzed.

## Design choices

- **Coverage first, conservatively.** The report starts with how much of the history the logs explain. Headline numbers use verified evidence only; weaker evidence is shown separately.
- **Rebuild from the log, confirm in Git.** Claude Code logs carry the original file and a numbered patch. Codex logs carry numbered unified diffs, and its older patch text is located by content only where the match is unique. An edit is verified only when its before-state matches the file as reconstructed from Git and earlier edits, and its lines then appear in a commit.
- **Read-only and offline.** Git runs with hooks, fsmonitor, external diff, and textconv disabled, and Tandem never writes to the repository or the logs. `scan` and `report` make no network requests, and the test suite fails on any attempted connection.
- **Incremental and crash-safe.** Logs are read up to a fixed snapshot boundary and resumed from a verified prefix. A rewritten log starts a new generation instead of being merged. A run becomes visible only when it completes.
- **Versioned definitions.** Adapters, attribution rules, interaction rules, metric definitions, the schema, and the report format are versioned separately and recorded with every run.

More in [docs/architecture.md](docs/architecture.md).

## Privacy

- Results live in a private SQLite database (mode 0600) in your user data directory, outside the repository. It holds diffs from your code and is not encrypted, so treat it as private.
- Transcript content is untrusted data. Nothing in it is executed, and terminal control characters are escaped before printing.
- JSON exports leave out prompts, absolute home paths, and full diffs by default. `tandem audit` prints structure and counts only.

## Limitations

- Attribution can only be as good as the logs. Edits made through shell commands, other tools, or deleted transcripts are unexplained, not guessed.
- Only first-parent history is walked, so a merged pull request counts as one change at its merge commit.
- Line identity is exact text after line-ending normalization. Whitespace changes count as changes, and moves, copies, and semantically equivalent code are not detected.
- Survival measures persistence, not correctness. Code that lasts may still be wrong, and rewrites may be fixes.
- Undocumented log formats change. Supported versions are listed in [docs/compatibility.md](docs/compatibility.md), and [docs/log-audit.md](docs/log-audit.md) records what each version contains and the results of runs on real logs.

## Status

Alpha (0.1.0). The CLI pipeline is complete and tested on real Claude Code and Codex logs. Planned next: a local web view, test results as evidence, and Windows support.

## Development

```sh
python -m pip install -e ".[dev]"
pytest          # ~190 tests, including end-to-end runs on real Git repositories
ruff check . && ruff format --check .
mypy            # strict
```

CI runs on macOS and Linux with Python 3.11 to 3.13. The core has no runtime dependencies.

## License

[MIT](LICENSE)
