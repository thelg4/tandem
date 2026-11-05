import { memo } from 'react'
import { AGENT_COLORS, type Agent } from '../../shared/protocol'
import { shortcut } from '../platform'
import type { Diag } from '../state/store'
import { Icon } from './Icon'

interface Props {
  repoRoot: string | null
  calm: boolean
  diagnostics: Diag[]
  onAdd: (agent: Agent) => void
  onToggleCalm: () => void
}

/** A unified window toolbar: on macOS the traffic lights sit at its left edge. */
export const Toolbar = memo(function Toolbar({ repoRoot, calm, diagnostics, onAdd, onToggleCalm }: Props): JSX.Element {
  const warnings = diagnostics.filter((d) => d.level === 'warn')
  return (
    <header className="toolbar">
      <div className="toolbar-title" title={repoRoot ?? ''}>
        <span className="title">{repoRoot ? repoRoot.split('/').pop() : 'Connecting…'}</span>
        <span className="subtitle">Tandem</span>
      </div>
      <span className="spacer" />
      {diagnostics.length > 0 && (
        <span className={`diag ${warnings.length ? 'warn' : ''}`} title={diagnostics.map((d) => d.message).join('\n')}>
          <Icon name={warnings.length ? 'warning' : 'info'} />
          {warnings.length || diagnostics.length}
        </span>
      )}
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
