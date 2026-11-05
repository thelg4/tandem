import { AGENT_COLORS, AGENT_NAMES } from '../../shared/protocol'
import { Icon } from '../components/Icon'
import { activityRows, cellVisual, GREY, type ActivityTouch } from '../state/selectors'
import type { AppState } from '../state/store'

function short(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 10) return 'now'
  if (s < 60) return `${s}s`
  const m = Math.round(s / 60)
  return m < 60 ? `${m}m` : `${Math.round(m / 60)}h`
}

function Touch({ t, now }: { t: ActivityTouch; now: number }): JSX.Element {
  const name = t.agent ? AGENT_NAMES[t.agent] : 'Unknown'
  const verb = t.kind === 'edit' ? 'Edited' : 'Read'
  return (
    <span
      className={`touch ${t.kind}`}
      style={{ ['--agent' as string]: t.agent ? AGENT_COLORS[t.agent] : GREY }}
      title={`${name}${t.external ? ' (outside the app)' : ''} ${verb.toLowerCase()} this ${short(now - t.at)} ago`}
    >
      <span className="agent-dot" />
      {verb} <span className="touch-age">{short(now - t.at)}</span>
    </span>
  )
}

interface Props {
  state: AppState
  now: number
  selected: string | null
  onSelect: (path: string) => void
}

/** Every file an agent read or edited, newest first: name first, path second. */
export function ActivityList({ state, now, selected, onSelect }: Props): JSX.Element {
  const rows = activityRows(state)
  if (rows.length === 0) {
    return (
      <div className="activity-empty">
        <Icon name="doc" size={28} />
        <p>No file activity yet</p>
        <p className="muted">Files show up here as Claude Code and Codex read and edit them.</p>
      </div>
    )
  }
  return (
    <div className="activity" role="list">
      {rows.map((row) => {
        const slash = row.path.lastIndexOf('/')
        const name = row.path.slice(slash + 1)
        const dir = slash > 0 ? row.path.slice(0, slash) : ''
        const v = cellVisual(state, row.path, now)
        const accent = v.editAlpha > 0 ? v.editColor : v.readAlpha > 0 ? v.readColor : undefined
        const strength = Math.max(v.editAlpha, v.readAlpha * 0.6)
        return (
          <div
            key={row.path}
            role="listitem"
            className={`activity-row ${selected === row.path ? 'selected' : ''} ${row.overlap === 'active' ? 'overlap' : ''}`}
            onClick={() => onSelect(row.path)}
            title={row.path}
          >
            <span
              className="activity-bar"
              style={{ background: accent ?? 'transparent', opacity: accent ? 0.25 + 0.75 * strength : 0 }}
            />
            <Icon name="doc" size={15} />
            <div className="activity-text">
              <span className="activity-name">{name}</span>
              {dir && <span className="activity-dir">{dir}</span>}
            </div>
            <div className="activity-tags">
              {row.overlap === 'active' && <span className="pill red">Both editing</span>}
              {row.overlap === 'recent' && <span className="pill amber">Overlap</span>}
              {row.change && (
                <span className="pill grey" title="Changed by something other than an agent's edit tool, such as a shell command">
                  Changed outside {short(now - row.change.at)}
                </span>
              )}
              {row.touches.slice(0, 2).map((t) => (
                <Touch key={t.sessionId} t={t} now={now} />
              ))}
              {row.touches.length > 2 && <span className="muted">+{row.touches.length - 2}</span>}
            </div>
          </div>
        )
      })}
    </div>
  )
}
