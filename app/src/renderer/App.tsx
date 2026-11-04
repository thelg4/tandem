import { useCallback, useEffect, useMemo, useReducer, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import type { Agent } from '../shared/protocol'
import { Banner, sessionNames } from './components/Banner'
import { ExternalList } from './components/ExternalList'
import { PaneStack } from './components/PaneStack'
import type { PaneHeader } from './components/TerminalPane'
import { Toolbar } from './components/Toolbar'
import { RepoMap } from './map/RepoMap'
import { forgetPane, getAct } from './state/paneActivity'
import { installPaneDataListener } from './state/paneData'
import { hasLiveDecay, paneSession, paneStatus } from './state/selectors'
import { initialState, reduce } from './state/store'

/** Re-render on a clock: 4 fps while something fades or a pane is live, else 1 fps. */
function useNow(fast: boolean): number {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), fast ? 250 : 1000)
    return () => clearInterval(id)
  }, [fast])
  return now
}

export function App(): JSX.Element {
  const [state, dispatch] = useReducer(reduce, initialState)
  const [split, setSplit] = useState(0.55)
  const focused = useRef<string | null>(null)
  const panesRef = useRef(state.panes)
  panesRef.current = state.panes

  const addPane = useCallback(async (agent: Agent) => {
    const { paneId } = await window.tandem.createPane(agent)
    dispatch({ type: 'pane_added', paneId, agent })
  }, [])

  const closePane = useCallback((paneId: string) => {
    window.tandem.kill(paneId)
    forgetPane(paneId)
    dispatch({ type: 'pane_removed', paneId })
  }, [])

  const restartPane = useCallback((paneId: string) => {
    void window.tandem.restart(paneId)
    dispatch({ type: 'pane_restarted', paneId })
  }, [])

  useEffect(() => {
    installPaneDataListener()
    const offs = [
      window.tandem.onWatcher((msg) => dispatch({ type: 'watcher', msg })),
      window.tandem.onPaneExit((paneId, code) => dispatch({ type: 'pane_exited', paneId, code })),
      window.tandem.onMenu((event) => {
        if (event.kind === 'new-pane') void addPane(event.agent)
        else {
          const target = focused.current ?? panesRef.current.at(-1)?.paneId
          if (target) closePane(target)
        }
      }),
    ]
    window.tandem.ready()
    return () => offs.forEach((off) => off())
  }, [addPane, closePane])

  const live = state.panes.some((p) => !p.exited)
  const now = useNow(live || hasLiveDecay(state, Date.now()))

  const headers = useMemo(() => {
    const out: Record<string, PaneHeader> = {}
    for (const p of state.panes) {
      const s = paneSession(state, p.paneId)
      const fresh = s?.currentFile && now - s.currentFile.at < 60_000 ? s.currentFile : null
      const overlapEntry = s
        ? Object.entries(state.overlaps).find(([, o]) => o.level === 'active' && o.sessions.includes(s.id))
        : undefined
      out[p.paneId] = {
        status: paneStatus(state, p.paneId, now, getAct(p.paneId)),
        title: s?.title ?? null,
        currentFile: fresh ? { path: fresh.path, kind: fresh.kind } : null,
        overlap: overlapEntry
          ? { path: overlapEntry[0], others: sessionNames(state, overlapEntry[1].sessions.filter((id) => id !== s!.id)) }
          : null,
      }
    }
    return out
  }, [state, now])

  const startSplit = (e: ReactPointerEvent<HTMLDivElement>): void => {
    e.preventDefault()
    const move = (ev: PointerEvent): void => setSplit(Math.min(0.8, Math.max(0.25, ev.clientX / window.innerWidth)))
    const up = (): void => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  return (
    <div className={`app ${state.calm ? 'calm' : ''}`}>
      <Toolbar
        repoRoot={state.repoRoot}
        calm={state.calm}
        diagnostics={state.diagnostics}
        onAdd={(agent) => void addPane(agent)}
        onToggleCalm={() => dispatch({ type: 'toggle_calm' })}
      />
      <main className="workspace">
        <section className="left" style={{ flexBasis: `${split * 100}%` }}>
          {state.panes.length === 0 ? (
            <div className="empty-panes">
              <p>Start an agent in this repository.</p>
              <div>
                <button className="add big" style={{ ['--agent' as string]: '#e0825f' }} onClick={() => void addPane('claude_code')}>
                  + Claude
                </button>
                <button className="add big" style={{ ['--agent' as string]: '#3fbf96' }} onClick={() => void addPane('codex')}>
                  + Codex
                </button>
              </div>
              <p className="muted">⌘T new Claude pane · ⇧⌘T new Codex pane · ⌘W close</p>
            </div>
          ) : (
            <PaneStack
              panes={state.panes}
              headers={headers}
              onFocus={(id) => (focused.current = id)}
              onClose={closePane}
              onRestart={restartPane}
            />
          )}
        </section>
        <div className="divider-v" onPointerDown={startSplit} />
        <section className="right">
          <Banner state={state} />
          <ExternalList state={state} />
          <RepoMap state={state} now={now} />
        </section>
      </main>
    </div>
  )
}
