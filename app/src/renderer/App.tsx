import { useCallback, useEffect, useMemo, useReducer, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { AGENT_COLORS, type Agent } from '../shared/protocol'
import { Banner, sessionNames } from './components/Banner'
import { ExternalList } from './components/ExternalList'
import { PaneStack } from './components/PaneStack'
import type { PaneHeader } from './components/TerminalPane'
import { Toolbar } from './components/Toolbar'
import { RepoMap } from './map/RepoMap'
import { shortcut } from './platform'
import { forgetPane, getAct } from './state/paneActivity'
import { forgetPaneData, installPaneDataListener } from './state/paneData'
import { hasLiveDecay, paneSession, paneStatus, reuseUnchanged } from './state/selectors'
import { initialState, reduce } from './state/store'

const MAP_IDLE_TICK_MS = 30_000

/** Re-render on a clock: 4 fps while something fades or a pane is live, else 1 fps. */
function useNow(fast: boolean): number {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), fast ? 250 : 1000)
    return () => clearInterval(id)
  }, [fast])
  return now
}

export function App({ onShowProjects }: { onShowProjects: () => void }): JSX.Element {
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
    forgetPaneData(paneId)
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
        else if (event.kind === 'close-pane') {
          const target = focused.current ?? panesRef.current.at(-1)?.paneId
          if (target) closePane(target)
        }
      }),
    ]
    window.tandem.ready()
    return () => offs.forEach((off) => off())
  }, [addPane, closePane])

  const live = state.panes.some((p) => !p.exited)
  const decaying = hasLiveDecay(state, Date.now())
  const now = useNow(live || decaying)
  // While nothing on the map fades, its cells are settled and only its "n min ago"
  // labels move, so it gets a 30 s clock and its memo skips the other ticks. Rounding
  // up keeps every cell at or past its settled state.
  const mapNow = decaying ? now : Math.ceil(now / MAP_IDLE_TICK_MS) * MAP_IDLE_TICK_MS
  const prevHeaders = useRef<Record<string, PaneHeader>>({})

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
    prevHeaders.current = reuseUnchanged(prevHeaders.current, out)
    return prevHeaders.current
  }, [state, now])

  const onAdd = useCallback((agent: Agent) => void addPane(agent), [addPane])
  const onToggleCalm = useCallback(() => dispatch({ type: 'toggle_calm' }), [])
  const onFocus = useCallback((id: string) => {
    focused.current = id
  }, [])

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
        onAdd={onAdd}
        onToggleCalm={onToggleCalm}
        onShowProjects={onShowProjects}
      />
      <main className="workspace">
        <section className="left" style={{ flexBasis: `${split * 100}%` }}>
          {state.panes.length === 0 ? (
            <div className="empty-panes">
              <h2>No agents running</h2>
              <p className="muted">Start Claude Code or Codex in this repository.</p>
              <div>
                <button className="push big" onClick={() => void addPane('claude_code')}>
                  <span className="agent-dot" style={{ ['--agent' as string]: AGENT_COLORS.claude_code }} />
                  New Claude Pane
                </button>
                <button className="push big" onClick={() => void addPane('codex')}>
                  <span className="agent-dot" style={{ ['--agent' as string]: AGENT_COLORS.codex }} />
                  New Codex Pane
                </button>
              </div>
              <p className="hint">
                <kbd>{shortcut('T')}</kbd> Claude · <kbd>{shortcut('T', true)}</kbd> Codex · <kbd>{shortcut('W')}</kbd> close
              </p>
            </div>
          ) : (
            <PaneStack
              panes={state.panes}
              headers={headers}
              onFocus={onFocus}
              onClose={closePane}
              onRestart={restartPane}
            />
          )}
        </section>
        <div className="divider-v" onPointerDown={startSplit} />
        <section className="right">
          <Banner state={state} />
          <ExternalList state={state} />
          <RepoMap state={state} now={mapNow} />
        </section>
      </main>
    </div>
  )
}
