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

export type MenuEvent = { kind: 'new-pane'; agent: Agent } | { kind: 'close-pane' } | { kind: 'show-projects' }

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
