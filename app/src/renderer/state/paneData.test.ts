import { describe, expect, it } from 'vitest'
import { PaneOutput } from './paneData'

describe('PaneOutput', () => {
  it('buffers until a terminal attaches, then writes straight through', () => {
    const out = new PaneOutput(100)
    out.data('p1', 'a')
    out.data('p1', 'b')
    const got: string[] = []
    out.attach('p1', (d) => got.push(d))
    out.data('p1', 'c')
    expect(got.join('')).toBe('abc')
    expect(out.buffered('p1')).toBe(0)
  })

  it('keeps only the newest output when the buffer is over its cap', () => {
    const out = new PaneOutput(10)
    for (const chunk of ['aaaa', 'bbbb', 'cccc', 'dddd']) out.data('p1', chunk)
    expect(out.buffered('p1')).toBeLessThanOrEqual(10)
    const got: string[] = []
    out.attach('p1', (d) => got.push(d))
    expect(got.join('')).toBe('ccccdddd')
  })

  it('drops a closed pane and ignores its late output', () => {
    const out = new PaneOutput(100)
    out.data('p1', 'x')
    out.forget('p1')
    expect(out.data('p1', 'late')).toBe(false)
    expect(out.buffered('p1')).toBe(0)
  })

  it('a detached writer stops receiving output', () => {
    const out = new PaneOutput(100)
    const got: string[] = []
    const detach = out.attach('p1', (d) => got.push(d))
    detach()
    out.data('p1', 'y')
    expect(got).toEqual([])
    expect(out.buffered('p1')).toBe(1)
  })
})
