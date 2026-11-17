import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { AGENT_COLORS, AGENT_NAMES, type HistoryEntry } from '../../shared/protocol'
import { relativeTime } from '../format'
import { chatRows, filterChats, groupChats, type ChatRow } from '../state/history'
import type { PaneStatus } from '../state/selectors'
import type { AppState } from '../state/store'

const REFRESH_MS = 60_000
/** After a session starts, ends or is retitled, wait for its log to settle. */
const SETTLE_MS = 1500

interface Props {
  state: AppState
  now: number
  /** Status of each open pane, for its row's dot. */
  statuses: Record<string, PaneStatus>
  focusedPane: string | null
  onOpen: (row: ChatRow) => void
}

/** What makes the logs worth reading again: a session appeared, ended, moved or was retitled. */
function sessionsKey(state: AppState): string {
  return Object.values(state.sessions)
    .map((s) => `${s.id}|${s.ended ? 1 : 0}|${s.paneId ?? ''}|${s.title ?? ''}`)
    .sort()
    .join('\n')
}

/** Chat history: this repository's Claude and Codex sessions, like a chat app's list. */
export const Sidebar = memo(function Sidebar({ state, now, statuses, focusedPane, onOpen }: Props): JSX.Element {
  const [history, setHistory] = useState<HistoryEntry[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [query, setQuery] = useState('')
  const key = sessionsKey(state)
  const loading = useRef(false)

  useEffect(() => {
    let live = true
    const load = (): void => {
      if (loading.current) return
      loading.current = true
      void window.tandem.sessions().then((list) => {
        loading.current = false
        if (!live) return
        setFailed(list === null)
        if (list) setHistory(list)
      })
    }
    const settle = setTimeout(load, history === null ? 0 : SETTLE_MS)
    const tick = setInterval(load, REFRESH_MS)
    return () => {
      live = false
      loading.current = false
      clearTimeout(settle)
      clearInterval(tick)
    }
    // Reload when the watcher's view of sessions changes; ``history`` only picks the delay.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  // Minute resolution is plenty for "5 min ago" and keeps the list from re-sorting each tick.
  const minute = Math.floor(now / 60_000) * 60_000
  const rows = useMemo(() => chatRows(history ?? [], state, minute), [history, state, minute])
  const groups = useMemo(() => groupChats(filterChats(rows, query), minute), [rows, query, minute])

  return (
    <nav className="sidebar" aria-label="Chat history">
      <div className="sidebar-search">
        <input
          type="search"
          className="search"
          placeholder="Search chats"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') setQuery('')
          }}
        />
      </div>
      <div className="sidebar-list">
        {history === null && !failed && <p className="sidebar-note">Reading sessions…</p>}
        {failed && history === null && <p className="sidebar-note">Couldn’t read the agents’ logs.</p>}
        {history !== null && groups.length === 0 && (
          <p className="sidebar-note">{query ? 'No matching chats.' : 'No chats in this repository yet.'}</p>
        )}
        {groups.map((g) => (
          <section key={g.label} className="chat-group">
            <h3>{g.label}</h3>
            {g.rows.map((r) => (
              <ChatItem
                key={r.key}
                row={r}
                now={minute}
                status={r.paneId ? statuses[r.paneId] : undefined}
                focused={r.paneId !== null && r.paneId === focusedPane}
                onOpen={onOpen}
              />
            ))}
          </section>
        ))}
      </div>
    </nav>
  )
})

function ChatItem({
  row,
  now,
  status,
  focused,
  onOpen,
}: {
  row: ChatRow
  now: number
  status: PaneStatus | undefined
  focused: boolean
  onOpen: (row: ChatRow) => void
}): JSX.Element {
  const name = AGENT_NAMES[row.agent]
  const title = row.title ?? (row.paneId ? `New ${name} chat` : `Untitled ${name} chat`)
  const hint = row.elsewhere
    ? `${name} · running outside Tandem`
    : row.paneId
      ? `${name} · show its pane`
      : `${name} · resume in a new pane`
  const meta = row.elsewhere
    ? 'elsewhere'
    : row.paneId
      ? status === 'waiting'
        ? 'needs you'
        : (status ?? '')
      : relativeTime(new Date(row.at).toISOString(), now)
  return (
    <button
      className={`chat-item ${row.paneId ? 'open' : ''} ${focused ? 'focused' : ''}`}
      style={{ ['--agent' as string]: AGENT_COLORS[row.agent] }}
      disabled={row.elsewhere}
      title={`${title}\n${hint}`}
      onClick={() => onOpen(row)}
    >
      {row.paneId ? <span className={`dot ${status ?? 'starting'}`} /> : <span className="agent-dot" />}
      <span className="chat-title">{title}</span>
      <span className={`chat-meta ${status === 'waiting' ? 'waiting' : ''}`}>{meta}</span>
    </button>
  )
}
