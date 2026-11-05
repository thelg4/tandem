import { memo } from 'react'
import { AGENT_COLORS, type Agent } from '../../shared/protocol'
import { shortcut } from '../platform'
import type { Diag } from '../state/store'
import { Icon } from './Icon'
import { Usage } from './Usage'

interface Props {
  repoRoot: string | null
  calm: boolean
  diagnostics: Diag[]
  onAdd: (agent: Agent) => void
  onToggleCalm: () => void
  onShowProjects: () => void
}

/** A unified window toolbar: on macOS the traffic lights sit at its left edge. */
export const Toolbar = memo(function Toolbar({ repoRoot, calm, diagnostics, onAdd, onToggleCalm, onShowProjects }: Props): JSX.Element {
  const warnings = diagnostics.filter((d) => d.level === 'warn')
  return (
    <header className="toolbar">
      <button className="toolbar-title" onClick={onShowProjects} aria-label={`Switch project (${shortcut('O')})`}>
        <span className="title">
          {repoRoot ? repoRoot.split('/').pop() : 'Connecting…'}
          <svg className="chevron" width="9" height="9" viewBox="0 0 10 10" aria-hidden="true">
            <path d="M2 3.5l3 3 3-3" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
        <span className="subtitle">Tandem</span>
      </button>
      <span className="spacer" />
      {diagnostics.length > 0 && (
        <span className={`diag ${warnings.length ? 'warn' : ''}`} title={diagnostics.map((d) => d.message).join('\n')}>
          <Icon name={warnings.length ? 'warning' : 'info'} />
          {warnings.length || diagnostics.length}
        </span>
      )}
      <Usage />
      <div className="segmented" role="group" aria-label="New pane">
        <button onClick={() => onAdd('claude_code')} title={`New Claude pane (${shortcut('T')})`}>
          <span className="agent-dot" style={{ ['--agent' as string]: AGENT_COLORS.claude_code }} />
          Claude
        </button>
        <button onClick={() => onAdd('codex')} title={`New Codex pane (${shortcut('T', true)})`}>
          <span className="agent-dot" style={{ ['--agent' as string]: AGENT_COLORS.codex }} />
          Codex
        </button>
      </div>
      <button
        className={`tool ${calm ? 'on' : ''}`}
        aria-pressed={calm}
        onClick={onToggleCalm}
        title="Calm: turn pulses into static colors"
      >
        <Icon name="calm" />
        Calm
      </button>
    </header>
  )
})
