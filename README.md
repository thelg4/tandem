# Tandem

**See who changed what, what stuck, and what one agent quietly undid for the other.**

[![CI](https://github.com/thelg4/tandem/actions/workflows/ci.yml/badge.svg)](https://github.com/thelg4/tandem/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![Dependencies: none](https://img.shields.io/badge/runtime%20deps-0-brightgreen)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Tandem is a local, read-only analysis tool for repositories where both **Claude Code** and **Codex** write code. It reads both agents' session logs and the Git history, works out which agent wrote which lines, and finds where one agent rewrote, removed, or reverted the other's work. Then it measures what lasted.

Two agents on one repo produce two separate conversations and one shared history. Neither conversation tells you that Codex deleted the helper Claude added an hour ago, and token totals don't say whether any of the work survived. Tandem reconstructs that shared history, and every finding links back to the log records and commits behind it.

## Try it without your own logs

```sh
uvx --from git+https://github.com/thelg4/tandem tandem demo
```

The demo builds a throwaway repository with invented Claude Code and Codex logs and runs the real pipeline on it. In the scripted story, Claude Code writes a rate limiter, and then Codex:

- **rewrites** Claude's refill logic,
- **exactly reverts** a `retry_after` helper a later Claude session added (deleting an added block restores the earlier state, so it counts as a revert),
- **partially reverts** an edit where Claude raised a default and documented it: Codex restores the old default and keeps the docs.

Tandem finds all three:

```text
Cross-agent interactions (verified): 3 (8 line(s) affected, 3 recent)
  Codex changed Claude Code's lines: exact_revert 1, partial_revert 1, rewrite 1
  same agent, different session (counted separately): 2
  Review first (review order, not severity):
    813cb12f29c8  limiter.py:24  exact_revert: Codex changed 4 of Claude Code's lines, 1 commit(s) later, verified  [recent]
    c653f704b7ad  limiter.py:12  partial_revert: Codex changed 1 of Claude Code's lines, 1 commit(s) later, verified  [recent]
    3f9c3b23f9f2  limiter.py:17  rewrite: Codex changed 3 of Claude Code's lines, 2 commit(s) later, verified  [recent]
    Run `tandem explain <id>` for the evidence.
```

Then it shows the evidence for one of them, with both edits traced to their log records and commits:

```text
$ tandem explain 813cb12f

Interaction 813cb12f29c8  (rules 0.1.0)
  exact_revert · verified · limiter.py line 24
  Codex changed 4 line(s) that Claude Code wrote; 1 commit(s) later, 11 min later
  tags: recent

Earlier edit: Claude Code session claude_code:c2b8f6e1
  Edit at 2026-09-14T09:30:00.000Z; commit da7257f0a61f
  evidence: claude_code.tool_use_result, linked to tool call by call_id
  log: /tmp/tandem-demo-…/logs/claude/projects/-demo-limiter/c2b8f6e1.jsonl @ byte 1257
Later edit: Codex session codex:x8e4b2d5
  apply_patch at 2026-09-14T09:41:00.000Z; commit 647c78d69a1a
  evidence: codex.file_change, linked to tool call by call_id
  log: /tmp/tandem-demo-…/logs/codex/sessions/2026/09/14/rollout-2026-09-14T09-00-00-x8e4b2d5.jsonl @ byte 1113

Change
  earlier edit replaced 0 line(s) with 4
  later edit replaced 4 line(s) with 0 at line 24
  the later edit removed exactly the lines the earlier edit added
…  (both edits' diffs, read back from the logs)
```

Interaction IDs and temporary paths differ on every run. With [pipx](https://pipx.pypa.io) instead of uv: `pipx run --spec git+https://github.com/thelg4/tandem tandem demo`.

## What it measures

- **Provenance.** Each agent edit in a log is rebuilt exactly against the file's state at that moment, then followed through the repository's first-parent history (the main line, not merged branches) line by line. A line is `verified` when its edit was rebuilt exactly and the line reached a commit. Weaker evidence is `supported`, `ambiguous`, or `unknown`, and lines not committed yet are `provisional`. Lines no log explains, such as hand edits, shell commands, formatters, or other tools, stay unknown. Tandem never guesses an owner for them.
- **Interactions.** When one agent's edit changes lines another agent wrote, Tandem records an interaction with exactly one label: `exact_revert`, `partial_revert`, `rewrite`, `removal`, or `overlap_candidate` (order uncertain). Labels describe what changed, not intent: a rewrite may be an improvement, and a revert may be the right call.
- **Durability.** For each agent and each session, Tandem measures what lasted over a horizon of N commits (default 5):
  - **survival:** lines still present N commits after they landed;
  - **churn received:** lines the other agent changed within N commits;
  - **exact revert rate:** edits the other agent fully undid;
  - **tokens per surviving line.**

  A line counts only once N commits have landed after it ("mature"), so recent work is never scored as a success. Every value carries its numerator, denominator, and exclusions, counts are pooled rather than averaged, and there is no composite score or winner.

The demo's durability section:

```text
Durability (horizon 5 first-parent commits; verified lines only)
  claude_code: 43 mature line(s), 0 pending
    survival at 5: 33 / 43 = 76.7%
    churn received: 8 / 43 = 18.6%
    exact revert rate: 1 / 4 = 25.0%
    tokens per surviving line: 38,120 / 33 = 1,155 per line
    3 interaction(s) received affecting 8 line(s); 1 partial revert(s)
    small sample (under 30 mature lines or 3 sessions): not for comparison
  codex: 18 mature line(s), 0 pending
    …
```

The full rules: [attribution](docs/attribution.md), [interactions](docs/interactions.md), [metrics](docs/metrics.md).

## Use it on your repository

```sh
pipx install git+https://github.com/thelg4/tandem   # or: uv tool install git+https://github.com/thelg4/tandem
cd path/to/repo
tandem doctor    # check Git, the repository, log locations, and the database
tandem audit     # what your agent logs contain (structure and counts only, safe to share)
tandem scan      # read the logs and Git history (incremental; reruns are fast)
tandem report    # coverage, attribution, interactions, durability
tandem explain <interaction-id>
tandem session <session-id>
```

Tandem reads logs from `~/.claude/projects` and `~/.codex/sessions`, honoring `CLAUDE_CONFIG_DIR` and `CODEX_HOME`. `--claude-root` and `--codex-root` override both. It needs Python 3.11+ and Git 2.31+ on macOS or Linux.

> **Keep your Claude Code transcripts.** Claude Code deletes transcripts after 30 days by default. Work from deleted sessions can't be attributed, so it shows up as unexplained lines. To keep them, set `"cleanupPeriodDays": 3650` in `~/.claude/settings.json` (or `$CLAUDE_CONFIG_DIR/settings.json`). Tandem warns when an agent's logs start after the history it analyzed.

## Design choices

- **Coverage first, conservatively.** Reports start with how much of the history the logs explain. Headline numbers use verified evidence only, and weaker evidence is listed separately.
- **Rebuild from the log, confirm in Git.**
  - Claude Code logs carry the original file and a numbered patch.
  - Codex logs carry numbered unified diffs. Its older patch text has no line numbers, so it is located by content, and only where the match is unique.
  - An edit is verified only when its before-state matches the file as reconstructed from Git and earlier edits.
- **Read-only and offline.** Git runs with hooks, fsmonitor, external diff, and textconv disabled. Tandem never writes to your repository or logs, and `scan` and `report` make no network requests. The test suite fails on any connection attempt.
- **Incremental, versioned, crash-safe.** Logs are resumed from a verified prefix, and a run is visible only when it completes. Rules, metric definitions, and schema are versioned separately and recorded with every run. See [architecture](docs/architecture.md).

## Live workspace (experimental)

A desktop app that runs the real `claude` and `codex` CLIs in terminal panes. Next to them is a live map of the repository that lights up as each agent reads and edits files. When two sessions edit the same file within two minutes of each other, you get a warning before one overwrites the other.

```sh
pip install -e .                  # the app runs `python3 -m tandem watch`
cd app && npm install
TANDEM_REPO=/path/to/repo npm run dev
```

To build a standalone app, run `npm run dist` in `app/`. On macOS that makes `dist/Tandem-<version>-arm64.dmg` and a zipped `Tandem.app`. On Linux it makes an AppImage. The app carries its own copy of the watcher, so it only needs Python 3.11+ as `python3` on your login `PATH`. Opened without `--repo` or `TANDEM_REPO`, it asks for a repository. The build is unsigned, so on macOS open it the first time with right-click → Open.

The app follows Apple's design language and the system's light or dark appearance. On macOS the title bar folds into the toolbar, the window is translucent and selection uses your accent color. On Linux it uses a normal window frame and solid backgrounds. Linux builds are untested so far.

- **Panes.** `⌘T` opens a Claude pane and `⇧⌘T` a Codex pane (`Ctrl+T` and `Ctrl+Shift+T` on Linux). Each pane is the agent itself, running in your login shell with the repository as its working directory. Type into it exactly as you would in a terminal. A pane follows its agent across `/clear`, `/resume` and `/new`.
- **Map.** Agent colors: orange for Claude, green for Codex. A fill marks an edit and fades over 90 s to a faint tint. An outline marks a read and fades over 20 s. Amber means two sessions edited the file in the last 15 minutes. A red pulse plus the banner means they edited it within 2 minutes of each other. Grey hatching marks a change that no tool call explains, for example one made by a shell command. **Calm** turns the pulses into static colors.
- **Outside the app.** Claude Code and Codex sessions started elsewhere, such as Codex Desktop or an iTerm window, show on the map too, as long as their working directory is in this worktree.
- **Settings.** `TANDEM_PYTHON` sets the interpreter (default: `python3` on your login `PATH`). `TANDEM_CODEX_BIN` sets the Codex binary (default: `codex` on your login `PATH`, then the copy bundled in ChatGPT.app). `TANDEM_CLAUDE_BIN` sets the Claude binary.
- **Privacy.** `tandem watch` follows the same rules as `scan`. It reads logs and Git read-only and makes no network requests. No prompt text, diffs or command text leave the watcher. The app loads nothing remote.
- **Limits.** Reads done through shell commands (`cat`, `grep`) are invisible. Edits made through shell commands appear only as grey "changed" cells. "Needs you" is a heuristic: an open tool call with no terminal output for 1.5 s, or a terminal bell.

## Privacy

- Results live in a private SQLite database (mode 0600) in your user data directory, outside the repository. It holds diffs of your code and is not encrypted, so treat it as private.
- Log content is untrusted data. Nothing in it is executed, and terminal control characters are escaped before printing.
- `report --format json` leaves out prompts and diffs. `explain` and `session` include code from the diffs. Home directories are always written as `~`. `tandem audit` prints structure and counts only.

## Limitations

- Attribution is only as good as the logs. Edits made through shell commands or other tools, or recorded in deleted transcripts, are left unexplained, never guessed.
- Only first-parent history is walked, so a merged pull request counts as one change at its merge commit.
- Line identity is exact text after line-ending normalization. Whitespace changes count as changes, and moves, copies, and equivalent rewrites are not detected.
- Survival measures persistence, not correctness. Code that lasts may still be wrong, and rewrites may be fixes.
- Log formats are undocumented and change. [docs/compatibility.md](docs/compatibility.md) lists supported versions, and [docs/log-audit.md](docs/log-audit.md) records what each version contains.

## Status

Alpha (0.1.0). The full CLI pipeline works and is tested end to end.

On real logs, the adapters have read 144 desktop Claude Code transcripts and 13 Codex rollouts with no malformed records. On a 66-commit project, Tandem matched 218 of 251 Codex edits to the commits they landed in and verified 3,943 lines. That project's Claude Code transcripts had already expired, so a cross-agent interaction has not yet been observed on real data; the demo shows what one looks like. The [log audit](docs/log-audit.md) has the details.

Planned: a local web view, test results as evidence, and Windows support.

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
