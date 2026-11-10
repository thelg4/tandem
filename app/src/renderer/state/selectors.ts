import { AGENT_COLORS } from '../../shared/protocol'
import type { PaneAct } from './paneActivity'
import type { AppState, SessionView } from './store'

export const READ_FADE_MS = 20_000
export const EDIT_FADE_MS = 90_000
export const EDIT_RESIDUAL = 0.15
export const WAITING_SILENCE_MS = 1500
export const GREY = '#8a8f98'

export type PaneStatus = 'working' | 'waiting' | 'idle' | 'exited' | 'starting'

export function paneSession(state: AppState, paneId: string): SessionView | undefined {
  return Object.values(state.sessions).find((s) => s.paneId === paneId && !s.ended)
}

/**
 * Heuristic for "waiting on you": both CLIs animate while a tool runs, so a frozen
 * screen with an open call is almost always an approval prompt. A bell counts until
 * the next keystroke.
 */
export function paneStatus(state: AppState, paneId: string, now: number, act: PaneAct | undefined): PaneStatus {
  const pane = state.panes.find((p) => p.paneId === paneId)
  if (!pane || pane.exited) return 'exited'
  if (act && act.bell > act.lastInput) return 'waiting'
  const s = paneSession(state, paneId)
  if (!s) return 'starting'
  if (s.pending && act && now - act.lastOutput >= WAITING_SILENCE_MS) return 'waiting'
  if (s.state === 'working' || s.pending) return 'working'
  return 'idle'
}

export interface CellVisual {
  readColor?: string
  readAlpha: number
  editColor?: string
  editAlpha: number
  overlap?: 'active' | 'recent'
  changeColor?: string
  changeAlpha: number
}

function editAlpha(age: number, alive: boolean): number {
  if (age < 0) return 1
  if (age < EDIT_FADE_MS) return 1 - (age / EDIT_FADE_MS) * (1 - EDIT_RESIDUAL)
  return alive ? EDIT_RESIDUAL : 0
}

export function cellVisual(state: AppState, path: string, now: number): CellVisual {
  const out: CellVisual = { readAlpha: 0, editAlpha: 0, changeAlpha: 0 }
  const touches = state.files[path]
  if (touches) {
    for (const [sid, t] of Object.entries(touches)) {
      const session = state.sessions[sid]
      const color = session ? AGENT_COLORS[session.agent] : GREY
      if (t.read !== undefined) {
        const a = Math.max(0, Math.min(1, 1 - (now - t.read) / READ_FADE_MS))
        if (a > out.readAlpha) Object.assign(out, { readAlpha: a, readColor: color })
      }
      if (t.edit !== undefined) {
        const a = editAlpha(now - t.edit, session !== undefined && !session.ended)
        if (a > out.editAlpha) Object.assign(out, { editAlpha: a, editColor: color })
      }
    }
  }
  const overlap = state.overlaps[path]
  if (overlap) out.overlap = overlap.level
  const change = state.changes[path]
  if (change) {
    const a = Math.max(0, 1 - (now - change.at) / EDIT_FADE_MS)
    const likely = change.likely ? state.sessions[change.likely] : undefined
    if (a > 0) Object.assign(out, { changeAlpha: a, changeColor: likely ? AGENT_COLORS[likely.agent] : GREY })
  }
  return out
}

export interface ActivityTouch {
  sessionId: string
  agent: SessionView['agent'] | null
  kind: 'read' | 'edit'
  at: number
  external: boolean
}

export interface ActivityRow {
  path: string
  at: number
  /** Latest touch per session, newest first; an edit wins over a read of the same session. */
  touches: ActivityTouch[]
  overlap?: 'active' | 'recent'
  change?: { at: number; likelyAgent: SessionView['agent'] | null }
}

/** Files the agents touched, plus changes no tool call explains, newest first. */
export function activityRows(state: AppState, limit = 200): ActivityRow[] {
  const paths = new Set([...Object.keys(state.files), ...Object.keys(state.changes)])
  const rows: ActivityRow[] = []
  for (const path of paths) {
    const touches: ActivityTouch[] = []
    for (const [sid, t] of Object.entries(state.files[path] ?? {})) {
      const s = state.sessions[sid]
      const base = { sessionId: sid, agent: s?.agent ?? null, external: s?.external ?? false }
      if (t.edit !== undefined) touches.push({ ...base, kind: 'edit', at: t.edit })
      else if (t.read !== undefined) touches.push({ ...base, kind: 'read', at: t.read })
    }
    touches.sort((a, b) => b.at - a.at)
    const change = state.changes[path]
    const row: ActivityRow = { path, at: Math.max(touches[0]?.at ?? 0, change?.at ?? 0), touches }
    const overlap = state.overlaps[path]
    if (overlap) row.overlap = overlap.level
    if (change) {
      const likely = change.likely ? state.sessions[change.likely] : undefined
      row.change = { at: change.at, likelyAgent: likely?.agent ?? null }
    }
    rows.push(row)
  }
  return rows.sort((a, b) => b.at - a.at || a.path.localeCompare(b.path)).slice(0, limit)
}

export function hasLiveDecay(state: AppState, now: number): boolean {
  if (Object.values(state.overlaps).some((o) => o.level === 'active')) return true
  if (Object.values(state.changes).some((c) => now - c.at < EDIT_FADE_MS)) return true
  return Object.values(state.files).some((touches) =>
    Object.values(touches).some(
      (t) => (t.read !== undefined && now - t.read < READ_FADE_MS) || (t.edit !== undefined && now - t.edit < EDIT_FADE_MS),
    ),
  )
}

/**
 * Return ``next`` with every value that equals its counterpart in ``prev`` replaced by
 * the previous object, and ``prev`` itself when nothing changed. Memoized children then
 * skip the clock-driven re-render unless what they show actually changed.
 */
export function reuseUnchanged<T>(prev: Record<string, T>, next: Record<string, T>): Record<string, T> {
  const prevKeys = Object.keys(prev)
  let same = prevKeys.length === Object.keys(next).length
  const out: Record<string, T> = {}
  for (const [key, value] of Object.entries(next)) {
    const old = prev[key]
    if (old !== undefined && JSON.stringify(old) === JSON.stringify(value)) out[key] = old
    else {
      out[key] = value
      same = false
    }
  }
  return same ? prev : out
}
