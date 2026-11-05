import { describe, expect, it } from 'vitest'
import { FINISH_MIN_MS, WAITING_REPEAT_MS, paneNotices, type PaneMemory } from './notify'
import type { PaneStatus } from './selectors'

/** Feed a sequence of [time, status] for one pane and collect the notices. */
function run(steps: [number, PaneStatus][]): string[] {
  let memory: Record<string, PaneMemory> = {}
  const out: string[] = []
  for (const [t, status] of steps) {
    const r = paneNotices(memory, { p1: status }, t)
    memory = r.memory
    out.push(...r.notices.map((n) => `${t}:${n.reason}`))
  }
  return out
}

describe('paneNotices', () => {
  it('says nothing about a pane it sees for the first time', () => {
    expect(run([[0, 'waiting']])).toEqual([])
  })

  it('notifies when a pane starts waiting', () => {
    expect(run([[0, 'working'], [1000, 'waiting']])).toEqual(['1000:waiting'])
  })

  it('does not repeat a flickering wait', () => {
    const t = WAITING_REPEAT_MS
    expect(run([[0, 'working'], [10, 'waiting'], [20, 'working'], [30, 'waiting'], [t + 40, 'working'], [t + 50, 'waiting']])).toEqual([
      '10:waiting',
      `${t + 50}:waiting`,
    ])
  })

  it('notifies a finish only after real work, counting time spent waiting', () => {
    expect(run([[0, 'idle'], [10, 'working'], [5000, 'idle']])).toEqual([])
    const end = FINISH_MIN_MS + 10
    expect(run([[0, 'idle'], [10, 'working'], [100, 'waiting'], [200, 'working'], [end, 'idle']])).toEqual([
      '100:waiting',
      `${end}:finished`,
    ])
  })

  it('treats an exit as no finish', () => {
    expect(run([[0, 'idle'], [10, 'working'], [FINISH_MIN_MS + 10, 'exited']])).toEqual([])
  })

  it('forgets closed panes', () => {
    const a = paneNotices({}, { p1: 'idle', p2: 'idle' }, 0)
    expect(Object.keys(paneNotices(a.memory, { p2: 'idle' }, 1).memory)).toEqual(['p2'])
  })
})
