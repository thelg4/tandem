# Live Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An Electron app that runs real `claude` / `codex` CLIs in terminal panes beside a live repo map, fed by a new `tandem watch` Python process.

**Architecture:** `src/tandem/live/` follows agent logs incrementally, turns raw records into activity signals, keeps per-session and per-file state with file-level overlap rules, polls `git status`, and streams NDJSON on stdout. `app/` (electron-vite, React) spawns panes with node-pty and the watcher as a child process. It renders what the watcher reports.

**Tech Stack:** Python 3.11+ stdlib (existing strict mypy/ruff), Electron + electron-vite + TypeScript strict + React 18 + @xterm/xterm + node-pty + d3-hierarchy + Vitest.

**Spec:** `docs/superpowers/specs/2026-11-04-live-workspace-design.md`

## Global Constraints

- The watcher makes no network requests, never writes to logs, the repo or agent config, and adds no Python dependencies.
- Git runs only through `GitReader.run` (hardened env, `GIT_OPTIONAL_LOCKS=0`).
- No prompt text, diffs or command text cross the protocol. Strip control characters from every string.
- Protocol: one JSON object per line, `"v": 1`, UTC ISO timestamps with milliseconds and a `Z` suffix.
- Windows: overlap `active` ≤ 2 min, `recent` ≤ 15 min, measured on record time. Read glow 20 s, edit glow 90 s. Waiting heuristic 1.5 s of pty silence with a pending call.
- Polling: tail every 250 ms, discovery and git every 2 s, recency window for discovery 10 min, tree cap 20 000.
- Colors: Claude `#e0825f`, Codex `#3fbf96`, overlap red `#e5484d`, recent amber `#e6a23c`, waiting `#e6c34a`.
- Renderer: `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`, CSP `default-src 'self'; style-src 'self' 'unsafe-inline'`.
- Terminal bytes go straight from IPC to `xterm.write`, never through React state.

## Review Focus

1. **Huge or old logs at startup.** A 50 MB rollout is replayed once without blocking the loop for more than a few seconds, and old activity is ignored by window. Test: `test_replay_old_activity_is_silent` (Task 4).
2. **Agent exits or the app dies.** On stdin EOF the watcher exits with 0 instead of orphaning. Test: `test_watch_exits_on_stdin_eof` (Task 6).
3. **Paths with spaces, unicode, `..`, or outside the repo.** Normalized or dropped, never crash. Test: `test_paths_outside_repo_dropped` and `test_codex_relative_patch_path` (Tasks 2/4).
4. **Same agent, two panes, same file.** Two Claude panes are different sessions, so this is an overlap. A Claude parent and its subagent are not. Test: `test_subagent_never_overlaps_parent` (Task 4).
5. **Typing performance.** A pane firehose (`yes`) must not re-render React per chunk. Test: `pane output does not dispatch per chunk` (Task 8).

---

### Task 1: Protocol messages

**Files:**
- Create: `src/tandem/live/__init__.py`, `src/tandem/live/protocol.py`
- Test: `tests/test_live_protocol.py`

**Interfaces:**
- Produces: `PROTOCOL_VERSION = 1`; `clean(s: str, limit: int = 200) -> str` (strips non-printables and truncates); `iso(ts: float) -> str` (UTC `YYYY-MM-DDTHH:MM:SS.mmmZ`); `parse_iso(raw: object) -> float | None`; `encode(msg: dict[str, object]) -> str` (adds `"v": 1`, cleans every str value recursively, compact JSON, no trailing newline); `decode_command(line: str) -> dict[str, object] | None` (accepts only `pane_started`, `pane_exited`, `pane_input` with the field types from the spec; otherwise `None`).

- [ ] Step 1: Tests: `encode({"type":"hello","x":"a\x1b[31mb"})` decodes to `{"v":1,"type":"hello","x":"a[31mb"}` with the escape removed; `iso(0.0) == "1970-01-01T00:00:00.000Z"`; `parse_iso("2026-11-04T17:01:28.737Z")` round-trips through `iso`; `decode_command('{"type":"pane_started","pane_id":"p1","agent":"codex","pid":12,"spawned_at":"…"}')` returns a dict; an unknown type, a non-JSON line and a wrong field type each return `None`.
- [ ] Step 2: Run `pytest tests/test_live_protocol.py -v` → FAIL (module missing).
- [ ] Step 3: Implement.
- [ ] Step 4: Run → PASS; `mypy` and `ruff check .` clean.
- [ ] Step 5: Commit `Add live protocol encoding`.

### Task 2: Activity extractors

**Files:**
- Create: `src/tandem/live/extract.py`
- Test: `tests/test_live_extract.py`

**Interfaces:**
- Consumes: `parse_iso` (Task 1).
- Produces:
  ```python
  Kind = Literal["read", "edit", "shell", "prompt"]
  Phase = Literal["start", "done", "failed"]
  @dataclass(frozen=True, slots=True)
  class Activity: kind: Kind; phase: Phase; paths: tuple[str, ...]  # absolute, normpath'd
                  tool: str; at: float | None; call_id: str
  @dataclass(frozen=True, slots=True)
  class Turn: working: bool; at: float | None
  @dataclass(frozen=True, slots=True)
  class Meta: cwd: str | None = None; model: str | None = None; title: str | None = None
              root_session: str | None = None  # Codex root thread id; Claude None
              originator: str | None = None
  Signal = Activity | Turn | Meta
  class ClaudeExtractor:  def feed(self, rec: dict[str, Any]) -> list[Signal]; pending: int (property)
  class CodexExtractor:   same interface
  ```
- Rules: exactly the spec's extraction bullets. Claude paths come from `input.file_path | path | notebook_path`. Glob/Grep with no path → the cwd (a directory read). Results pair to calls by `tool_use_id`. An edit result emits `Activity(kind="edit", phase="done"|"failed", same paths)`. Codex `apply_patch` paths come from lines matching `^\*\*\* (Add|Update|Delete) File: (.+)$` and `^\*\*\* Move to: (.+)$`, resolved against `json.loads(arguments).get("workdir")` (function_call) or the session cwd (custom_tool_call). A `FileChange` gives `done` for each key of `changes`. Outputs pair by `call_id`. `pending` counts calls with no result.

- [ ] Step 1: Tests (synthetic dict records modeled on `tests/fixtures/*`):
  - `test_claude_read_and_edit_lifecycle`: a `tool_use` Read → `read/start` with an absolute path; a `tool_use` Edit → `edit/start`, `pending == 1`; a `tool_result` → `edit/done`, `pending == 0`; `is_error` → `edit/failed`.
  - `test_claude_turn_and_title`: a user text record → `Turn(True)` and `Activity(prompt)`; `system`/`turn_duration` → `Turn(False)`; `ai-title` → `Meta(title=…)`.
  - `test_codex_relative_patch_path`: cwd `/r/a`, patch `*** Update File: ../b/x.ts` → path `/r/b/x.ts`.
  - `test_codex_file_change_and_turns`: `task_started` → `Turn(True)`; `FileChange` with two keys → two `edit/done` paths; `task_complete` → `Turn(False)`.
  - `test_codex_meta_root_session`: `session_meta` with `session_id` S and `id` C → `Meta(root_session=S, cwd=…, originator=…)`.
  - `test_shell_tools`: Claude `Bash`, Codex `exec` and `exec_command` → `shell/start` with empty paths.
- [ ] Step 2: Run → FAIL.
- [ ] Step 3: Implement both classes.
- [ ] Step 4: Run → PASS; mypy and ruff clean.
- [ ] Step 5: Commit `Extract live activity from Claude and Codex records`.

### Task 3: Tailing and discovery

**Files:**
- Create: `src/tandem/live/tail.py`
- Test: `tests/test_live_tail.py`

**Interfaces:**
- Consumes: `sources.jsonl.read_incremental`, `Checkpoint`, `SourceState`.
- Produces:
  ```python
  @dataclass(slots=True)
  class Tail:  path: Path; agent: AgentFamily; checkpoint: Checkpoint | None = None
      def read(self) -> tuple[list[dict[str, Any]], bool]  # (new records, rewritten)
  def recent_logs(root: Path, since: float) -> list[Path]   # *.jsonl, mtime >= since, no symlinks, under root
  def peek_cwd(path: Path, agent: AgentFamily, max_lines: int = 200) -> tuple[str | None, dict[str, Any] | None]
      # Claude: first record cwd; Codex: session_meta payload (returned as 2nd item)
  def claude_registry(config_dir: Path) -> dict[int, tuple[str, str | None]]  # pid -> (sessionId, cwd); tolerant of junk files
  ```
- `Tail.read` on `REWRITTEN` resets the checkpoint and returns all records with `rewritten=True`.

- [ ] Step 1: Tests: append half a line, read → no record; finish the line → one record; truncate the file → `rewritten=True`; `recent_logs` skips old mtimes (via `os.utime`) and symlinks; `claude_registry` parses `<pid>.json` and ignores invalid JSON and `*.key`.
- [ ] Step 2: FAIL → Step 3 implement → Step 4 PASS (+mypy/ruff) → Step 5 commit `Tail agent logs incrementally for live watch`.

### Task 4: Live state and overlap rules

**Files:**
- Create: `src/tandem/live/overlap.py`, `src/tandem/live/state.py`
- Test: `tests/test_live_state.py`

**Interfaces:**
- Consumes: `Activity`, `Turn`, `Meta` (Task 2); `iso` (Task 1).
- Produces:
  ```python
  ACTIVE_S = 120.0; RECENT_S = 900.0
  class OverlapTracker:
      def edit(self, path: str, session: str, at: float) -> list[dict[str, object]]   # overlap msgs
      def tick(self, now: float) -> list[dict[str, object]]
  class LiveState:
      def __init__(self, top: Path, clock: Callable[[], float]) -> None
      def ensure_session(self, sid: str, agent: str, *, pane_id: str | None, external: bool) -> list[dict[str, object]]
      def end_session(self, sid: str) -> list[dict[str, object]]
      def apply(self, sid: str, sig: Signal) -> list[dict[str, object]]
      def tick(self) -> list[dict[str, object]]
      def edited_recently(self, path: str, within: float) -> bool
      def shell_open(self) -> list[str]   # sessions with an open shell call
  ```
- `apply` turns absolute paths into top-relative ones and drops any outside `top`. It ignores map and overlap effects for activity with `at < now - RECENT_S`, but still updates pending and turn state. It emits `activity`, `status` (whenever `working` or `pending_call` changes) and `session` (on a Meta title/model change).
- Overlap: per path, keep the last edit time per session. Level is `active` if two sessions have edits within `ACTIVE_S` of each other and the newest is within `ACTIVE_S` of now; `recent` if two sessions both edited within `RECENT_S` of now; otherwise `cleared`. Emit only on level change. An ended session's touches still count until they age out.

- [ ] Step 1: Tests with a fake clock:
  - `test_reads_never_overlap`.
  - `test_overlap_active_then_recent_then_cleared`: edits by A and B 30 s apart → `active`; tick at +3 min → `recent`; tick at +16 min → `cleared`.
  - `test_edits_far_apart_are_recent_not_active`: 5 min apart → `recent` only.
  - `test_subagent_never_overlaps_parent`: the same sid (subagents are folded upstream) → no overlap message.
  - `test_replay_old_activity_is_silent`: an edit with `at` 20 min ago → no activity or overlap message, but `status` still reflects pending.
  - `test_paths_outside_repo_dropped`: `/elsewhere/x` and `top/../x` → no activity message; `top/a b/ü.py` → relative `a b/ü.py`.
- [ ] Step 2: FAIL → Step 3 implement → Step 4 PASS (+mypy/ruff) → Step 5 commit `Track live sessions and file-level overlaps`.

### Task 5: Git polling and tree

**Files:**
- Create: `src/tandem/live/gitpoll.py`
- Test: `tests/test_live_gitpoll.py`

**Interfaces:**
- Consumes: `GitReader` (`run`, `discover().top_level`).
- Produces:
  ```python
  def repo_tree(git: GitReader, top: Path, cap: int = 20_000) -> tuple[list[dict[str, object]], bool]
      # git ls-files -z --cached --others --exclude-standard; [{path, size}], truncated
  class DirtyWatcher:
      def __init__(self, git: GitReader, top: Path) -> None   # first poll primes, emits nothing
      def poll(self) -> list[str]   # paths newly dirty, or dirty with changed mtime, since last poll
  def head(git: GitReader) -> str | None
  ```
- [ ] Step 1: Tests use the existing git fixture runner in `tests/conftest.py`. A fresh repo with one committed file gives a tree of 1 file and a correct size. Priming the poll → `[]`. After modifying the file → `["f.txt"]`. Polling again unchanged → `[]`. After touching it with a new mtime → `["f.txt"]`. A new untracked file → included.
- [ ] Step 2: FAIL → Step 3 implement → Step 4 PASS (+mypy/ruff) → Step 5 commit `Poll git status and list the tree for live watch`.

### Task 6: Watch loop and `tandem watch` command

**Files:**
- Create: `src/tandem/live/watch.py`
- Modify: `src/tandem/cli.py` (add `watch` subparser + `cmd_watch`)
- Test: `tests/test_live_watch.py`

**Interfaces:**
- Consumes: Tasks 1–5; `paths.default_claude_root`, `default_codex_root`.
- Produces: `run_watch(repo: Path, out: TextIO, inp: TextIO, *, claude_root: Path, codex_root: Path, claude_config: Path, clock: Callable[[], float] = time.time, poll_s: float = 0.25, max_loops: int | None = None) -> int`. A daemon thread reads `inp` lines into a `queue.Queue`, and EOF puts a sentinel. The loop drains commands; every 2 s it runs discovery (bind panes, register external sessions, re-read the Claude registry), the dirty watcher (emitting `change` with `likely_session` when exactly one session has a shell open and no session edited the path in the last 5 s; if some session did edit it, nothing is emitted), and `head()` → re-send `tree` on change; it polls tails every loop and `state.tick()` every 1 s. It flushes `out` after each batch and returns 0 on the EOF sentinel.
- Session ids: Claude `claude_code:<sessionId>`; for a `subagents/` file use the parent folder name as the sessionId. Codex `codex:<root_session or id>`.
- CLI: `tandem watch [PATH] [--claude-root DIR] [--codex-root DIR]`. `claude_config` = parent of the Claude root.

- [ ] Step 1: Tests:
  - `test_watch_end_to_end`: a temp git repo; a temp Claude root containing `<uuid>.jsonl` with cwd = repo, an Edit `tool_use` and `tool_result` for `a.py` at now; stdin carries `pane_started` (claude, that uuid) and then EOF after the first loops. Run with `max_loops=20, poll_s=0`. The output contains `hello`, `tree`, `session` with `pane_id`, and `activity` kind `edit` path `a.py`.
  - `test_external_codex_session_and_overlap`: Codex rollout with `session_meta` cwd = repo, `apply_patch` on `a.py` now, plus a Claude edit on `a.py` now → an `overlap` `active` with both sessions; the Codex session has `external: true`.
  - `test_watch_exits_on_stdin_eof`: empty stdin → returns 0 within `max_loops`.
  - `test_cli_watch_help`: `main(["watch","--help"])` returns 0.
- [ ] Step 2: FAIL → Step 3 implement → Step 4 PASS, then run the full `pytest`, `mypy` and `ruff check .` → all clean.
- [ ] Step 5: Commit `Add tandem watch: live NDJSON stream of agent activity`.

### Task 7: Electron shell, panes and watcher bridge

**Files:**
- Create: `app/package.json`, `app/electron.vite.config.ts`, `app/tsconfig*.json`, `app/src/shared/protocol.ts`, `app/src/main/index.ts`, `app/src/main/panes.ts`, `app/src/main/watcher.ts`, `app/src/main/agents.ts`, `app/src/preload/index.ts`, `app/src/renderer/index.html`, `app/src/renderer/main.tsx`, `app/.gitignore`
- Modify: root `.gitignore` (add `app/node_modules/`, `app/out/`)

**Interfaces:**
- `shared/protocol.ts`: TS types mirroring every message in the spec table (`WatcherMsg` union on `type`, `PaneCommand` union).
- `preload` exposes `window.tandem: TandemBridge`:
  ```ts
  interface TandemBridge {
    repo(): Promise<{ root: string } | null>
    createPane(agent: 'claude_code' | 'codex'): Promise<{ paneId: string }>
    write(paneId: string, data: string): void
    resize(paneId: string, cols: number, rows: number): void
    kill(paneId: string): void
    restart(paneId: string): Promise<void>
    onPaneData(cb: (paneId: string, data: string) => void): () => void
    onPaneExit(cb: (paneId: string, code: number) => void): () => void
    onWatcher(cb: (msg: WatcherMsg) => void): () => void
  }
  ```
- `agents.ts`: `resolveCodex(): string` (`TANDEM_CODEX_BIN` → `codex` on PATH via `which` in a login shell → `/Applications/ChatGPT.app/Contents/Resources/codex`). `agentCommand(agent, sessionId?) -> string`.
- `panes.ts`: `PaneManager` spawns `node-pty` with `process.env.SHELL ?? '/bin/zsh'`, args `['-l','-c', 'exec ' + cmd]`, `cwd` = repo, `TERM=xterm-256color`. On create it sends `pane_started` to the watcher with the pty pid, and `pane_exited` on exit. `write` also sends a throttled `pane_input` (1/s).
- `watcher.ts`: spawns `TANDEM_PYTHON ?? 'python3'` with `['-m','tandem','watch', repo]` and `PYTHONPATH` pointed at `<repoOfApp>/src` when running from the clone; splits stdout into lines, `JSON.parse`, forwards to the renderer; writes commands as NDJSON to stdin; logs stderr. It restarts once after 2 s on a crash and then shows a diagnostic.
- `--repo <path>` from argv; otherwise `dialog.showOpenDialog({properties:['openDirectory']})`.
- Scripts: `dev`, `build`, `typecheck` (`tsc --noEmit -p` for each tsconfig), `test` (vitest), `postinstall: electron-builder install-app-deps` or `@electron/rebuild -f -w node-pty`.

- [ ] Step 1: Scaffold and `npm install` in `app/`; verify `npx tsc` and `node -e "require('node-pty')"` under the Electron ABI via `npm run typecheck`.
- [ ] Step 2: Implement main, preload, and a placeholder renderer that lists watcher messages.
- [ ] Step 3: `npm run typecheck` → clean; `npm run build` → succeeds.
- [ ] Step 4: Commit `Scaffold the Electron app with pty panes and watcher bridge`.

### Task 8: Renderer state

**Files:**
- Create: `app/src/renderer/state/store.ts`, `app/src/renderer/state/selectors.ts`, `app/src/renderer/state/paneActivity.ts`
- Test: `app/src/renderer/state/store.test.ts`

**Interfaces:**
- `store.ts`: `AppState { panes: Pane[]; sessions: Record<string, SessionView>; files: Record<string, FileTouches>; overlaps: Record<string, OverlapView>; tree: TreeFile[]; changes: Record<string, ChangeView>; diagnostics: Diag[]; calm: boolean }`; `reduce(state, action)` for actions `watcher`, `pane_added`, `pane_exited`, `pane_removed`, `toggle_calm`. `FileTouches = Record<sessionId, { read?: number; edit?: number }>` (ms epoch).
- `paneActivity.ts`: a module-level `Map<paneId, {lastOutput: number; bell: number; lastInput: number}>` written by terminal callbacks, so there are no React dispatches per chunk.
- `selectors.ts`: `paneStatus(state, paneId, now, act) -> 'working'|'waiting'|'idle'|'exited'|'starting'`. It returns `waiting` when the session has `pending_call` and `now - lastOutput >= 1500`, or when `bell > lastInput`. `cellVisual(state, path, now) -> { readColor?: string; readAlpha: number; editColor?: string; editAlpha: number; overlap?: 'active'|'recent'; change?: 'grey'|string }`. Read alpha fades linearly to 0 over 20 s. Edit alpha fades from 1 to 0.15 over 90 s, holds 0.15 while the session is not ended, and otherwise falls to 0. `hasLiveDecay(state, now) -> boolean`.

- [ ] Step 1: Vitest tests: an `activity` edit sets `files[path][sid].edit`; an `overlap` `cleared` removes the entry; `paneStatus` waiting after 1.5 s of silence with a pending call and working before that; bell rule; edit alpha at 0 / 45 s / 120 s (ended vs alive); `pane output does not dispatch per chunk` (writing to `paneActivity` 1000 times leaves the store state reference unchanged).
- [ ] Step 2: FAIL → Step 3 implement → Step 4 PASS + typecheck → Step 5 commit `Add renderer state for panes, touches and overlaps`.

### Task 9: Panes UI

**Files:**
- Create: `app/src/renderer/components/PaneStack.tsx`, `TerminalPane.tsx`, `Toolbar.tsx`, `app/src/renderer/styles.css`, `app/src/renderer/App.tsx`

**Interfaces:**
- `TerminalPane({paneId, agent, status, title, currentFile, overlapFile})`: owns its xterm `Terminal` (fit and WebGL addons, falling back to canvas if WebGL fails). `onPaneData` → `term.write` plus `paneActivity` updates; `term.onData` → `bridge.write`; `ResizeObserver` → fit → `bridge.resize`; `term.onBell` → `paneActivity.bell`. On exit it shows an overlay "exited · press Enter to restart" → `bridge.restart`.
- Header: agent-colored name, status dot (classes `dot working|waiting|idle`), title, current file. A red border and "⚠ also edited by …" when overlapping.
- `PaneStack`: vertical flex with draggable dividers (pointer events, min 120 px per pane). The App layout puts the left column and the map side by side with a draggable splitter (default 55%).
- `Toolbar`: `+ Claude`, `+ Codex`, `Calm` toggle, repo name. Menu accelerators `Cmd+T` (new Claude pane), `Cmd+Shift+T` (new Codex pane), `Cmd+W` (close focused pane), registered in the main-process menu.
- [ ] Step 1: Implement. Step 2: `npm run typecheck` clean. Step 3: manual check `npm run dev -- --repo ..` opens a Claude pane, typing works, and resize reflows. Step 4: commit `Add terminal panes, toolbar and layout`.

### Task 10: Repo map

**Files:**
- Create: `app/src/renderer/map/tree.ts`, `app/src/renderer/map/RepoMap.tsx`, `app/src/renderer/map/FileCard.tsx`, `app/src/renderer/components/Banner.tsx`, `app/src/renderer/components/ExternalList.tsx`
- Test: `app/src/renderer/map/tree.test.ts`

**Interfaces:**
- `tree.ts`: `buildLevel(files: TreeFile[], dir: string, collapseOver = 40) -> MapNode[]` where `MapNode = {path: string; name: string; isDir: boolean; size: number; descendants: string[]}`. Children of `dir` are files, plus directories collapsed into one node when they have more than `collapseOver` files; smaller directories are expanded inline. `layout(nodes, w, h) -> Rect[]` uses d3 `treemap().tile(treemapSquarify)`, with size `max(size, 1)`.
- `dirVisual(state, node, now)` merges child visuals: max alphas, any `active` wins.
- `RepoMap` renders absolutely positioned divs with CSS transitions (no canvas). Glows use `box-shadow`/`background` with the agent color and alpha. `active` → class `pulse-red` (keyframes); `recent` → amber outline. Calm mode or `prefers-reduced-motion` → no keyframes. Hover → title tooltip; click a file → `FileCard`; click a dir → zoom, with breadcrumbs.
- A ticker (`requestAnimationFrame` throttled to 4 fps) runs only while `hasLiveDecay`.
- `Banner`: one line per `active` overlap: "⚠ Claude and Codex are both editing `path`", dismissible until the next level change.
- `ExternalList`: sessions with `external: true`, showing agent, status and touched-file count.
- [ ] Step 1: Vitest tree tests: a 100-file dir collapses, a 3-file dir expands, layout rects fill the area within 1 px. Step 2: FAIL → Step 3 implement → Step 4 PASS + typecheck + build.
- [ ] Step 5: Manual smoke test: edit a file via Claude in a pane → the cell glows orange within about 1 s; a Codex Desktop edit to the same file → red pulse and banner.
- [ ] Step 6: Commit `Add live repo map, overlap banner and external sessions`.

### Task 11: Docs

**Files:**
- Modify: `README.md` (a "Live workspace (experimental)" section: what it is, `cd app && npm install && npm run dev -- --repo /path`, the env vars `TANDEM_PYTHON` and `TANDEM_CODEX_BIN`, privacy notes), `docs/architecture.md` (the `live` module row and the protocol pointer).
- [ ] Step 1: Write. Step 2: Commit `Document the live workspace`.
