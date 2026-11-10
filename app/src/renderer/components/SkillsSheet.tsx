import { useCallback, useEffect, useState, type KeyboardEvent } from 'react'
import {
  AGENT_COLORS,
  AGENT_NAMES,
  type Agent,
  type AgentExtras,
  type ExtrasResult,
  type McpEntry,
  type PluginEntry,
  type Skill,
  type SkillScope,
} from '../../shared/protocol'
import { Icon } from './Icon'

const AGENTS: Agent[] = ['claude_code', 'codex']
type Tab = 'skills' | 'plugins' | 'mcp'

const SCOPE_TITLE: Record<SkillScope, string> = { project: 'This Project', user: 'All Projects' }

function other(agent: Agent): Agent {
  return agent === 'claude_code' ? 'codex' : 'claude_code'
}

function AgentHead({ agent }: { agent: Agent }): JSX.Element {
  return (
    <span className="extras-agent">
      <span className="agent-dot" style={{ ['--agent' as string]: AGENT_COLORS[agent] }} />
      {AGENT_NAMES[agent]}
    </span>
  )
}

interface CellProps {
  skill: Skill
  agent: Agent
  busy: boolean
  run: (p: Promise<ExtrasResult>) => void
}

/** One agent's column for a skill: installed (reveal, remove), or a button to copy it over. */
function SkillCell({ skill, agent, busy, run }: CellProps): JSX.Element {
  const copy = skill.copies[agent]
  if (!copy) {
    return (
      <button
        className="push small"
        disabled={busy}
        onClick={() => run(window.tandem.copySkill(skill.name, skill.scope, agent))}
        title={`Copy ${AGENT_NAMES[other(agent)]}’s ${skill.name} into ${AGENT_NAMES[agent]}’s skills`}
      >
        <Icon name="plus" size={11} />
        Add
      </button>
    )
  }
  return (
    <span className="skill-have">
      <Icon name="check" size={12} />
      {copy.linked && <span className="tag" title={`${copy.path} is a link`}>link</span>}
      <button
        className="icon-button"
        title={`Show in ${window.tandem.platform === 'darwin' ? 'Finder' : 'file manager'}`}
        onClick={() => window.tandem.revealSkill(skill.name, skill.scope, agent)}
      >
        <Icon name="folder" size={12} />
      </button>
      <button
        className="icon-button"
        disabled={busy}
        title={`Remove from ${AGENT_NAMES[agent]}`}
        onClick={() => run(window.tandem.removeSkill(skill.name, skill.scope, agent))}
      >
        <Icon name="trash" size={12} />
      </button>
    </span>
  )
}

function SkillsTab({ extras, busy, run }: { extras: AgentExtras; busy: boolean; run: CellProps['run'] }): JSX.Element {
  const scopes: SkillScope[] = extras.dirs.project ? ['project', 'user'] : ['user']
  return (
    <div className="extras-list">
      <div className="skill-row skill-head">
        <span />
        {AGENTS.map((a) => (
          <AgentHead key={a} agent={a} />
        ))}
      </div>
      {scopes.map((scope) => {
        const rows = extras.skills.filter((s) => s.scope === scope)
        const dirs = extras.dirs[scope]
        return (
          <section key={scope}>
            <h3 title={dirs ? `${dirs.claude_code}\n${dirs.codex}` : undefined}>{SCOPE_TITLE[scope]}</h3>
            {rows.length === 0 && <p className="extras-empty">No skills yet.</p>}
            {rows.map((s) => (
              <div className="skill-row" key={s.name}>
                <div className="skill-text">
                  <span className="skill-name">
                    {s.name}
                    {s.differs && (
                      <span className="tag warn" title="Each agent has its own SKILL.md for this, and they differ.">
                        differs
                      </span>
                    )}
                  </span>
                  <span className="skill-desc" title={s.description}>
                    {s.description || 'No description.'}
                  </span>
                </div>
                {AGENTS.map((a) => (
                  <div className="skill-cell" key={a}>
                    <SkillCell skill={s} agent={a} busy={busy} run={run} />
                  </div>
                ))}
              </div>
            ))}
          </section>
        )
      })}
    </div>
  )
}

function Columns<T>({
  items,
  render,
  empty,
}: {
  items: (T & { agent: Agent })[]
  render: (item: T) => JSX.Element
  empty: string
}): JSX.Element {
  return (
    <div className="extras-columns">
      {AGENTS.map((agent) => {
        const mine = items.filter((i) => i.agent === agent)
        return (
          <div key={agent} className="extras-column">
            <h3>
              <AgentHead agent={agent} />
              <span className="count">{mine.length}</span>
            </h3>
            {mine.length === 0 && <p className="extras-empty">{empty}</p>}
            {mine.map(render)}
          </div>
        )
      })}
    </div>
  )
}

function PluginItem(p: PluginEntry): JSX.Element {
  return (
    <div className={`extras-item ${p.enabled ? '' : 'off'}`} key={p.id} title={p.id}>
      <span className="skill-name">
        {p.name}
        {p.version && <span className="extras-version">{p.version}</span>}
        {!p.enabled && <span className="tag">off</span>}
      </span>
      <span className="skill-desc">{p.description ?? p.marketplace ?? ''}</span>
    </div>
  )
}

function McpItem(m: McpEntry): JSX.Element {
  return (
    <div className="extras-item" key={`${m.scope}:${m.name}`} title={m.target ?? undefined}>
      <span className="skill-name">
        {m.name}
        {m.scope === 'project' && <span className="tag">project</span>}
      </span>
      <span className="skill-desc mono">{m.target ?? ''}</span>
    </div>
  )
}

/**
 * Skills, plugins and MCP servers for both agents, side by side. Skills share one
 * format (a folder with a SKILL.md), so they can be copied between agents and installed
 * for both at once. Plugins and MCP servers are each agent's own and are shown as-is.
 */
export function SkillsSheet({ onClose }: { onClose: () => void }): JSX.Element {
  const [extras, setExtras] = useState<AgentExtras | null>(null)
  const [tab, setTab] = useState<Tab>('skills')
  const [scope, setScope] = useState<SkillScope>('user')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState<{ text: string; error: boolean } | null>(null)

  const reload = useCallback(() => {
    void window.tandem.extras().then((x) => {
      setExtras(x)
      if (!x.dirs.project) setScope('user')
    })
  }, [])

  useEffect(() => {
    reload()
    window.addEventListener('focus', reload)
    return () => window.removeEventListener('focus', reload)
  }, [reload])

  const run = useCallback(
    (p: Promise<ExtrasResult>) => {
      setBusy(true)
      void p.then((r) => {
        setBusy(false)
        if (r.ok) setStatus(r.message ? { text: r.message, error: false } : null)
        else if (!r.canceled) setStatus({ text: r.error ?? 'Something went wrong.', error: true })
        reload()
      })
    },
    [reload],
  )

  const onKey = (e: KeyboardEvent): void => {
    if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
    }
  }

  return (
    <div className="sheet-scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="extras" onKeyDown={onKey} tabIndex={-1} ref={(el) => el?.focus()}>
        <div className="extras-top">
          <div className="segmented" role="tablist">
            {(
              [
                ['skills', 'Skills'],
                ['plugins', 'Plugins'],
                ['mcp', 'MCP Servers'],
              ] as [Tab, string][]
            ).map(([t, label]) => (
              <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? 'on' : ''} onClick={() => setTab(t)}>
                {label}
              </button>
            ))}
          </div>
          <span className="spacer" />
          <button className="icon-button" onClick={onClose} aria-label="Close">
            <Icon name="xmark" />
          </button>
        </div>
        <div className="extras-body">
          {extras === null ? (
            <p className="extras-empty">Reading agent folders…</p>
          ) : tab === 'skills' ? (
            <SkillsTab extras={extras} busy={busy} run={run} />
          ) : tab === 'plugins' ? (
            <Columns items={extras.plugins} render={PluginItem} empty="No plugins installed." />
          ) : (
            <Columns items={extras.mcp} render={McpItem} empty="No MCP servers configured." />
          )}
        </div>
        <div className="picker-foot extras-foot">
          {status ? (
            <span className={status.error ? 'picker-error' : 'muted'}>{status.text}</span>
          ) : tab === 'skills' ? (
            <span className="muted">Running agents may need a restart to pick up new skills.</span>
          ) : (
            <span className="muted">Each agent installs its own {tab === 'plugins' ? 'plugins' : 'MCP servers'}; shown here read-only.</span>
          )}
          <span className="spacer" />
          {tab === 'skills' && (
            <>
              {extras?.dirs.project && <span className="muted">Install in</span>}
              {extras?.dirs.project && (
                <div className="segmented" role="group" aria-label="Install for">
                  {(['project', 'user'] as SkillScope[]).map((s) => (
                    <button key={s} className={scope === s ? 'on' : ''} aria-pressed={scope === s} onClick={() => setScope(s)}>
                      {SCOPE_TITLE[s]}
                    </button>
                  ))}
                </div>
              )}
              <button
                className="push primary"
                disabled={busy || extras === null}
                onClick={() => run(window.tandem.addSkills(scope))}
                title="Choose a folder with a SKILL.md (or a folder of them) and install it for Claude and Codex"
              >
                Add Skill for Both…
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
