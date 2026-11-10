import { describe, expect, it } from 'vitest'
import type { HistoryEntry, WatcherMsg } from '../../shared/protocol'
import { chatRows, filterChats, groupChats } from './history'
import { initialState, reduce, type AppState } from './store'

const NOW = new Date(2026, 10, 10, 15, 0, 0).getTime()
const ago = (h: number) => new Date(NOW - h * 3_600_000).toISOString()

const entry = (id: string, agent: 'claude_code' | 'codex', title: string, hoursAgo: number): HistoryEntry => ({
  session_id: `${agent}:${id}`,
  agent,
  raw_id: id,
  title,
  started: ago(hoursAgo + 1),
  last_active: ago(hoursAgo),
  running: false,
})

const session = (id: string, agent: 'claude_code' | 'codex', paneId: string | null, external: boolean, ended = false): WatcherMsg => ({
  v: 1,
  type: 'session',
  session_id: id,
  agent,
  pane_id: paneId,
  external,
  ended,
  cwd: null,
  model: null,
  title: null,
})

function feed(state: AppState, ...msgs: WatcherMsg[]): AppState {
  return msgs.reduce((s, msg) => reduce(s, { type: 'watcher', msg }), state)
}

describe('chat history rows', () => {
  it('puts open panes first, marks external live sessions, and keeps the rest by recency', () => {
    let state = reduce(initialState, { type: 'pane_added', paneId: 'p1', agent: 'codex' })
    state = reduce(state, { type: 'pane_added', paneId: 'p2', agent: 'claude_code' })
    state = feed(state, session('codex:old', 'codex', 'p1', false), session('claude_code:ext', 'claude_code', null, true), session('codex:stale', 'codex', null, true))
    const history = [
      entry('recent', 'claude_code', 'Recent', 2),
      entry('old', 'codex', 'Resumed', 200),
      entry('stale', 'codex', 'Recently touched', 3),
      { ...entry('ext', 'claude_code', 'Elsewhere', 0.5), running: true },
    ]
    const rows = chatRows(history, state, NOW)
    expect(rows.map((r) => [r.key, r.paneId, r.elsewhere])).toEqual([
      ['codex:old', 'p1', false],
      ['pane:p2', 'p2', false],
      ['claude_code:ext', null, true],
      ['claude_code:recent', null, false],
      ['codex:stale', null, false],
    ])
    expect(rows[0]!.title).toBe('Resumed')
  })

  it('groups by day and filters by title words', () => {
    const rows = chatRows(
      [entry('a', 'codex', 'Fix the parser', 1), entry('b', 'claude_code', 'Parser tests', 30), entry('c', 'codex', 'Docs', 24 * 40)],
      initialState,
      NOW,
    )
    expect(groupChats(rows, NOW).map((g) => [g.label, g.rows.length])).toEqual([
      ['Today', 1],
      ['Yesterday', 1],
      ['Older', 1],
    ])
    expect(filterChats(rows, 'parser').map((r) => r.key)).toEqual(['codex:a', 'claude_code:b'])
    expect(filterChats(rows, 'fix parser').map((r) => r.key)).toEqual(['codex:a'])
  })
})
