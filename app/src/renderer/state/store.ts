import type { Agent, TreeFile, WatcherMsg } from '../../shared/protocol'

export interface Pane {
  paneId: string
  agent: Agent
  exited: boolean
  exitCode?: number
}

export interface SessionView {
  id: string
  agent: Agent
  paneId: string | null
  external: boolean
  ended: boolean
  title: string | null
  model: string | null
  state: 'working' | 'idle'
  pending: boolean
  currentFile?: { path: string; kind: 'read' | 'edit'; at: number }
  touched: string[]
  /** What the session read and edited, oldest first; repeats in a row are merged. */
  timeline: TimelineEntry[]
}

export interface TimelineEntry {
  path: string
  kind: 'read' | 'edit'
  /** First and last touch of this run, as epoch milliseconds. */
  first: number
  at: number
  count: number
  /** The tool call last counted, so a call reported twice counts once. */
  lastId: string
}

/** Last touch per session, as epoch milliseconds of the record time. */
export type FileTouches = Record<string, { read?: number; edit?: number }>

export interface OverlapView {
  level: 'active' | 'recent'
  sessions: string[]
  at: number
}

export interface ChangeView {
  likely: string | null
  at: number
}

export interface Diag {
  level: 'info' | 'warn'
  code: string
  message: string
}

export interface AppState {
  repoRoot: string | null
  panes: Pane[]
  sessions: Record<string, SessionView>
  files: Record<string, FileTouches>
  overlaps: Record<string, OverlapView>
  changes: Record<string, ChangeView>
  tree: TreeFile[]
  treeTruncated: boolean
  diagnostics: Diag[]
  calm: boolean
}

export type Action =
  | { type: 'watcher'; msg: WatcherMsg }
  | { type: 'pane_added'; paneId: string; agent: Agent }
  | { type: 'pane_exited'; paneId: string; code: number }
  | { type: 'pane_restarted'; paneId: string }
  | { type: 'pane_removed'; paneId: string }
  | { type: 'toggle_calm' }

export const initialState: AppState = {
  repoRoot: null,
  panes: [],
  sessions: {},
  files: {},
  overlaps: {},
  changes: {},
  tree: [],
  treeTruncated: false,
  diagnostics: [],
  calm: false,
}

const MAX_TOUCHED = 200
export const MAX_TIMELINE = 500

/** Append touches, merging into the previous entry when it is the same file and kind. */
export function extendTimeline(
  timeline: TimelineEntry[],
  paths: string[],
  kind: 'read' | 'edit',
  at: number,
  id: string,
): TimelineEntry[] {
  const out = timeline.slice()
  for (const path of paths) {
    const last = out.at(-1)
    if (last && last.path === path && last.kind === kind) {
      const repeat = id !== '' && id === last.lastId
      out[out.length - 1] = { ...last, at: Math.max(last.at, at), count: last.count + (repeat ? 0 : 1), lastId: id }
    } else {
      out.push({ path, kind, first: at, at, count: 1, lastId: id })
    }
  }
  return out.length > MAX_TIMELINE ? out.slice(-MAX_TIMELINE) : out
}

function mapPane(state: AppState, paneId: string, f: (p: Pane) => Pane): AppState {
  return { ...state, panes: state.panes.map((p) => (p.paneId === paneId ? f(p) : p)) }
}

function onWatcher(state: AppState, msg: WatcherMsg): AppState {
  switch (msg.type) {
    case 'hello':
      return { ...state, repoRoot: msg.repo_root }
    case 'tree':
      return { ...state, tree: msg.files, treeTruncated: msg.truncated }
    case 'session': {
      const prev = state.sessions[msg.session_id]
      const next: SessionView = {
        id: msg.session_id,
        agent: msg.agent,
        paneId: msg.pane_id,
        external: msg.external,
        ended: msg.ended,
        title: msg.title,
        model: msg.model,
        state: prev?.state ?? 'idle',
        pending: prev?.pending ?? false,
        currentFile: prev?.currentFile,
        touched: prev?.touched ?? [],
        timeline: prev?.timeline ?? [],
      }
      return { ...state, sessions: { ...state.sessions, [msg.session_id]: next } }
    }
    case 'status': {
      const prev = state.sessions[msg.session_id]
      if (!prev) return state
      const next = { ...prev, state: msg.state, pending: msg.pending_call }
      return { ...state, sessions: { ...state.sessions, [msg.session_id]: next } }
    }
    case 'activity': {
      if (msg.kind !== 'read' && msg.kind !== 'edit') return state
      if (msg.phase === 'failed' || msg.paths.length === 0) return state
      const t = Date.parse(msg.at)
      const files = { ...state.files }
      for (const path of msg.paths) {
        const touches = { ...(files[path] ?? {}) }
        touches[msg.session_id] = { ...touches[msg.session_id], [msg.kind]: t }
        files[path] = touches
      }
      const prev = state.sessions[msg.session_id]
      if (!prev) return { ...state, files }
      const touched = [...new Set([...prev.touched, ...msg.paths])].slice(-MAX_TOUCHED)
      const session: SessionView = {
        ...prev,
        touched,
        // Reads arrive once, at start; edits count when they finish.
        timeline:
          msg.kind === 'read' || msg.phase === 'done'
            ? extendTimeline(prev.timeline, msg.paths, msg.kind, t, msg.id)
            : prev.timeline,
        currentFile: { path: msg.paths[0]!, kind: msg.kind, at: t },
      }
      return { ...state, files, sessions: { ...state.sessions, [msg.session_id]: session } }
    }
    case 'overlap': {
      const overlaps = { ...state.overlaps }
      if (msg.level === 'cleared') delete overlaps[msg.path]
      else overlaps[msg.path] = { level: msg.level, sessions: msg.sessions, at: Date.parse(msg.at) }
      return { ...state, overlaps }
    }
    case 'change':
      return {
        ...state,
        changes: { ...state.changes, [msg.path]: { likely: msg.likely_session, at: Date.parse(msg.at) } },
      }
    case 'diagnostic':
      return {
        ...state,
        diagnostics: [...state.diagnostics.slice(-19), { level: msg.level, code: msg.code, message: msg.message }],
      }
  }
}

export function reduce(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'watcher':
      return onWatcher(state, action.msg)
    case 'pane_added':
      return { ...state, panes: [...state.panes, { paneId: action.paneId, agent: action.agent, exited: false }] }
    case 'pane_exited':
      return mapPane(state, action.paneId, (p) => ({ ...p, exited: true, exitCode: action.code }))
    case 'pane_restarted':
      return mapPane(state, action.paneId, (p) => ({ paneId: p.paneId, agent: p.agent, exited: false }))
    case 'pane_removed':
      return { ...state, panes: state.panes.filter((p) => p.paneId !== action.paneId) }
    case 'toggle_calm':
      return { ...state, calm: !state.calm }
  }
}
