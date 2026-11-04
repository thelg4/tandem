// Pane output arrives as soon as the pty spawns, often before its terminal mounts.
// One listener buffers per pane until a terminal attaches, then writes straight to it.

import { noteOutput } from './paneActivity'

type Writer = (data: string) => void

/** Output a pane may hold before its terminal mounts; older chunks are dropped first. */
export const PENDING_CAP = 1 << 20

interface Pending {
  chunks: string[]
  size: number
}

export class PaneOutput {
  private readonly writers = new Map<string, Writer>()
  private readonly pending = new Map<string, Pending>()
  // Pane ids are never reused, so a closed id can be remembered to drop late output.
  private readonly closed = new Set<string>()

  constructor(private readonly cap: number = PENDING_CAP) {}

  /** False when the pane is closed and its output was dropped. */
  data(paneId: string, data: string): boolean {
    if (this.closed.has(paneId)) return false
    const write = this.writers.get(paneId)
    if (write) {
      write(data)
      return true
    }
    let p = this.pending.get(paneId)
    if (!p) {
      p = { chunks: [], size: 0 }
      this.pending.set(paneId, p)
    }
    p.chunks.push(data)
    p.size += data.length
    let drop = 0
    while (p.size > this.cap && drop < p.chunks.length - 1) p.size -= p.chunks[drop++]!.length
    if (drop) p.chunks.splice(0, drop)
    return true
  }

  attach(paneId: string, write: Writer): () => void {
    this.writers.set(paneId, write)
    const p = this.pending.get(paneId)
    this.pending.delete(paneId)
    if (p) write(p.chunks.join(''))
    return () => {
      if (this.writers.get(paneId) === write) this.writers.delete(paneId)
    }
  }

  forget(paneId: string): void {
    this.closed.add(paneId)
    this.writers.delete(paneId)
    this.pending.delete(paneId)
  }

  buffered(paneId: string): number {
    return this.pending.get(paneId)?.size ?? 0
  }
}

const output = new PaneOutput()
let installed = false

export function installPaneDataListener(): void {
  if (installed) return
  installed = true
  window.tandem.onPaneData((paneId, data) => {
    if (output.data(paneId, data)) noteOutput(paneId, Date.now())
  })
}

export function attach(paneId: string, write: Writer): () => void {
  return output.attach(paneId, write)
}

export function forgetPaneData(paneId: string): void {
  output.forget(paneId)
}
