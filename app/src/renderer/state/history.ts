import type { Agent, HistoryEntry } from '../../shared/protocol'
import type { AppState } from './store'

/** One row of the chat history sidebar. */
export interface ChatRow {
  /** The session key (agent:id), or pane:<id> for a pane that has no session yet. */
  key: string
  agent: Agent
  /** The agent's own session id, for resuming; null for a pane with no session yet. */
  rawId: string | null
  title: string | null
  /** Last activity, epoch milliseconds. */
  at: number
  /** The pane showing this chat, if one is open. */
  paneId: string | null
  /** A live process outside Tandem has it open (another terminal), so it is not resumed here. */
  elsewhere: boolean
}

export interface ChatGroup {
  label: string
  rows: ChatRow[]
}

function rawOf(sessionId: string): string {
  const i = sessionId.indexOf(':')
  return i < 0 ? sessionId : sessionId.slice(i + 1)
}

/**
 * The logs' sessions merged with what the watcher knows live: open panes come first
 * (in pane order), then everything else by last activity. Live titles win over the
 * logs', which can lag a few seconds behind.
 */
export function chatRows(history: HistoryEntry[], state: AppState, now: number): ChatRow[] {
  const rows = new Map<string, ChatRow>()
  for (const h of history) {
    rows.set(h.session_id, {
      key: h.session_id,
      agent: h.agent,
      rawId: h.raw_id,
      title: h.title,
      at: Date.parse(h.last_active) || 0,
      paneId: null,
      elsewhere: h.running,
    })
  }
  // The watcher's own "external" flag only means the log moved in the last few minutes,
  // so whether a chat is busy elsewhere comes from the logs' process records instead.
  for (const s of Object.values(state.sessions)) {
    const open = s.paneId !== null && !s.ended && state.panes.some((p) => p.paneId === s.paneId)
    const prev = rows.get(s.id)
    if (!prev && !open) continue
    rows.set(s.id, {
      key: s.id,
      agent: s.agent,
      rawId: rawOf(s.id),
      title: s.title ?? prev?.title ?? null,
      at: open ? Math.max(prev?.at ?? 0, now) : (prev?.at ?? now),
      paneId: open ? s.paneId : null,
      elsewhere: !open && (prev?.elsewhere ?? false),
    })
  }
  // A pane before its first prompt has no session yet; it still gets a row.
  const bound = new Set([...rows.values()].map((r) => r.paneId).filter(Boolean))
  for (const p of state.panes) {
    if (bound.has(p.paneId)) continue
    rows.set(`pane:${p.paneId}`, {
      key: `pane:${p.paneId}`,
      agent: p.agent,
      rawId: null,
      title: null,
      at: now,
      paneId: p.paneId,
      elsewhere: false,
    })
  }
  const order = new Map(state.panes.map((p, i) => [p.paneId, i]))
  return [...rows.values()].sort((a, b) => {
    const pa = a.paneId !== null ? order.get(a.paneId) ?? 0 : null
    const pb = b.paneId !== null ? order.get(b.paneId) ?? 0 : null
    if (pa !== null && pb !== null) return pa - pb
    if (pa !== null) return -1
    if (pb !== null) return 1
    return b.at - a.at
  })
}

function startOfDay(t: number): number {
  const d = new Date(t)
  d.setHours(0, 0, 0, 0)
  return d.getTime()
}

/** Open chats, then Today, Yesterday, Previous 7 Days, Previous 30 Days, Older. */
export function groupChats(rows: ChatRow[], now: number): ChatGroup[] {
  const today = startOfDay(now)
  const day = 86_400_000
  const buckets: [string, (t: number) => boolean][] = [
    ['Today', (t) => t >= today],
    ['Yesterday', (t) => t >= today - day],
    ['Previous 7 Days', (t) => t >= today - 7 * day],
    ['Previous 30 Days', (t) => t >= today - 30 * day],
    ['Older', () => true],
  ]
  const open: ChatRow[] = []
  const dated = new Map<string, ChatRow[]>(buckets.map(([label]) => [label, []]))
  for (const r of rows) {
    if (r.paneId !== null) open.push(r)
    else dated.get(buckets.find(([, test]) => test(r.at))![0])!.push(r)
  }
  const groups: ChatGroup[] = open.length ? [{ label: 'Open', rows: open }] : []
  for (const [label] of buckets) {
    const list = dated.get(label)!
    if (list.length) groups.push({ label, rows: list })
  }
  return groups
}

/** Rows whose title contains every word of ``query``, ignoring case. */
export function filterChats(rows: ChatRow[], query: string): ChatRow[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean)
  if (words.length === 0) return rows
  return rows.filter((r) => {
    const title = (r.title ?? '').toLowerCase()
    return words.every((w) => title.includes(w))
  })
}
