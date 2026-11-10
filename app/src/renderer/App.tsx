import { useCallback, useEffect, useMemo, useReducer, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { AGENT_COLORS, AGENT_NAMES, type Agent, type Clash, type ScanStatus } from '../shared/protocol'
import { Banner, sessionNames } from './components/Banner'
import { ExternalList } from './components/ExternalList'
import { PaneStack } from './components/PaneStack'
import { Sidebar } from './components/Sidebar'
import { SkillsSheet } from './components/SkillsSheet'
import { focusPane, type PaneHeader } from './components/TerminalPane'
import { Toolbar } from './components/Toolbar'
import { RepoMap } from './map/RepoMap'
import { shortcut } from './platform'
import type { ChatRow } from './state/history'
import { paneNotices, type PaneMemory } from './state/notify'
import { forgetPane, getAct } from './state/paneActivity'
import { forgetPaneData, installPaneDataListener } from './state/paneData'
import { hasLiveDecay, paneSession, paneStatus, reuseUnchanged } from './state/selectors'
import { initialState, reduce } from './state/store'

const MAP_IDLE_TICK_MS = 30_000
const SIDEBAR_KEY = 'tandem.sidebar'

function storedSidebar(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_KEY) !== '0'
  } catch {
    return true
  }
}

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
  const [timeline, setTimeline] = useState<string | null>(null)
  const [skills, setSkills] = useState(false)
  const [sidebar, setSidebar] = useState(storedSidebar)
  const [focusedPane, setFocusedPane] = useState<string | null>(null)
  const resuming = useRef(new Set<string>())
  const workspace = useRef<HTMLElement>(null)
  const [scan, setScan] = useState<ScanStatus>({ state: 'never', at: null })
  const [clashes, setClashes] = useState<Clash[]>([])
  // A request to open a file in the right panel; ``n`` makes a repeat request count.
  const [openRequest, setOpenRequest] = useState<{ path: string; n: number } | null>(null)
  const openFile = useCallback((path: string) => setOpenRequest((r) => ({ path, n: (r?.n ?? 0) + 1 })), [])
  const focused = useRef<string | null>(null)
  const panesRef = useRef(state.panes)
  panesRef.current = state.panes

  const addPane = useCallback(async (agent: Agent, resume?: string) => {
    const { paneId } = await window.tandem.createPane(agent, resume)
    dispatch({ type: 'pane_added', paneId, agent })
  }, [])

  const toggleSidebar = useCallback(() => {
    setSidebar((on) => {
      try {
        localStorage.setItem(SIDEBAR_KEY, on ? '0' : '1')
      } catch {
        // Storage unavailable; the choice lasts this session.
      }
      return !on
    })
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
        else if (event.kind === 'open-file') openFile(event.path)
        else if (event.kind === 'show-skills') setSkills(true)
        else if (event.kind === 'toggle-sidebar') toggleSidebar()
        else if (event.kind === 'focus-pane') {
          if (focusPane(event.paneId)) {
          focused.current = event.paneId
          setFocusedPane(event.paneId)
        }
        } else if (event.kind === 'close-pane') {
          const target = focused.current ?? panesRef.current.at(-1)?.paneId
          if (target) closePane(target)
        }
      }),
    ]
    offs.push(window.tandem.onScanStatus(setScan), window.tandem.onClashes(setClashes))
    void window.tandem.scanStatus().then(setScan)
    void window.tandem.clashes().then(setClashes)
    window.tandem.ready()
    return () => offs.forEach((off) => off())
  }, [addPane, closePane, openFile, toggleSidebar])

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
        sessionId: s?.id ?? null,
        files: s ? new Set(s.timeline.map((e) => e.path)).size : 0,
        currentFile: fresh ? { path: fresh.path, kind: fresh.kind } : null,
        overlap: overlapEntry
          ? { path: overlapEntry[0], others: sessionNames(state, overlapEntry[1].sessions.filter((id) => id !== s!.id)) }
          : null,
      }
    }
    prevHeaders.current = reuseUnchanged(prevHeaders.current, out)
    return prevHeaders.current
  }, [state, now])

  // System notifications and the Dock badge follow the pane headers. The main process
  // drops notifications while Tandem is the focused window.
  const noticeMemory = useRef<Record<string, PaneMemory>>({})
  const badge = useRef(0)
  const panesNow = state.panes
  useEffect(() => {
    const statuses = Object.fromEntries(Object.entries(headers).map(([id, h]) => [id, h.status]))
    const { memory, notices } = paneNotices(noticeMemory.current, statuses, Date.now())
    noticeMemory.current = memory
    for (const n of notices) {
      const pane = panesNow.find((p) => p.paneId === n.paneId)
      const h = headers[n.paneId]
      if (!pane || !h) continue
      const name = AGENT_NAMES[pane.agent]
      if (n.reason === 'waiting') {
        window.tandem.notify({
          paneId: n.paneId,
          title: `${name} needs you`,
          body: h.title ? `${h.title}\nWaiting for your approval or input.` : 'Waiting for your approval or input.',
        })
      } else {
        window.tandem.notify({ paneId: n.paneId, title: `${name} finished`, body: h.title ?? 'Ready for your next prompt.' })
      }
    }
    const waiting = Object.values(statuses).filter((st) => st === 'waiting').length
    if (waiting !== badge.current) {
      badge.current = waiting
      window.tandem.setBadge(waiting)
    }
  }, [headers, panesNow])

  const onTimeline = useCallback((sessionId: string) => setTimeline(sessionId), [])
  const onCloseTimeline = useCallback(() => setTimeline(null), [])
  const onAdd = useCallback((agent: Agent) => void addPane(agent), [addPane])
  const onToggleCalm = useCallback(() => dispatch({ type: 'toggle_calm' }), [])
  const onShowSkills = useCallback(() => setSkills(true), [])
  const onHideSkills = useCallback(() => setSkills(false), [])
  const onFocus = useCallback((id: string) => {
    focused.current = id
    setFocusedPane(id)
  }, [])

  // A chat in the sidebar: bring its pane forward, or resume it in a new one.
  const onOpenChat = useCallback(
    (row: ChatRow) => {
      if (row.paneId) {
        if (focusPane(row.paneId)) onFocus(row.paneId)
        return
      }
      if (row.elsewhere || !row.rawId || resuming.current.has(row.key)) return
      resuming.current.add(row.key)
      void addPane(row.agent, row.rawId).finally(() => {
        // The watcher binds the session to its pane within a poll or two.
        setTimeout(() => resuming.current.delete(row.key), 5000)
      })
    },
    [addPane, onFocus],
  )

  const statuses = useMemo(
    () => Object.fromEntries(Object.entries(headers).map(([id, h]) => [id, h.status])),
    [headers],
  )

  const startSplit = (e: ReactPointerEvent<HTMLDivElement>): void => {
    e.preventDefault()
    const box = workspace.current?.getBoundingClientRect()
    if (!box) return
    const move = (ev: PointerEvent): void =>
      setSplit(Math.min(0.8, Math.max(0.25, (ev.clientX - box.left) / box.width)))
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
        sidebar={sidebar}
        diagnostics={state.diagnostics}
        onAdd={onAdd}
        onToggleCalm={onToggleCalm}
        onToggleSidebar={toggleSidebar}
        onShowProjects={onShowProjects}
        onShowSkills={onShowSkills}
      />
      <div className="body">
        {sidebar && (
          <Sidebar state={state} now={now} statuses={statuses} focusedPane={focusedPane} onOpen={onOpenChat} />
        )}
        <main className="workspace" ref={workspace}>
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
                onTimeline={onTimeline}
              />
            )}
          </section>
          <div className="divider-v" onPointerDown={startSplit} />
          <section className="right">
            <Banner state={state} clashes={clashes} now={mapNow} onOpen={openFile} />
            <ExternalList state={state} />
            <RepoMap
              state={state}
              now={mapNow}
              session={timeline}
              onCloseSession={onCloseTimeline}
              openRequest={openRequest}
              scan={scan}
            />
          </section>
        </main>
      </div>
      {skills && <SkillsSheet onClose={onHideSkills} />}
    </div>
  )
}
