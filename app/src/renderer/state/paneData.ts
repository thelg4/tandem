// Pane output arrives as soon as the pty spawns, often before its terminal mounts.
// One listener buffers per pane until a terminal attaches, then writes straight to it.

import { noteOutput } from './paneActivity'

type Writer = (data: string) => void

const writers = new Map<string, Writer>()
const pending = new Map<string, string[]>()
let installed = false

export function installPaneDataListener(): void {
  if (installed) return
  installed = true
  window.tandem.onPaneData((paneId, data) => {
    noteOutput(paneId, Date.now())
    const write = writers.get(paneId)
    if (write) write(data)
    else pending.set(paneId, [...(pending.get(paneId) ?? []), data])
  })
}

export function attach(paneId: string, write: Writer): () => void {
  writers.set(paneId, write)
  for (const chunk of pending.get(paneId) ?? []) write(chunk)
  pending.delete(paneId)
  return () => {
    if (writers.get(paneId) === write) writers.delete(paneId)
  }
}
