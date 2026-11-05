import { describe, expect, it } from 'vitest'
import type { WatcherMsg } from '../../shared/protocol'
import { getAct, noteBell, noteInput, noteOutput } from './paneActivity'
import { cellVisual, hasLiveDecay, paneStatus, reuseUnchanged } from './selectors'
import { initialState, reduce, type AppState } from './store'

const T = Date.parse('2026-11-04T12:00:00.000Z')
const at = (ms: number) => new Date(T + ms).toISOString()

function feed(state: AppState, ...msgs: WatcherMsg[]): AppState {
  return msgs.reduce((s, msg) => reduce(s, { type: 'watcher', msg }), state)
}

const session = (id: string, agent: 'claude_code' | 'codex', paneId: string | null, ended = false): WatcherMsg => ({
  v: 1,
  type: 'session',
  session_id: id,
  agent,
  pane_id: paneId,
  external: paneId === null,
  ended,
  cwd: null,
  model: null,
  title: null,
})

const edit = (sid: string, path: string, ms: number, phase: 'start' | 'done' | 'failed' = 'done'): WatcherMsg => ({
  v: 1,
  type: 'activity',
  session_id: sid,
  kind: 'edit',
  phase,
  paths: [path],
  tool: 'Edit',
  at: at(ms),
  id: 'x',
})

function withPane(): AppState {
  let s = reduce(initialState, { type: 'pane_added', paneId: 'p1', agent: 'claude_code' })
  s = feed(s, session('A', 'claude_code', 'p1'), session('B', 'codex', null))
  return s
}

describe('reduce', () => {
  it('records edit touches and the current file', () => {
    const s = feed(withPane(), edit('A', 'src/a.py', 0))
    expect(s.files['src/a.py']?.['A']?.edit).toBe(T)
    expect(s.sessions['A']?.currentFile).toEqual({ path: 'src/a.py', kind: 'edit', at: T })
  })

  it('ignores failed edits', () => {
    const s = feed(withPane(), edit('A', 'src/a.py', 0, 'failed'))
    expect(s.files['src/a.py']).toBeUndefined()
  })

  it('stores and clears overlaps', () => {
    let s = feed(withPane(), { v: 1, type: 'overlap', path: 'a.py', level: 'active', sessions: ['A', 'B'], at: at(0) })
    expect(s.overlaps['a.py']?.level).toBe('active')
    s = feed(s, { v: 1, type: 'overlap', path: 'a.py', level: 'cleared', sessions: [], at: at(1) })
    expect(s.overlaps['a.py']).toBeUndefined()
  })

  it('marks panes exited and removed', () => {
    let s = reduce(withPane(), { type: 'pane_exited', paneId: 'p1', code: 0 })
    expect(s.panes[0]?.exited).toBe(true)
    s = reduce(s, { type: 'pane_restarted', paneId: 'p1' })
    expect(s.panes[0]?.exited).toBe(false)
    s = reduce(s, { type: 'pane_removed', paneId: 'p1' })
    expect(s.panes).toEqual([])
  })
})

describe('paneStatus', () => {
  it('is waiting after 1.5 s of silence with a pending call, working before', () => {
    const s = feed(withPane(), { v: 1, type: 'status', session_id: 'A', state: 'working', pending_call: true, at: at(0) })
    noteOutput('p1', T)
    expect(paneStatus(s, 'p1', T + 1000, getAct('p1'))).toBe('working')
    expect(paneStatus(s, 'p1', T + 1600, getAct('p1'))).toBe('waiting')
    noteOutput('p1', T + 1700)
    expect(paneStatus(s, 'p1', T + 1800, getAct('p1'))).toBe('working')
  })

  it('treats a bell as waiting until the next keystroke', () => {
    const s = feed(withPane(), { v: 1, type: 'status', session_id: 'A', state: 'idle', pending_call: false, at: at(0) })
    noteOutput('p2', T)
    noteBell('p2', T + 10)
    let st = reduce(s, { type: 'pane_added', paneId: 'p2', agent: 'codex' })
    st = feed(st, session('C', 'codex', 'p2'))
    expect(paneStatus(st, 'p2', T + 20, getAct('p2'))).toBe('waiting')
    noteInput('p2', T + 30)
    expect(paneStatus(st, 'p2', T + 40, getAct('p2'))).toBe('idle')
  })

  it('is starting before a session binds and exited after exit', () => {
    const s = reduce(initialState, { type: 'pane_added', paneId: 'p9', agent: 'codex' })
    expect(paneStatus(s, 'p9', T, getAct('p9'))).toBe('starting')
    expect(paneStatus(reduce(s, { type: 'pane_exited', paneId: 'p9', code: 1 }), 'p9', T, getAct('p9'))).toBe('exited')
  })
})

describe('cellVisual', () => {
  it('fades edits to a residual tint while the session lives', () => {
    const s = feed(withPane(), edit('A', 'a.py', 0))
    expect(cellVisual(s, 'a.py', T).editAlpha).toBeCloseTo(1)
    expect(cellVisual(s, 'a.py', T + 45_000).editAlpha).toBeCloseTo(0.575)
    expect(cellVisual(s, 'a.py', T + 120_000).editAlpha).toBeCloseTo(0.15)
    expect(cellVisual(s, 'a.py', T).editColor).toBe('#e0825f')
    const ended = feed(s, session('A', 'claude_code', null, true))
    expect(cellVisual(ended, 'a.py', T + 120_000).editAlpha).toBe(0)
  })

  it('fades reads over 20 s', () => {
    const s = feed(withPane(), { v: 1, type: 'activity', session_id: 'B', kind: 'read', phase: 'start', paths: ['b.py'], tool: 'Read', at: at(0), id: 'r' })
    expect(cellVisual(s, 'b.py', T + 10_000).readAlpha).toBeCloseTo(0.5)
    expect(cellVisual(s, 'b.py', T + 10_000).readColor).toBe('#3fbf96')
    expect(cellVisual(s, 'b.py', T + 25_000).readAlpha).toBe(0)
  })

  it('reports live decay only while something is fading', () => {
    const s = feed(withPane(), edit('A', 'a.py', 0))
    expect(hasLiveDecay(s, T + 1000)).toBe(true)
    expect(hasLiveDecay(s, T + 200_000)).toBe(false)
  })
})

describe('pane output does not dispatch per chunk', () => {
  it('keeps the store state reference unchanged', () => {
    const s = withPane()
    for (let i = 0; i < 1000; i++) noteOutput('p1', T + i)
    expect(getAct('p1')?.lastOutput).toBe(T + 999)
    expect(withPane()).toEqual(s)
  })
})

describe('reuseUnchanged', () => {
  it('keeps the previous object for values that did not change', () => {
    const prev = { p1: { a: 1, b: { c: 'x' } }, p2: { a: 2, b: null } }
    const next = { p1: { a: 1, b: { c: 'x' } }, p2: { a: 3, b: null } }
    const out = reuseUnchanged(prev, next)
    expect(out.p1).toBe(prev.p1)
    expect(out.p2).toBe(next.p2)
    expect(reuseUnchanged(out, { ...out })).toBe(out)
  })
})
