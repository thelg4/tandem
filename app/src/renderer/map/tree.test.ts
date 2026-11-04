import { describe, expect, it } from 'vitest'
import { buildLevel, layout } from './tree'

const files = [
  ...Array.from({ length: 100 }, (_, i) => ({ path: `big/f${i}.ts`, size: 10 })),
  { path: 'small/a.ts', size: 30 },
  { path: 'small/b.ts', size: 20 },
  { path: 'small/deep/c.ts', size: 10 },
  { path: 'README.md', size: 50 },
]

describe('buildLevel', () => {
  it('collapses big directories and expands small ones', () => {
    const nodes = buildLevel(files, '')
    const big = nodes.find((n) => n.path === 'big')!
    expect(big.isDir).toBe(true)
    expect(big.children).toBeUndefined()
    expect(big.descendants).toHaveLength(100)
    expect(big.size).toBe(1000)

    const small = nodes.find((n) => n.path === 'small')!
    expect(small.children?.map((c) => c.path).sort()).toEqual(['small/a.ts', 'small/b.ts', 'small/deep'])
    expect(small.descendants).toHaveLength(3)
    expect(nodes.find((n) => n.path === 'README.md')?.isDir).toBe(false)
  })

  it('zooms into a directory', () => {
    const nodes = buildLevel(files, 'big')
    expect(nodes).toHaveLength(100)
    expect(nodes[0]?.name.startsWith('f')).toBe(true)
  })
})

describe('layout', () => {
  it('fills the area with leaf rects', () => {
    const rects = layout(buildLevel(files, ''), 400, 300, 0)
    const leaves = rects.filter((r) => r.leaf)
    const area = leaves.reduce((a, r) => a + r.w * r.h, 0)
    expect(Math.abs(area - 400 * 300)).toBeLessThan(1)
    for (const r of leaves) {
      expect(r.x).toBeGreaterThanOrEqual(-0.5)
      expect(r.y + r.h).toBeLessThanOrEqual(300.5)
    }
    expect(rects.some((r) => !r.leaf && r.node.path === 'small')).toBe(true)
  })

  it('handles an empty tree', () => {
    expect(layout([], 100, 100)).toEqual([])
  })
})
