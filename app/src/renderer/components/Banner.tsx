import { memo, useState } from 'react'
import { AGENT_NAMES, type Clash } from '../../shared/protocol'
import { relativeTime } from '../format'
import type { AppState } from '../state/store'
import { Icon } from './Icon'

export function sessionNames(state: AppState, ids: string[]): string {
  const agents = ids.map((id) => state.sessions[id]?.agent).filter((a) => a !== undefined)
  if (agents.length > 1 && agents.every((a) => a === agents[0])) {
    return `${agents.length} ${AGENT_NAMES[agents[0]!]} sessions`
  }
  const names = agents.map((a) => AGENT_NAMES[a])
  return names.length <= 1 ? (names[0] ?? 'Sessions') : `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`
}

export const CLASH_VERBS: Record<Clash['label'], string> = {
  exact_revert: 'reverted',
  partial_revert: 'partly reverted',
  rewrite: 'rewrote',
  removal: 'removed',
}

/** "Codex reverted 4 lines Claude wrote" */
export function clashText(c: Clash): string {
  const lines = c.lines === 1 ? '1 line' : `${c.lines} lines`
  return `${AGENT_NAMES[c.later.agent]} ${CLASH_VERBS[c.label]} ${lines} ${AGENT_NAMES[c.original.agent]} wrote`
}

interface Props {
  state: AppState
  clashes: Clash[]
  now: number
  onOpen: (path: string) => void
}

export const Banner = memo(function Banner({ state, clashes, now, onOpen }: Props): JSX.Element | null {
  const [dismissed, setDismissed] = useState<Record<string, number>>({})
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set())
  const active = Object.entries(state.overlaps).filter(
    ([path, o]) => o.level === 'active' && o.at > (dismissed[path] ?? 0),
  )
  const shown = clashes.filter((c) => !hidden.has(c.id)).slice(0, 3)
  if (active.length === 0 && shown.length === 0) return null
  return (
    <div className="banner">
      {shown.map((c) => (
        <div className="banner-row clash" key={c.id}>
          <Icon name="warning" />
          <button className="banner-text banner-link" onClick={() => onOpen(c.path)} title="Show this file’s edits">
            {clashText(c)} in <code>{c.path}</code>
            {c.later.time && <span className="banner-age"> · {relativeTime(c.later.time, now)}</span>}
          </button>
          <button onClick={() => setHidden((h) => new Set(h).add(c.id))} title="Dismiss" aria-label="Dismiss">
            <Icon name="xmark" size={12} />
          </button>
        </div>
      ))}
      {active.map(([path, o]) => (
        <div className="banner-row" key={path}>
          <Icon name="warning" />
          <span className="banner-text">
            {sessionNames(state, o.sessions)} are both editing <code>{path}</code>
          </span>
          <button onClick={() => setDismissed((d) => ({ ...d, [path]: o.at }))} title="Hide until it changes" aria-label="Hide">
            <Icon name="xmark" size={12} />
          </button>
        </div>
      ))}
    </div>
  )
})
