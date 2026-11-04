import { memo } from 'react'
import { AGENT_COLORS, type Agent } from '../../shared/protocol'
import type { Diag } from '../state/store'

interface Props {
  repoRoot: string | null
  calm: boolean
  diagnostics: Diag[]
  onAdd: (agent: Agent) => void
  onToggleCalm: () => void
}

export const Toolbar = memo(function Toolbar({ repoRoot, calm, diagnostics, onAdd, onToggleCalm }: Props): JSX.Element {
  const warnings = diagnostics.filter((d) => d.level === 'warn')
  return (
    <header className="toolbar">
      <span className="brand">tandem</span>
      <span className="repo" title={repoRoot ?? ''}>
        {repoRoot ? repoRoot.split('/').pop() : 'connecting…'}
      </span>
      <button className="add" style={{ ['--agent' as string]: AGENT_COLORS.claude_code }} onClick={() => onAdd('claude_code')} title="New Claude pane (⌘T)">
        + Claude
      </button>
      <button className="add" style={{ ['--agent' as string]: AGENT_COLORS.codex }} onClick={() => onAdd('codex')} title="New Codex pane (⇧⌘T)">
        + Codex
      </button>
      <span className="spacer" />
      {diagnostics.length > 0 && (
        <span className={`diag ${warnings.length ? 'warn' : ''}`} title={diagnostics.map((d) => d.message).join('\n')}>
          {warnings.length ? `⚠ ${warnings.length}` : `ⓘ ${diagnostics.length}`}
        </span>
      )}
      <button className={`toggle ${calm ? 'on' : ''}`} onClick={onToggleCalm} title="Turn pulses into static colors">
        Calm
      </button>
    </header>
  )
})
