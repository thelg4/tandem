import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join, resolve } from 'node:path'
import type { PaneCommand, WatcherMsg } from '../shared/protocol'

/** Runs `python -m tandem watch <repo>` and relays its NDJSON stream. */
export class WatcherBridge {
  private proc: ChildProcessWithoutNullStreams | null = null
  private buffer = ''
  private restarts = 0
  private stopped = false

  constructor(
    private readonly repo: string,
    private readonly appRoot: string,
    private readonly onMessage: (msg: WatcherMsg) => void,
  ) {}

  start(): void {
    const python = process.env.TANDEM_PYTHON || 'python3'
    const src = resolve(this.appRoot, '..', 'src')
    const env: NodeJS.ProcessEnv = { ...process.env, PYTHONUNBUFFERED: '1' }
    if (existsSync(join(src, 'tandem'))) {
      env.PYTHONPATH = env.PYTHONPATH ? `${src}:${env.PYTHONPATH}` : src
    }
    const proc = spawn(python, ['-m', 'tandem', 'watch', this.repo], { env, stdio: 'pipe' })
    this.proc = proc
    proc.stdout.setEncoding('utf8')
    proc.stdout.on('data', (chunk: string) => this.onData(chunk))
    proc.stderr.setEncoding('utf8')
    proc.stderr.on('data', (chunk: string) => console.error(`[tandem watch] ${chunk.trimEnd()}`))
    proc.on('error', (err) => this.diag('warn', 'watcher_spawn_failed', `${python}: ${err.message}`))
    proc.on('exit', (code) => {
      this.proc = null
      if (this.stopped) return
      if (this.restarts++ < 1) {
        setTimeout(() => this.start(), 2000)
      } else {
        this.diag('warn', 'watcher_exited', `tandem watch exited (code ${code}); live view paused`)
      }
    })
  }

  send(cmd: PaneCommand): void {
    if (this.proc?.stdin.writable) this.proc.stdin.write(JSON.stringify(cmd) + '\n')
  }

  stop(): void {
    this.stopped = true
    this.proc?.stdin.end()
    this.proc?.kill()
  }

  private onData(chunk: string): void {
    this.buffer += chunk
    let nl: number
    while ((nl = this.buffer.indexOf('\n')) >= 0) {
      const line = this.buffer.slice(0, nl)
      this.buffer = this.buffer.slice(nl + 1)
      if (!line.trim()) continue
      try {
        this.onMessage(JSON.parse(line) as WatcherMsg)
      } catch {
        console.error('[tandem watch] unparseable line')
      }
    }
  }

  private diag(level: 'info' | 'warn', code: string, message: string): void {
    this.onMessage({ v: 1, type: 'diagnostic', level, code, message })
  }
}
