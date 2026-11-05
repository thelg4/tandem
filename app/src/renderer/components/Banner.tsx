import { memo, useState } from 'react'
import { AGENT_NAMES } from '../../shared/protocol'
import type { AppState } from '../state/store'

export function sessionNames(state: AppState, ids: string[]): string {
  const agents = ids.map((id) => state.sessions[id]?.agent).filter((a) => a !== undefined)
  if (agents.length > 1 && agents.every((a) => a === agents[0])) {
    return `${agents.length} ${AGENT_NAMES[agents[0]!]} sessions`
  }
  const names = agents.map((a) => AGENT_NAMES[a])
  return names.length <= 1 ? (names[0] ?? 'Sessions') : `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`
}

export const Banner = memo(function Banner({ state }: { state: AppState }): JSX.Element | null {
  const [dismissed, setDismissed] = useState<Record<string, number>>({})
  const active = Object.entries(state.overlaps).filter(
    ([path, o]) => o.level === 'active' && o.at > (dismissed[path] ?? 0),
  )
  if (active.length === 0) return null
  return (
    <div className="banner">
      {active.map(([path, o]) => (
        <div className="banner-row" key={path}>
          <span>
            ⚠ {sessionNames(state, o.sessions)} are both editing <code>{path}</code>
          </span>
          <button onClick={() => setDismissed((d) => ({ ...d, [path]: o.at }))} title="Hide until it changes">
            ×
          </button>
        </div>
      ))}
    </div>
  )
})
