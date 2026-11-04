# Tandem Live Workspace — design

Status: draft for review · 2026-11-04

## Intent

Running more than one coding agent on a repository is becoming common. Today Tandem explains afterward what Claude Code and Codex did to each other's work. The Live Workspace shows it while it happens. It is a desktop app where the real `claude` and `codex` CLIs run in terminal panes and behave exactly as they do in any terminal. Next to them, a live map of the repository lights up as each agent reads and edits files. You can see moment to moment what each agent is doing, and you are warned when two agents are editing the same file before one of them overwrites the other.

Audience for now: the author, running it from a clone of the public repo. Packaging for other people comes later.

## Goals

1. **Moment-to-moment awareness.** For each agent pane: what it is doing (working, waiting on you, idle) and which files it is reading and editing.
2. **Collision warning.** A visible warning when two sessions are editing the same file, early enough to stop one of them.
3. **Normal CLIs.** The agents run unmodified in real terminal panes. You use them exactly as you would in iTerm.

## Non-goals (v1)

- Building our own chat UI or driving agents through SDKs.
- Installers, code signing, auto-update, or Windows support.
- Running `scan`/`report` from inside the app. The existing CLI keeps doing that. A history view is a later step.
- Line-level collision detection. v1 is file-level.
- Injecting per-session settings or hooks at launch. This was offered as a later option for lower latency.

## Anti-goals (failure even if it technically works)

These are agreed with the author and are the acceptance bar.

1. **Typing feels worse than a normal terminal.** Lag, broken shortcuts, wrong colors, bad resize.
2. **It cries wolf.** Overlap alarms for reads, or for edits that are not close in time.
3. **It's busy to look at.** Constant motion in peripheral vision.
4. **It breaks Tandem's promises.** Any network request, or any write to agent configuration, logs or the repository.
5. **It lags.** The map should update within about a second of the agent acting.

## Decided shape

| Decision | Choice | Whose call |
|---|---|---|
| Platform | Electron desktop app | Author (asked, accepted recommendation) |
| Agent windows | Real `claude` / `codex` CLIs in terminal panes | Author |
| Layout | **B**: terminal panes stacked on the left, live repo map on the right | Author (picked from mockups A/B/C) |
| Signals | Agent colors (Claude orange `#e0825f`, Codex green `#3fbf96`). Status dot: pulsing = working, blinking yellow = waiting on you, dim = idle. Overlapping files flash red. | Author (accepted mockup) |
| Who watches | A Python `tandem watch` process does all the detection. Electron only displays what it reports. | Author (accepted recommendation) |
| Data sources | Session logs followed as they are written, plus `git status` polling. No agent config changes. | Claude, accepted |
| Pane count | Any number of panes of either agent | Claude |
| Distribution | Run from the repo (`npm run dev`) | Author ("for now, just me") |

## Architecture

```
┌──────────────── Electron app (app/) ─────────────────┐
│ renderer: React                                      │
│   PaneStack ── xterm.js panes                        │
│   RepoMap  ── treemap, glows, overlap flashes        │
│   Banner   ── collision warnings                     │
│ main: node-pty (spawns claude / codex in the repo)   │
│       watcher bridge (spawns `tandem watch`)         │
└───────────────┬──────────────────────────────────────┘
                │ NDJSON over stdin/stdout
┌───────────────▼──────────────────────────────────────┐
│ tandem watch (src/tandem/live/)   Python, stdlib only│
│   tail: poll log sizes, read new complete records    │
│   extract: raw records → Activity (per provider)     │
│   state: per-session status + per-file touches       │
│   overlap: file-level collision rules                │
│   gitpoll: dirty paths not explained by a tool call  │
└──────────────────────────────────────────────────────┘
   reads ~/.claude/projects/**.jsonl, ~/.codex/sessions/**.jsonl, git (read-only)
```

### Python: `tandem watch`

New package `src/tandem/live/`. It stays stdlib-only like the rest of the core, keeps the same strict mypy and ruff settings, and makes no network requests.

- **Tailing.** Poll candidate log files every 250 ms by size and mtime. Read new complete records with the existing `sources.jsonl.read_incremental` and its checkpoints, so an unfinished trailing line waits for the next poll. On a `REWRITTEN` state, re-read from 0 and rebuild that session's live state.
- **Which files to tail.**
  - *Claude panes*: the app launches `claude --session-id <uuid>`, so the transcript is the `<uuid>.jsonl` found under the Claude root. Search by file name only. Folder names are never trusted (ING-02). Subagent transcripts live in `<uuid>/subagents/*.jsonl` next to it (records have `isSidechain: true`). They are tailed too, and their activity counts as the pane's session.
  - *Codex panes*: the app can't choose the session id. The watcher watches for a new rollout file created after the pane was spawned whose `session_meta.cwd` is inside the repo, and binds it to the oldest unbound Codex pane. Records with `source.subagent` are child threads of the bound session, not new panes.
  - *External sessions*: any Claude or Codex log modified in the last 10 minutes whose recorded cwd is inside the repo is also tailed, for example Codex Desktop or a `claude` started in iTerm. These appear on the map and in a compact "outside the app" list, with no pane.
  - Discovery rescans the roots every 2 s, looking only at files with mtime in the last 10 minutes.
- **Extraction** (`live/extract.py`). These are deliberately separate from the scan adapters. The adapters parse whole generations and only produce edits from results. Live tracking needs *intent* at call time: "Claude is about to edit X". The extractors are small and incremental, and each one keeps pending call state per session.
  - Claude: `assistant` `tool_use` blocks. `Read`/`Glob`/`Grep`/`NotebookRead` → **read** (path from `file_path`/`path`/`notebook_path`). `Edit`/`Write`/`MultiEdit`/`NotebookEdit` → **edit** with status `pending`, then `done`/`failed` on the matching `tool_result` (`is_error`). `Bash` → **shell**. A `user` text record → **prompt**, which marks a turn start. A `system` record with `subtype: turn_duration`, or an assistant `stop_reason: end_turn` with no pending calls → turn done. An `ai-title` record (`aiTitle`) sets the session title.
  - Codex: `response_item` `function_call`/`custom_tool_call`. `apply_patch` → **edit** for each `*** Add/Update/Delete File:` path in the patch text, plus `Move to:`. Shell tools (`exec` code-mode cells, the most common in 0.15x, plus `exec_command`, `shell`, `shell_command`, `local_shell`) → **shell**. `apply_patch` paths may be relative (`../../x/y.tsx`). Resolve them against the call's `workdir` if present, otherwise against the session cwd. `event_msg` `item_completed` `FileChange` → **edit done** per path. Its `changes` keys are absolute paths, and it also catches edits made inside `exec` cells. `*_output` → completes the call. `event_msg` `task_started` → turn start; `task_complete`/`turn_aborted` → turn done. `user_message` → prompt.
  - Paths are made repo-relative against the session cwd. Paths outside the repo are dropped.
- **Session status** (from logs). `working` while a turn is open. `idle` after turn done. `pending_call` is a flag on the session while a call has no result. The app combines this with terminal output to show **waiting on you** (below).
- **Overlap rules** (`live/overlap.py`). These are chosen to satisfy anti-goal 2.
  - Only **edits** count, pending or done. Reads never raise an overlap.
  - Two *different sessions* (any agents, including two Claude panes) that both edited the same path within the last **15 minutes** → `overlap` level `recent` (steady amber outline).
  - If both edits are within **2 minutes** of each other → level `active` (red pulse plus a banner naming the file and the sessions). `active` decays to `recent` after 2 minutes without a new edit by either session.
  - An overlap clears when either session's last edit to that path is older than 15 minutes, or when the session has exited and been gone for 15 minutes.
- **Git polling** (`live/gitpoll.py`). Every 2 s, `git status --porcelain=v1 -z` runs through `GitReader` (hooks, fsmonitor and textconv disabled, read-only). A path that becomes dirty or changes, with no edit activity from any session in the previous 5 s, is reported as an **unattributed change**: a grey glow. If exactly one session had a shell command open at that moment, it is reported as `likely: <session>`, a hatched glow in that agent's color. This is a hint, never an attribution. File content isn't hashed. A dirty path also fires again when its mtime changes between polls.
- **Repo tree.** At start and after HEAD changes, send `git ls-files -z` plus untracked-not-ignored files (`--others --exclude-standard`) with byte sizes, capped at 20 000 entries.

### Protocol (NDJSON)

All messages are one JSON object per line with `"v": 1` and a `"type"`. Timestamps are UTC ISO-8601 strings with milliseconds.

Watcher → app:

| type | fields |
|---|---|
| `hello` | `version`, `repo_root`, `claude_root`, `codex_root` |
| `tree` | `files: [{path, size}]`, `truncated: bool` |
| `session` | `session_id`, `agent` (`claude_code`/`codex`), `pane_id` or null, `external: bool`, `ended: bool`, `cwd`, `model` or null, `title` (Claude's `ai-title`, max 80 chars; Codex null in v1) |
| `status` | `session_id`, `state` (`working`/`idle`), `pending_call: bool`, `at` |
| `activity` | `session_id`, `kind` (`read`/`edit`/`shell`/`prompt`), `phase` (`start`/`done`/`failed`), `paths: [rel]`, `tool`, `at`, `id` |
| `overlap` | `path`, `level` (`active`/`recent`/`cleared`), `sessions: [session_id]`, `at` |
| `change` | `path`, `likely_session` or null, `at` |
| `diagnostic` | `level` (`info`/`warn`), `code`, `message` |

App → watcher (stdin):

| type | fields |
|---|---|
| `pane_started` | `pane_id`, `agent`, `pid`, `claude_session_id` (Claude only), `spawned_at` |
| `pane_input` | `pane_id`, `at` (sent at most once per second per pane) |
| `pane_exited` | `pane_id`, `at` |

Content never crosses the protocol: no prompt text at all (the title is the agent's own generated title), no diffs, and no command text. (`tool` is the tool name, for example `Edit`.) Control characters are stripped from every string field (NFR-03).

### Electron app (`app/`)

- **Stack** (Claude's call): electron-vite, TypeScript (strict), React 18, `@xterm/xterm` with fit and WebGL addons, `node-pty`, `d3-hierarchy` for the treemap layout, Vitest for unit tests. No other UI framework. CSS lives in plain modules.
- **Security.** `contextIsolation: true`, `nodeIntegration: false`, sandboxed renderer, a strict CSP (`default-src 'self'`), and no remote URLs loaded. The preload script exposes only a typed bridge: create, write, resize and kill a pane, plus a watcher event stream.
- **Launching.** `npm run dev -- --repo <path>`. With no `--repo`, a folder picker opens. The app spawns `tandem watch --repo <path>` using `TANDEM_PYTHON` (default `python3 -m tandem`).
- **Panes.** A toolbar with `+ Claude` and `+ Codex`. Panes stack vertically on the left with draggable dividers, and the left column is resizable against the map (default 55 / 45). Each pane runs in a `node-pty` login shell environment with `cwd` set to the repo and the user's `$SHELL` environment. Claude is spawned as `claude --session-id <uuid>`. Codex uses `codex` from `PATH`, falling back to `/Applications/ChatGPT.app/Contents/Resources/codex`, or `TANDEM_CODEX_BIN` if set. Closing a pane sends SIGHUP to the pty. Keyboard input goes straight to xterm, and the app binds no shortcuts that collide with terminal ones; only `Cmd+T` (new pane menu) and `Cmd+W` (close focused pane) are bound.
- **Waiting on you.** This is a heuristic and is labeled as one in the code. A session shows *waiting on you* when the watcher says `pending_call: true` and the pane's pty has produced no output for 1.5 s. Both CLIs animate a spinner while a tool runs, so a frozen screen with an open call is almost always an approval prompt. It also shows when the CLI rings the terminal bell. It clears on the next keystroke into that pane or the next output.
- **Pane header.** Agent color, a status dot, the session title, and the file currently being edited or read. When a pane is involved in an `active` overlap, its header border turns red and names the file.
- **Repo map.** A squarified treemap of the repo by file size. A directory with more than 40 descendants is shown collapsed until you click into it, with breadcrumbs to go back up. Each cell records the last touch per session:
  - read → a soft outline pulse in the agent color, fading over 20 s;
  - edit → a fill in the agent color, fading over 90 s down to a faint residual tint that lasts while the session is alive;
  - unattributed change → a grey fill; `likely` changes are hatched in that agent's color;
  - overlap `recent` → a steady amber outline; `active` → a red pulse that also bubbles up to the collapsed parent directory.
  - Hover shows the path, the last touch per session ("Claude · edited 40 s ago"), and the overlap state. Clicking a file shows the same in a side card. Clicking a directory zooms in.
- **Motion budget** (anti-goal 3). Animation plays only when state changes, and nothing loops except `working` dots and `active` overlaps. With `prefers-reduced-motion` or a "Calm" toggle in the toolbar, pulses become static color changes.
- **External sessions.** A compact list above the map: "Codex Desktop · working · 3 files". Their touches appear on the map like any other session.

## Sessions, scope and time (Claude's calls, settled after the builder check)

- **A pane runs the agent itself.** The pty process is `$SHELL -l -c 'exec claude --session-id <uuid>'` (or `exec <codex>`). That gives login-shell `PATH` and env, and the pty pid is the agent's pid. When the agent exits, the pane shows "exited · press Enter to restart". It never drops to a bare shell.
- **A pane follows its agent across `/clear`, `/resume` and `/new`.**
  - *Claude*: Claude Code keeps `~/.claude/sessions/<pid>.json`, which holds the current `sessionId` and `cwd` for that process. This is an undocumented file, so treat it as optional and never fail if it's missing. The watcher re-reads the pane's file on every discovery pass and rebinds the pane when `sessionId` changes. If the file is missing, it falls back to the `--session-id` transcript.
  - *Codex*: a new rollout file whose `session_meta.originator` is not `Codex Desktop` and whose cwd is in scope binds to the oldest Codex pane with no session. If every Codex pane already has a session, it rebinds the Codex pane with the most recent keystroke (the app sends throttled `pane_input` messages). Misbinding when a terminal `codex` starts in the same repo at the same moment is accepted for v1.
  - A pane's previous session is marked `ended` and stays on the map until its touches decay.
- **Subagents are part of their parent session.** Claude `subagents/*.jsonl` and Codex child threads (`session_meta.session_id` is the root thread) are folded into the root session's id. A parent and its subagent never raise an overlap with each other.
- **Scope is one worktree.** `--repo` is resolved to its `git rev-parse --show-toplevel`. A session is in scope when its cwd is inside that top level. Linked worktrees of the same repository are different files and are out of scope. Paths are relative to the top level.
- **Windows run on record time.** Overlap windows (2 and 15 min) and glow decay compare each activity's record timestamp with the current wall clock. Activity with no timestamp uses arrival time. At startup, and after a `REWRITTEN` rebuild, logs are replayed with their own timestamps. So a collision from 20 minutes ago shows nothing, one from 5 minutes ago shows `recent` (amber, no banner), and only one still within 2 minutes raises the banner.

## Testing

- Python: unit tests for both extractors, built on synthetic record sequences shaped like the existing fixtures; tailing across partial lines and rewrites; overlap timing with an injected clock; Codex pane binding; gitpoll in a temp repo; a protocol test that runs `watch` against a temp repo and fixture logs and asserts the message stream. The suite's no-network guard stays in force.
- App: Vitest for the renderer state reducer (protocol messages → map/pane state, decay, waiting heuristic) and treemap grouping. `tsc --noEmit` must pass. A manual smoke check covers launching, opening a Claude pane, editing a file through it and watching the map light up.

## Left to the builder

The exact visual polish, component breakdown, how the reducer stores decay (timestamps, computed at render), divider implementation, and the folder picker UX.
