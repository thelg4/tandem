import { memo, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { cellVisual, type CellVisual } from '../state/selectors'
import type { AppState } from '../state/store'
import { ActivityList } from './ActivityList'
import { FileCard } from './FileCard'
import { buildLevel, layout, type MapNode, type Rect } from './tree'

// Theme colors from styles.css, so cells follow light and dark appearance.
const BASE = 'var(--cell)'
const AMBER = 'var(--amber)'

function rgba(hex: string, alpha: number): string {
  const n = parseInt(hex.slice(1), 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha.toFixed(3)})`
}

/** Strongest visual among the touched paths under a directory. */
export function dirVisual(state: AppState, node: MapNode, now: number): CellVisual {
  const prefix = `${node.path}/`
  const keys = new Set([...Object.keys(state.files), ...Object.keys(state.overlaps), ...Object.keys(state.changes)])
  const out: CellVisual = { readAlpha: 0, editAlpha: 0, changeAlpha: 0 }
  for (const path of keys) {
    if (!path.startsWith(prefix) && path !== node.path) continue
    const v = cellVisual(state, path, now)
    if (v.readAlpha > out.readAlpha) Object.assign(out, { readAlpha: v.readAlpha, readColor: v.readColor })
    if (v.editAlpha > out.editAlpha) Object.assign(out, { editAlpha: v.editAlpha, editColor: v.editColor })
    if (v.changeAlpha > out.changeAlpha) Object.assign(out, { changeAlpha: v.changeAlpha, changeColor: v.changeColor })
    if (v.overlap === 'active' || (v.overlap === 'recent' && out.overlap !== 'active')) out.overlap = v.overlap
  }
  return out
}

function cellStyle(r: Rect, v: CellVisual): CSSProperties {
  const layers: string[] = []
  if (v.changeColor && v.changeAlpha > 0) {
    const c = rgba(v.changeColor, v.changeAlpha * 0.6)
    layers.push(`repeating-linear-gradient(135deg, ${c} 0 4px, transparent 4px 8px)`)
  }
  // A collapsed directory holds many files; one edit inside should tint it, not flood it.
  const dir = r.node.isDir
  const fill = v.editColor && v.editAlpha > 0 ? rgba(v.editColor, v.editAlpha * (dir ? 0.18 : 0.75)) : BASE
  const shadows: string[] = []
  if (dir && v.editColor && v.editAlpha > 0) shadows.push(`inset 0 0 0 2px ${rgba(v.editColor, v.editAlpha)}`)
  if (v.readColor && v.readAlpha > 0) shadows.push(`inset 0 0 0 2px ${rgba(v.readColor, v.readAlpha)}`)
  if (v.overlap === 'recent') shadows.push(`inset 0 0 0 2px ${AMBER}`)
  return {
    left: r.x,
    top: r.y,
    width: Math.max(0, r.w),
    height: Math.max(0, r.h),
    background: layers.length ? `${layers.join(', ')}, ${fill}` : fill,
    boxShadow: shadows.join(', ') || undefined,
  }
}

function ago(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 60) return `${s} s ago`
  const m = Math.round(s / 60)
  return m < 60 ? `${m} min ago` : `${Math.round(m / 60)} h ago`
}

export function touchLines(state: AppState, path: string, now: number): string[] {
  const touches = state.files[path] ?? {}
  return Object.entries(touches).map(([sid, t]) => {
    const s = state.sessions[sid]
    const who = s ? (s.agent === 'claude_code' ? 'Claude' : 'Codex') : 'Unknown'
    const parts: string[] = []
    if (t.edit !== undefined) parts.push(`edited ${ago(now - t.edit)}`)
    if (t.read !== undefined) parts.push(`read ${ago(now - t.read)}`)
    return `${who}${s?.external ? ' (outside)' : ''} · ${parts.join(', ')}`
  })
}

interface Props {
  state: AppState
  now: number
}

export const RepoMap = memo(function RepoMap({ state, now }: Props): JSX.Element {
  const [view, setView] = useState<'activity' | 'map'>('activity')
  const [dir, setDir] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [size, setSize] = useState({ w: 0, h: 0 })
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = box.current
    if (!el) return
    const observer = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }))
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  const nodes = useMemo(() => buildLevel(state.tree, dir), [state.tree, dir])
  const rects = useMemo(() => layout(nodes, size.w, size.h), [nodes, size.w, size.h])
  const crumbs = dir ? dir.split('/') : []

  return (
    <div className="map">
      <div className="map-head">
        <div className="segmented small" role="tablist" aria-label="File view">
          <button role="tab" aria-selected={view === 'activity'} className={view === 'activity' ? 'on' : ''} onClick={() => setView('activity')}>
            Activity
          </button>
          <button role="tab" aria-selected={view === 'map'} className={view === 'map' ? 'on' : ''} onClick={() => setView('map')}>
            Map
          </button>
        </div>
        {view === 'map' && (
          <div className="crumbs">
            <button onClick={() => setDir('')}>{state.repoRoot?.split('/').pop() ?? 'repo'}</button>
            {crumbs.map((c, i) => (
              <span key={i}>
                {' / '}
                <button onClick={() => setDir(crumbs.slice(0, i + 1).join('/'))}>{c}</button>
              </span>
            ))}
            {state.treeTruncated && <span className="muted"> · tree truncated</span>}
          </div>
        )}
      </div>
      {view === 'activity' && (
        <ActivityList state={state} now={now} selected={selected} onSelect={setSelected} />
      )}
      <div className="map-area" ref={box} hidden={view !== 'map'}>
        {rects.map((r) => {
          const v = r.node.isDir ? dirVisual(state, r.node, now) : cellVisual(state, r.node.path, now)
          if (!r.leaf) {
            return (
              <div
                key={r.node.path}
                className={`group ${v.overlap === 'active' ? 'pulse-red' : ''}`}
                style={{ left: r.x, top: r.y, width: r.w, height: r.h }}
              >
                {r.w > 50 && <span className="group-label">{r.node.name}/</span>}
              </div>
            )
          }
          const lines = r.node.isDir ? [`${r.node.descendants.length} files`] : touchLines(state, r.node.path, now)
          return (
            <div
              key={r.node.path}
              className={`cell ${r.node.isDir ? 'dir' : ''} ${v.overlap === 'active' ? 'pulse-red' : ''} ${selected === r.node.path ? 'selected' : ''}`}
              style={cellStyle(r, v)}
              title={[r.node.path + (r.node.isDir ? '/' : ''), ...lines].join('\n')}
              onClick={() => (r.node.isDir ? setDir(r.node.path) : setSelected(r.node.path))}
            >
              {r.w > 36 && r.h > 15 && <span className="cell-label">{r.node.name}{r.node.isDir ? '/' : ''}</span>}
              {r.node.isDir && r.w > 120 && r.h > 50 && (
                <span className="dir-hint">{r.node.descendants.length} files · click to open</span>
              )}
            </div>
          )
        })}
        {state.tree.length === 0 && <div className="empty">Waiting for the repository tree…</div>}
      </div>
      {selected && <FileCard state={state} path={selected} now={now} onClose={() => setSelected(null)} />}
    </div>
  )
})
