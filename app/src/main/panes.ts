import { randomUUID } from 'node:crypto'
import * as pty from 'node-pty'
import type { Agent, PaneCommand } from '../shared/protocol'
import { agentCommand, loginShell, paneEnv } from './agents'

interface Pane {
  agent: Agent
  /** The agent session the pane resumed, kept so a restart resumes it again. */
  resume?: string
  proc: pty.IPty
  lastInputSent: number
  started: PaneCommand
}

export interface PaneSink {
  data(paneId: string, data: string): void
  exit(paneId: string, code: number): void
  command(cmd: PaneCommand): void
}

/** One pty per pane; the pty process is the agent itself (`exec` in a login shell). */
export class PaneManager {
  private panes = new Map<string, Pane>()
  private counter = 0

  constructor(
    private readonly repo: string,
    private readonly sink: PaneSink,
  ) {}

  /** A new pane; with ``resume`` (the agent's own session id) it continues that session. */
  create(agent: Agent, resume?: string): string {
    const paneId = `p${++this.counter}`
    this.spawn(paneId, agent, resume)
    return paneId
  }

  restart(paneId: string): void {
    const pane = this.panes.get(paneId)
    if (pane) this.spawn(paneId, pane.agent, pane.resume)
  }

  write(paneId: string, data: string): void {
    const pane = this.panes.get(paneId)
    if (!pane) return
    pane.proc.write(data)
    const now = Date.now()
    if (now - pane.lastInputSent >= 1000) {
      pane.lastInputSent = now
      this.sink.command({ type: 'pane_input', pane_id: paneId, at: new Date(now).toISOString() })
    }
  }

  resize(paneId: string, cols: number, rows: number): void {
    if (cols > 0 && rows > 0) this.panes.get(paneId)?.proc.resize(cols, rows)
  }

  kill(paneId: string): void {
    const pane = this.panes.get(paneId)
    this.panes.delete(paneId)
    pane?.proc.kill('SIGHUP')
  }

  /** ``pane_started`` for every running pane, for a watcher that restarted. */
  livePanes(): PaneCommand[] {
    return [...this.panes.values()].map((p) => p.started)
  }

  killAll(): void {
    for (const id of [...this.panes.keys()]) this.kill(id)
  }

  private spawn(paneId: string, agent: Agent, resume?: string): void {
    const claudeSessionId = agent === 'claude_code' ? (resume ?? randomUUID()) : undefined
    const env = paneEnv(process.env)
    const proc = pty.spawn(loginShell(), ['-l', '-c', `exec ${agentCommand(agent, claudeSessionId, resume)}`], {
      name: 'xterm-256color',
      cols: 100,
      rows: 30,
      cwd: this.repo,
      env,
    })
    const started: PaneCommand = {
      type: 'pane_started',
      pane_id: paneId,
      agent,
      pid: proc.pid,
      ...(claudeSessionId ? { claude_session_id: claudeSessionId } : {}),
      ...(agent === 'codex' && resume ? { codex_session_id: resume } : {}),
      spawned_at: new Date().toISOString(),
    }
    this.panes.set(paneId, { agent, resume, proc, lastInputSent: 0, started })
    proc.onData((data) => this.sink.data(paneId, data))
    proc.onExit(({ exitCode }) => {
      // A restart replaces the pty; only report the exit of the current one.
      if (this.panes.get(paneId)?.proc === proc) {
        this.sink.exit(paneId, exitCode)
      }
      this.sink.command({ type: 'pane_exited', pane_id: paneId, at: new Date().toISOString() })
    })
    this.sink.command(started)
  }
}
