// Mirrors src/tandem/live/protocol.py and the spec's protocol tables.

export type Agent = 'claude_code' | 'codex'

export interface TreeFile {
  path: string
  size: number
}

export type WatcherMsg =
  | { v: 1; type: 'hello'; version: string; repo_root: string; claude_root: string; codex_root: string }
  | { v: 1; type: 'tree'; files: TreeFile[]; truncated: boolean }
  | {
      v: 1
      type: 'session'
      session_id: string
      agent: Agent
      pane_id: string | null
      external: boolean
      ended: boolean
      cwd: string | null
      model: string | null
      title: string | null
    }
  | { v: 1; type: 'status'; session_id: string; state: 'working' | 'idle'; pending_call: boolean; at: string }
  | {
      v: 1
      type: 'activity'
      session_id: string
      kind: 'read' | 'edit' | 'shell' | 'prompt'
      phase: 'start' | 'done' | 'failed'
      paths: string[]
      tool: string
      at: string
      id: string
    }
  | { v: 1; type: 'overlap'; path: string; level: 'active' | 'recent' | 'cleared'; sessions: string[]; at: string }
  | { v: 1; type: 'change'; path: string; likely_session: string | null; at: string }
  | { v: 1; type: 'diagnostic'; level: 'info' | 'warn'; code: string; message: string }

export type PaneCommand =
  | {
      type: 'pane_started'
      pane_id: string
      agent: Agent
      pid: number
      claude_session_id?: string
      /** Set when the pane resumes an existing Codex session. */
      codex_session_id?: string
      spawned_at: string
    }
  | { type: 'pane_exited'; pane_id: string; at: string }
  | { type: 'pane_input'; pane_id: string; at: string }

export const AGENT_COLORS: Record<Agent, string> = {
  claude_code: '#e0825f',
  codex: '#3fbf96',
}

export const AGENT_NAMES: Record<Agent, string> = {
  claude_code: 'Claude',
  codex: 'Codex',
}

export type MenuEvent =
  | { kind: 'new-pane'; agent: Agent }
  | { kind: 'close-pane' }
  | { kind: 'show-projects' }
  | { kind: 'focus-pane'; paneId: string }
  | { kind: 'open-file'; path: string }
  | { kind: 'show-skills' }
  | { kind: 'toggle-sidebar' }

/** A system notification about one pane; clicking it brings that pane forward. */
export interface PaneNotice {
  paneId: string
  title: string
  body: string
}

/** A repository for the project picker. */
export interface ProjectEntry {
  path: string
  name: string
  /** Latest agent session in the logs (ISO), if any. */
  lastActive: string | null
  /** Agent sessions seen in the logs over the look-back window. */
  sessions: Partial<Record<Agent, number>>
  /** Last opened in this app (ISO), if ever. */
  opened: string | null
}

export interface ProjectList {
  projects: ProjectEntry[]
  home: string
  /** False when `tandem projects` could not run (for example, no Python 3.11+). */
  logsRead: boolean
}

export type FileContents =
  | { kind: 'text'; text: string; size: number }
  | { kind: 'binary'; size: number }
  | { kind: 'too_large'; size: number }
  | { kind: 'missing' }

export interface EditHunk {
  /** Null for Codex patch text, which carries no line numbers. */
  old_start: number | null
  new_start: number | null
  /** Each line starts with ' ', '-' or '+'. */
  lines: string[]
}

/** One successful agent edit to a file, from `tandem edits --format json`. */
export interface FileEdit {
  agent: Agent
  session_id: string
  title: string | null
  at: string
  tool: string
  change: 'update' | 'create' | 'delete'
  user_modified: boolean
  truncated: boolean
  hunks: EditHunk[]
}

/** One past or present agent session in this repository, from `tandem sessions --format json`. */
export interface HistoryEntry {
  /** agent:raw_id, the same key the watcher uses. */
  session_id: string
  agent: Agent
  /** The agent's own id, which its resume command takes. */
  raw_id: string
  title: string | null
  started: string
  last_active: string
  /** A live process has it open (Claude Code only; Codex keeps no such record). */
  running: boolean
}

export type OpenResult = { ok: true } | { ok: false; canceled?: boolean; error?: string }

export interface TokenCounts {
  input: number
  output: number
}

export interface LimitWindow {
  used_percent: number
  window_minutes: number | null
  resets_at: string | null
}

export interface AgentUsage {
  today: TokenCounts
  week: TokenCounts
  last_at: string | null
}

/** Output of `tandem usage --format json`. */
export interface UsageSummary {
  at: string
  claude_code: AgentUsage
  codex: AgentUsage & {
    limits: { primary: LimitWindow | null; secondary: LimitWindow | null; plan: string | null; at: string } | null
  }
}

/** One span of lines from `tandem blame --format json`. */
export interface BlameSpan {
  /** 1-based first line. */
  start: number
  count: number
  /** agent: an agent edit wrote it; unknown: a commit no log explains; baseline: older than
   * the analyzed history; changed: on disk but in no scanned log or commit yet. */
  kind: 'agent' | 'unknown' | 'baseline' | 'changed'
  evidence: 'verified' | 'supported' | 'ambiguous' | 'unknown' | null
  agent: Agent | null
  session_id: string | null
  edit_id: string | null
  at: string | null
  /** First commit with these lines; null while uncommitted. */
  commit: string | null
}

export interface FileBlame {
  path: string
  lines: number
  spans: BlameSpan[]
  scanned_at: string | null
  edits: number
  unavailable_edits: number
  note: string | null
}

export type ClashLabel = 'exact_revert' | 'partial_revert' | 'rewrite' | 'removal'

/** One agent changed lines another agent wrote, from the latest scan's interactions. */
export interface Clash {
  id: string
  path: string
  label: ClashLabel
  lines: number
  startLine: number
  evidence: 'verified' | 'supported'
  original: { agent: Agent; session: string; time: string | null }
  later: { agent: Agent; session: string; time: string | null }
}

export interface ScanStatus {
  state: 'never' | 'scanning' | 'ok' | 'failed'
  /** When the last successful scan finished (ISO). */
  at: string | null
}

/** Where a skill is installed: the user's home (every project) or this repository. */
export type SkillScope = 'user' | 'project'

/** One agent's copy of a skill: a folder with a SKILL.md. */
export interface SkillCopy {
  path: string
  /** The folder is a symlink (often managed by another tool). */
  linked: boolean
}

/** A skill by name and scope, with whichever agents have it. */
export interface Skill {
  name: string
  scope: SkillScope
  description: string
  copies: Partial<Record<Agent, SkillCopy>>
  /** Both agents have it, but their SKILL.md files differ. */
  differs: boolean
}

export interface PluginEntry {
  agent: Agent
  /** name@marketplace */
  id: string
  name: string
  marketplace: string | null
  version: string | null
  description: string | null
  enabled: boolean
}

export interface McpEntry {
  agent: Agent
  name: string
  scope: SkillScope
  /** The command it runs, or the URL it talks to. */
  target: string | null
}

export interface AgentExtras {
  skills: Skill[]
  plugins: PluginEntry[]
  mcp: McpEntry[]
  /** The skill folders Tandem reads and installs into; project is null without a repository. */
  dirs: Record<SkillScope, Record<Agent, string> | null>
}

export type ExtrasResult = { ok: true; message?: string } | { ok: false; canceled?: boolean; error?: string }
