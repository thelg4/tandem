import { useState } from 'react'
import { AGENT_COLORS, AGENT_NAMES } from '../../shared/protocol'
import { Icon } from '../components/Icon'
import { relativeTime } from '../format'
import type { AppState, TimelineEntry } from '../state/store'

interface Props {
  state: AppState
  sessionId: string
  now: number
  selected: string | null
  onOpen: (path: string) => void
  onBack: () => void
}

function Row({ e, now, selected, onOpen }: { e: TimelineEntry; now: number; selected: boolean; onOpen: (p: string) => void }): JSX.Element {
  const slash = e.path.lastIndexOf('/')
  const verb = e.kind === 'edit' ? 'Edited' : 'Read'
  const span = e.at - e.first >= 60_000 ? ` from ${new Date(e.first).toLocaleTimeString()}` : ''
  return (
    <div
      role="listitem"
      className={`activity-row timeline-row tl-${e.kind} ${selected ? 'selected' : ''}`}
      onClick={() => onOpen(e.path)}
      title={`${verb} ${e.path}${e.count > 1 ? ` ${e.count} times` : ''}${span}`}
    >
      <span className="timeline-kind" aria-label={verb}>
        <Icon name={e.kind === 'edit' ? 'pencil' : 'eye'} size={14} />
      </span>
      <div className="activity-text">
        <span className="activity-name">{e.path.slice(slash + 1)}</span>
        {slash > 0 && <span className="activity-dir">{e.path.slice(0, slash)}</span>}
      </div>
      <div className="activity-tags">
        {e.count > 1 && <span className="pill grey">×{e.count}</span>}
        <span className="touch-age" title={new Date(e.at).toLocaleString()}>
          {relativeTime(new Date(e.at).toISOString(), now)}
        </span>
      </div>
    </div>
  )
}

/** What one session read and edited, newest first, so you can review it before committing. */
export function SessionTimeline({ state, sessionId, now, selected, onOpen, onBack }: Props): JSX.Element {
  const [only, setOnly] = useState<'edits' | 'all'>('edits')
  const s = state.sessions[sessionId]
  const timeline = s?.timeline ?? []
  const edited = new Set(timeline.filter((e) => e.kind === 'edit').map((e) => e.path)).size
  const read = new Set(timeline.filter((e) => e.kind === 'read').map((e) => e.path)).size
  const rows = (only === 'edits' ? timeline.filter((e) => e.kind === 'edit') : timeline).slice().reverse()
  return (
    <div className="file-view">
      <div className="file-head">
        <button className="tool back" onClick={onBack} aria-label="Back">
          <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
            <path d="M6.5 1.5L3 5l3.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        {s && <span className="agent-dot" style={{ ['--agent' as string]: AGENT_COLORS[s.agent] }} />}
        <div className="file-title">
          <span className="activity-name">{s ? `${AGENT_NAMES[s.agent]}${s.title ? ` · ${s.title}` : ''}` : 'Session'}</span>
          <span className="timeline-sub">
            {edited} edited · {read} read{s?.ended ? ' · session ended' : ''}
          </span>
        </div>
        <span className="spacer" />
        <div className="segmented small" role="tablist" aria-label="Show">
          <button role="tab" aria-selected={only === 'edits'} className={only === 'edits' ? 'on' : ''} onClick={() => setOnly('edits')}>
            Edits
          </button>
          <button role="tab" aria-selected={only === 'all'} className={only === 'all' ? 'on' : ''} onClick={() => setOnly('all')}>
            All
          </button>
        </div>
      </div>
      {rows.length === 0 ? (
        <p className="view-note">
          {only === 'edits' && timeline.length > 0
            ? 'No edits yet in this session. Choose All to see what it read.'
            : 'Nothing yet. Files show up here as this session reads and edits them.'}{' '}
          Changes made with shell commands don’t appear, because the logs don’t record them as edits.
        </p>
      ) : (
        <div className="activity timeline" role="list">
          {rows.map((e, i) => (
            <Row key={`${e.first}-${e.path}-${e.kind}-${i}`} e={e} now={now} selected={selected === e.path} onOpen={onOpen} />
          ))}
        </div>
      )}
    </div>
  )
}
