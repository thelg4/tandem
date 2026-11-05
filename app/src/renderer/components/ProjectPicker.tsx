import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import iconUrl from '../../../resources/icon.png'
import { AGENT_COLORS, AGENT_NAMES, type Agent, type ProjectEntry, type ProjectList } from '../../shared/protocol'
import { relativeTime, tidyPath } from '../format'
import { shortcut } from '../platform'
import { Icon } from './Icon'

interface Props {
  /** The open repository; the picker is a dismissable sheet over it. Null: full window. */
  current: string | null
  onCancel?: () => void
}

const AGENTS: Agent[] = ['claude_code', 'codex']

function latest(p: ProjectEntry): string | null {
  return [p.lastActive ?? '', p.opened ?? ''].sort().at(-1) || null
}

/** A welcome window: recent projects from the agents' own logs, plus Open Other…. */
export function ProjectPicker({ current, onCancel }: Props): JSX.Element {
  const [list, setList] = useState<ProjectList | null>(null)
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const listRef = useRef<HTMLDivElement>(null)
  const now = Date.now()

  useEffect(() => {
    void window.tandem.projects().then(setList)
  }, [])

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    const all = list?.projects ?? []
    return q ? all.filter((p) => p.path.toLowerCase().includes(q)) : all
  }, [list, query])

  useEffect(() => setSelected(0), [query])
  useEffect(() => {
    listRef.current?.querySelector('.project.selected')?.scrollIntoView({ block: 'nearest' })
  }, [selected])

  const finish = (result: { ok: boolean; canceled?: boolean; error?: string }): void => {
    setBusy(false)
    if (!result.ok && !result.canceled) setError(result.error ?? 'Could not open that project.')
  }

  const open = (p: ProjectEntry | undefined): void => {
    if (!p || busy) return
    if (p.path === current) {
      onCancel?.()
      return
    }
    setBusy(true)
    setError(null)
    void window.tandem.openProject(p.path).then(finish)
  }

  const openOther = (): void => {
    if (busy) return
    setBusy(true)
    setError(null)
    void window.tandem.chooseProject().then(finish)
  }

  const onKey = (e: KeyboardEvent): void => {
    if (e.key === 'ArrowDown') setSelected((i) => Math.min(shown.length - 1, i + 1))
    else if (e.key === 'ArrowUp') setSelected((i) => Math.max(0, i - 1))
    else if (e.key === 'Enter') open(shown[selected])
    else if (e.key === 'Escape') onCancel?.()
    else if (e.key === 'o' && (e.metaKey || e.ctrlKey)) openOther()
    else return
    e.preventDefault()
  }

  const card = (
    <div className="picker" onKeyDown={onKey}>
      <div className="picker-side">
        <img src={iconUrl} alt="" className="picker-icon" />
        <h1>Tandem</h1>
        <p className="muted">Claude Code and Codex side by side, with a live map of what each one touches.</p>
        <span className="spacer" />
        <button className="push wide" onClick={openOther} disabled={busy}>
          Open Other…
        </button>
        <span className="picker-hint">{shortcut('O')}</span>
        {onCancel && (
          <button className="push wide" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
      <div className="picker-main">
        <input
          className="search"
          placeholder="Search projects"
          value={query}
          autoFocus
          onChange={(e) => setQuery(e.target.value)}
          spellCheck={false}
        />
        <div className="projects" ref={listRef} role="listbox" aria-label="Recent projects">
          {list === null && <p className="picker-empty">Looking for projects…</p>}
          {list !== null && shown.length === 0 && (
            <p className="picker-empty">
              {query ? 'No matching projects.' : 'No recent projects. Choose Open Other… to pick a repository.'}
            </p>
          )}
          {shown.map((p, i) => (
            <div
              key={p.path}
              role="option"
              aria-selected={i === selected}
              className={`project ${i === selected ? 'selected' : ''}`}
              onClick={() => setSelected(i)}
              onDoubleClick={() => open(p)}
              title={p.path}
            >
              <Icon name="folder" size={22} />
              <div className="project-text">
                <span className="project-name">
                  {p.name}
                  {p.path === current && <span className="project-current">Open</span>}
                </span>
                <span className="project-path">{tidyPath(p.path, list?.home ?? '')}</span>
              </div>
              <div className="project-meta">
                <span>{relativeTime(latest(p), now)}</span>
                <span className="project-agents">
                  {AGENTS.filter((a) => p.sessions[a]).map((a) => (
                    <span key={a} title={`${p.sessions[a]} ${AGENT_NAMES[a]} session(s)`}>
                      <span className="agent-dot" style={{ ['--agent' as string]: AGENT_COLORS[a] }} />
                      {p.sessions[a]}
                    </span>
                  ))}
                </span>
              </div>
            </div>
          ))}
        </div>
        <div className="picker-foot">
          {error ? (
            <span className="picker-error">{error}</span>
          ) : list && !list.logsRead ? (
            <span className="picker-error">Couldn’t read agent logs. Tandem needs Python 3.11+ as python3 on your PATH.</span>
          ) : (
            <span className="muted">Projects where Claude Code or Codex ran in the last 60 days.</span>
          )}
          <span className="spacer" />
          <button className="push primary" disabled={busy || !shown[selected]} onClick={() => open(shown[selected])}>
            Open
          </button>
        </div>
      </div>
    </div>
  )

  return current ? (
    <div className="sheet-scrim" onMouseDown={(e) => e.target === e.currentTarget && onCancel?.()}>
      {card}
    </div>
  ) : (
    <div className="welcome">
      <div className="welcome-drag" />
      {card}
    </div>
  )
}
