import { describe, expect, it } from 'vitest'
import { CLASH_RECENT_MS, SCAN_DEBOUNCE_MS, SCAN_MIN_GAP_MS, ScanScheduler, clashesFromReport } from './scan'

const NOW = Date.parse('2026-11-05T12:00:00.000Z')
const ago = (ms: number) => new Date(NOW - ms).toISOString()

function interaction(over: Record<string, unknown>): Record<string, unknown> {
  return {
    interaction_id: 'i1',
    path: 'm.py',
    label: 'exact_revert',
    lines_affected: 4,
    start_line: 10,
    evidence_class: 'verified',
    original: { agent: 'claude_code', session: 'claude_code:a', time: ago(300_000) },
    later: { agent: 'codex', session: 'codex:b', time: ago(60_000) },
    ...over,
  }
}

describe('clashesFromReport', () => {
  it('keeps recent cross-agent reverts, rewrites and removals', () => {
    const report = {
      interactions: [
        interaction({}),
        interaction({ interaction_id: 'same', later: { agent: 'claude_code', session: 'claude_code:c', time: ago(1) } }),
        interaction({ interaction_id: 'old', later: { agent: 'codex', session: 'codex:b', time: ago(CLASH_RECENT_MS + 1) } }),
        interaction({ interaction_id: 'order', label: 'overlap_candidate' }),
        interaction({ interaction_id: 'weak', evidence_class: 'ambiguous' }),
        interaction({ interaction_id: 'newer', label: 'rewrite', later: { agent: 'codex', session: 'codex:b', time: ago(5_000) } }),
      ],
    }
    const out = clashesFromReport(report, NOW)
    expect(out.map((c) => c.id)).toEqual(['newer', 'i1'])
    expect(out[1]).toMatchObject({ path: 'm.py', label: 'exact_revert', lines: 4, original: { agent: 'claude_code' } })
  })

  it('tolerates a missing or malformed report', () => {
    expect(clashesFromReport(null, NOW)).toEqual([])
    expect(clashesFromReport({ interactions: [{ path: 3 }] }, NOW)).toEqual([])
  })
})

/** A scheduler on a fake clock; ``finish`` settles the running scan. */
function harness() {
  let now = 0
  const timers: { at: number; fn: () => void; id: number }[] = []
  let ids = 0
  let finish: (ok: boolean) => void = () => {}
  const scans: number[] = []
  const statuses: string[] = []
  const s = new ScanScheduler({
    scan: () =>
      new Promise<boolean>((resolve) => {
        scans.push(now)
        finish = resolve
      }),
    now: () => now,
    setTimer: (fn, ms) => {
      const t = { at: now + ms, fn, id: ++ids }
      timers.push(t)
      return t.id
    },
    clearTimer: (id) => {
      const i = timers.findIndex((t) => t.id === id)
      if (i >= 0) timers.splice(i, 1)
    },
    onStatus: (st) => statuses.push(st.state),
    onScanned: () => {},
  })
  const advance = async (ms: number) => {
    now += ms
    for (const t of timers.filter((t) => t.at <= now)) {
      timers.splice(timers.indexOf(t), 1)
      t.fn()
    }
    await Promise.resolve()
  }
  const settle = async (ok = true) => {
    finish(ok)
    await new Promise((r) => setTimeout(r, 0))
  }
  return { s, advance, settle, scans, statuses, now: () => now }
}

describe('ScanScheduler', () => {
  it('debounces requests into one scan', async () => {
    const h = harness()
    h.s.request()
    await h.advance(10_000)
    h.s.request()
    await h.advance(10_000)
    expect(h.scans).toEqual([])
    await h.advance(10_000 + SCAN_DEBOUNCE_MS - h.now())
    expect(h.scans).toEqual([10_000 + SCAN_DEBOUNCE_MS])
  })

  it('runs once more after a request during a scan, keeping the minimum gap', async () => {
    const h = harness()
    h.s.request(0)
    await h.advance(0)
    expect(h.scans).toEqual([0])
    h.s.request(0)
    await h.settle()
    expect(h.s.pendingAt()).toBe(SCAN_MIN_GAP_MS)
    await h.advance(SCAN_MIN_GAP_MS)
    expect(h.scans).toEqual([0, SCAN_MIN_GAP_MS])
    await h.settle(false)
    expect(h.statuses).toEqual(['scanning', 'ok', 'scanning', 'failed'])
    expect(h.s.current().at).not.toBeNull()
  })

  it('does nothing after stop', async () => {
    const h = harness()
    h.s.request(0)
    h.s.stop()
    await h.advance(60_000)
    expect(h.scans).toEqual([])
  })
})
