import { randomUUID } from 'node:crypto'
import * as pty from 'node-pty'
import type { Agent, PaneCommand } from '../shared/protocol'
import { agentCommand, loginShell } from './agents'

interface Pane {
  agent: Agent
  proc: pty.IPty
  lastInputSent: number
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

  create(agent: Agent): string {
    const paneId = `p${++this.counter}`
    this.spawn(paneId, agent)
    return paneId
  }

  restart(paneId: string): void {
    const pane = this.panes.get(paneId)
    if (pane) this.spawn(paneId, pane.agent)
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

  killAll(): void {
    for (const id of [...this.panes.keys()]) this.kill(id)
  }

  private spawn(paneId: string, agent: Agent): void {
    const claudeSessionId = agent === 'claude_code' ? randomUUID() : undefined
    const env = { ...process.env, TERM: 'xterm-256color', COLORTERM: 'truecolor' } as Record<
      string,
      string
    >
    const proc = pty.spawn(loginShell(), ['-l', '-c', `exec ${agentCommand(agent, claudeSessionId)}`], {
      name: 'xterm-256color',
      cols: 100,
      rows: 30,
      cwd: this.repo,
      env,
    })
    this.panes.set(paneId, { agent, proc, lastInputSent: 0 })
    proc.onData((data) => this.sink.data(paneId, data))
    proc.onExit(({ exitCode }) => {
      // A restart replaces the pty; only report the exit of the current one.
      if (this.panes.get(paneId)?.proc === proc) {
        this.sink.exit(paneId, exitCode)
      }
      this.sink.command({ type: 'pane_exited', pane_id: paneId, at: new Date().toISOString() })
    })
    this.sink.command({
      type: 'pane_started',
      pane_id: paneId,
      agent,
      pid: proc.pid,
      ...(claudeSessionId ? { claude_session_id: claudeSessionId } : {}),
      spawned_at: new Date().toISOString(),
    })
  }
}
