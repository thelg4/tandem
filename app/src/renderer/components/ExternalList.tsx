import { AGENT_COLORS, AGENT_NAMES } from '../../shared/protocol'
import type { AppState } from '../state/store'

export function ExternalList({ state }: { state: AppState }): JSX.Element | null {
  const external = Object.values(state.sessions).filter((s) => s.external && !s.ended)
  if (external.length === 0) return null
  return (
    <div className="external">
      <span className="label">Outside the app</span>
      {external.map((s) => {
        const status = s.pending ? 'working' : s.state
        return (
          <span className="ext" key={s.id} style={{ ['--agent' as string]: AGENT_COLORS[s.agent] }} title={s.title ?? s.id}>
            <span className={`dot ${status}`} />
            {AGENT_NAMES[s.agent]} · {status} · {s.touched.length} file{s.touched.length === 1 ? '' : 's'}
            {s.title ? <span className="ext-title"> — {s.title}</span> : null}
          </span>
        )
      })}
    </div>
  )
}
