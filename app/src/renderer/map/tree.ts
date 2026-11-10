import { hierarchy, treemap, treemapSquarify } from 'd3-hierarchy'
import type { TreeFile } from '../../shared/protocol'

export interface MapNode {
  path: string
  name: string
  isDir: boolean
  size: number
  /** Every file path under this node (itself for a file). */
  descendants: string[]
  /** Present on expanded directories; collapsed directories have none. */
  children?: MapNode[]
}

export interface Rect {
  node: MapNode
  x: number
  y: number
  w: number
  h: number
  depth: number
  leaf: boolean
}

/** Children of ``dir`` (``''`` is the root): files, plus directories expanded or collapsed. */
export function buildLevel(files: TreeFile[], dir: string, collapseOver = 80): MapNode[] {
  const prefix = dir ? `${dir}/` : ''
  const own: MapNode[] = []
  const groups = new Map<string, TreeFile[]>()
  for (const f of files) {
    if (!f.path.startsWith(prefix)) continue
    const rest = f.path.slice(prefix.length)
    const slash = rest.indexOf('/')
    if (slash < 0) {
      own.push({ path: f.path, name: rest, isDir: false, size: f.size, descendants: [f.path] })
    } else {
      const seg = rest.slice(0, slash)
      const list = groups.get(seg) ?? []
      list.push(f)
      groups.set(seg, list)
    }
  }
  for (const [seg, list] of groups) {
    const path = prefix + seg
    const node: MapNode = {
      path,
      name: seg,
      isDir: true,
      size: list.reduce((a, f) => a + f.size, 0),
      descendants: list.map((f) => f.path),
    }
    if (list.length <= collapseOver) node.children = buildLevel(list, path, collapseOver)
    own.push(node)
  }
  return own.sort((a, b) => b.descendants.length - a.descendants.length || a.path.localeCompare(b.path))
}

interface Root {
  path: string
  children: MapNode[]
}

/**
 * Every file gets the same area and a directory one per file inside it. Sizing by bytes
 * let one lockfile swallow its folder and squeezed source files to unreadable slivers;
 * equal cells keep names legible and match how agents work, file by file.
 */
function weight(node: MapNode): number {
  return node.isDir ? Math.max(1, node.descendants.length) : 1
}

/** Squarified treemap; leaves are files and collapsed directories. */
export function layout(nodes: MapNode[], w: number, h: number, pad = 14): Rect[] {
  if (nodes.length === 0 || w <= 0 || h <= 0) return []
  const root = hierarchy<MapNode | Root>({ path: '', children: nodes }, (d) =>
    'isDir' in d ? d.children : d.children,
  ).sum((d) => ('isDir' in d && !d.children ? weight(d) : 0))
  treemap<MapNode | Root>()
    .tile(treemapSquarify)
    .size([w, h])
    .paddingInner(pad ? 2 : 0)
    .paddingTop((d) => (d.depth > 0 && pad ? pad : 0))
    .paddingRight((d) => (d.depth > 0 && pad ? 2 : 0))
    .paddingBottom((d) => (d.depth > 0 && pad ? 2 : 0))
    .paddingLeft((d) => (d.depth > 0 && pad ? 2 : 0))
    .round(false)(root)
  const rects: Rect[] = []
  for (const d of root.descendants()) {
    if (d.depth === 0 || !('isDir' in d.data)) continue
    const r = d as typeof d & { x0: number; y0: number; x1: number; y1: number }
    rects.push({
      node: d.data,
      x: r.x0,
      y: r.y0,
      w: r.x1 - r.x0,
      h: r.y1 - r.y0,
      depth: d.depth,
      leaf: !d.data.children,
    })
  }
  return rects
}
