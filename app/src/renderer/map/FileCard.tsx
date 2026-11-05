import { sessionNames } from '../components/Banner'
import { Icon } from '../components/Icon'
import type { AppState } from '../state/store'
import { touchLines } from './RepoMap'

interface Props {
  state: AppState
  path: string
  now: number
  onClose: () => void
}

export function FileCard({ state, path, now, onClose }: Props): JSX.Element {
  const lines = touchLines(state, path, now)
  const overlap = state.overlaps[path]
  const change = state.changes[path]
  return (
    <div className="file-card">
      <div className="file-card-head">
        <code>{path}</code>
        <button className="close" onClick={onClose} aria-label="Close">
          <Icon name="xmark" size={12} />
        </button>
      </div>
      {lines.length === 0 && <p className="muted">No agent has touched this file in this session.</p>}
      {lines.map((l) => (
        <p key={l}>{l}</p>
      ))}
      {overlap && (
        <p className={overlap.level === 'active' ? 'overlap-text' : 'amber-text'}>
          {overlap.level === 'active' ? 'Overlap now' : 'Recent overlap'}: {sessionNames(state, overlap.sessions)}
        </p>
      )}
      {change && (
        <p className="muted">
          Changed outside a known edit{change.likely ? ' · likely a shell command from ' + sessionNames(state, [change.likely]) : ''}
        </p>
      )}
    </div>
  )
}
