import { Fragment, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import type { Pane } from '../state/store'
import { TerminalPane, type PaneHeader } from './TerminalPane'

interface Props {
  panes: Pane[]
  headers: Record<string, PaneHeader>
  onFocus: (paneId: string) => void
  onClose: (paneId: string) => void
  onRestart: (paneId: string) => void
}

const MIN_PX = 120

export function PaneStack({ panes, headers, onFocus, onClose, onRestart }: Props): JSX.Element {
  const [weights, setWeights] = useState<Record<string, number>>({})
  const box = useRef<HTMLDivElement>(null)

  const startDrag = (index: number) => (e: ReactPointerEvent<HTMLDivElement>) => {
    const a = panes[index]
    const b = panes[index + 1]
    const el = box.current
    if (!a || !b || !el) return
    e.preventDefault()
    const total = panes.reduce((s, p) => s + (weights[p.paneId] ?? 1), 0)
    const pxPerWeight = el.clientHeight / total
    const wa = weights[a.paneId] ?? 1
    const wb = weights[b.paneId] ?? 1
    const startY = e.clientY
    const move = (ev: PointerEvent): void => {
      const minW = MIN_PX / pxPerWeight
      const delta = (ev.clientY - startY) / pxPerWeight
      const na = Math.max(minW, Math.min(wa + wb - minW, wa + delta))
      setWeights((w) => ({ ...w, [a.paneId]: na, [b.paneId]: wa + wb - na }))
    }
    const up = (): void => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  return (
    <div className="pane-stack" ref={box}>
      {panes.map((p, i) => (
        <Fragment key={p.paneId}>
          <div className="pane-slot" style={{ flexGrow: weights[p.paneId] ?? 1 }}>
            <TerminalPane
              paneId={p.paneId}
              agent={p.agent}
              exited={p.exited}
              header={headers[p.paneId]!}
              onFocus={onFocus}
              onClose={onClose}
              onRestart={onRestart}
            />
          </div>
          {i < panes.length - 1 && <div className="divider-h" onPointerDown={startDrag(i)} />}
        </Fragment>
      ))}
    </div>
  )
}
