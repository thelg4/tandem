import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join, resolve } from 'node:path'
import type { PaneCommand, WatcherMsg } from '../shared/protocol'
import { resolvePython } from './agents'

const MAX_QUEUED = 1000

/**
 * Runs `python -m tandem watch <repo>` and relays its NDJSON stream.
 *
 * Commands sent while the watcher is down are queued. After a crash restart the queue
 * is replaced by ``livePanes()``: the pane manager is the source of truth, and a fresh
 * watcher must hear about every pane still running or it would treat them as external.
 */
export class WatcherBridge {
  private proc: ChildProcessWithoutNullStreams | null = null
  private buffer = ''
  private restarts = 0
  private stopped = false
  private queue: PaneCommand[] = []

  constructor(
    private readonly repo: string,
    private readonly appRoot: string,
    private readonly onMessage: (msg: WatcherMsg) => void,
    private readonly livePanes: () => PaneCommand[],
    private readonly restartDelayMs = 2000,
  ) {}

  start(): void {
    this.stopped = false
    this.spawnProcess()
  }

  /** Throw away the current watcher and start a new one (fresh hello, tree, sessions). */
  reset(): void {
    const old = this.proc
    this.proc = null
    this.buffer = ''
    this.queue = []
    if (old) {
      old.removeAllListeners('exit')
      old.stdin.end()
      old.kill()
    }
    this.spawnProcess()
  }

  send(cmd: PaneCommand): void {
    if (this.proc?.stdin.writable) this.proc.stdin.write(JSON.stringify(cmd) + '\n')
    else if (this.queue.length < MAX_QUEUED) this.queue.push(cmd)
  }

  stop(): void {
    this.stopped = true
    this.proc?.stdin.end()
    this.proc?.kill()
  }

  /** Test hook: simulate the watcher process dying. */
  crashForTest(): void {
    this.proc?.kill('SIGKILL')
  }

  /** The checkout's `src` in development; the copy bundled in Resources in a packaged app. */
  private pythonSource(): string | null {
    const candidates = [resolve(this.appRoot, '..', 'src')]
    if (process.resourcesPath) candidates.push(join(process.resourcesPath, 'python'))
    return candidates.find((dir) => existsSync(join(dir, 'tandem'))) ?? null
  }

  private spawnProcess(): void {
    const python = resolvePython()
    const env: NodeJS.ProcessEnv = { ...process.env, PYTHONUNBUFFERED: '1' }
    const src = this.pythonSource()
    if (src) env.PYTHONPATH = env.PYTHONPATH ? `${src}:${env.PYTHONPATH}` : src
    const proc = spawn(python, ['-m', 'tandem', 'watch', this.repo], { env, stdio: 'pipe' })
    this.proc = proc
    proc.stdout.setEncoding('utf8')
    proc.stdout.on('data', (chunk: string) => this.onData(chunk))
    proc.stderr.setEncoding('utf8')
    proc.stderr.on('data', (chunk: string) => console.error(`[tandem watch] ${chunk.trimEnd()}`))
    proc.on('error', (err) => this.diag('warn', 'watcher_spawn_failed', `${python}: ${err.message}`))
    proc.on('exit', (code) => {
      if (this.proc === proc) this.proc = null
      if (this.stopped) return
      if (this.restarts++ < 1) {
        setTimeout(() => {
          this.queue = this.livePanes()
          this.spawnProcess()
        }, this.restartDelayMs)
      } else {
        this.diag('warn', 'watcher_exited', `tandem watch exited (code ${code}); live view paused`)
      }
    })
    const pending = this.queue
    this.queue = []
    for (const cmd of pending) proc.stdin.write(JSON.stringify(cmd) + '\n')
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
