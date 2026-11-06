import { describe, expect, it } from 'vitest'
import type { BlameSpan, FileBlame, FileEdit } from '../../shared/protocol'
import { blameSummary, findEdit } from './FileView'

const edit = (session_id: string, at: string): FileEdit => ({
  agent: 'codex',
  session_id,
  title: null,
  at,
  tool: 'apply_patch',
  change: 'update',
  user_modified: false,
  truncated: false,
  hunks: [],
})

describe('findEdit', () => {
  const edits = [
    edit('codex:a', '2026-11-05T12:00:30.000Z'),
    edit('codex:b', '2026-11-05T12:00:01.000Z'),
    edit('codex:a', '2026-11-05T12:00:02.000Z'),
  ]

  it('picks the same session’s edit closest in time', () => {
    expect(findEdit(edits, { session: 'codex:a', at: '2026-11-05T12:00:00.000Z' })).toBe(2)
  })

  it('gives up beyond 10 seconds or in another session', () => {
    expect(findEdit(edits, { session: 'codex:a', at: '2026-11-05T12:05:00.000Z' })).toBe(-1)
    expect(findEdit(edits, { session: 'codex:z', at: '2026-11-05T12:00:01.000Z' })).toBe(-1)
  })
})

describe('blameSummary', () => {
  const span = (count: number, kind: BlameSpan['kind'], agent: BlameSpan['agent'] = null): BlameSpan => ({
    start: 1,
    count,
    kind,
    agent,
    evidence: agent ? 'verified' : null,
    session_id: null,
    edit_id: null,
    at: null,
    commit: null,
  })

  it('shares lines by agent, grouping unexplained kinds', () => {
    const blame: FileBlame = {
      path: 'm.py',
      lines: 10,
      spans: [span(6, 'agent', 'claude_code'), span(1, 'unknown'), span(1, 'changed'), span(2, 'agent', 'codex')],
      scanned_at: null,
      edits: 2,
      unavailable_edits: 0,
      note: null,
    }
    expect(blameSummary(blame).map((p) => [p.key, p.pct])).toEqual([
      ['claude_code', 60],
      ['other', 20],
      ['codex', 20],
    ])
  })
})
