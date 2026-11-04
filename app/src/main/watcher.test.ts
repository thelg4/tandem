import { chmodSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import type { PaneCommand, WatcherMsg } from '../shared/protocol'
import { WatcherBridge } from './watcher'

// Stands in for `python -m tandem watch`: says hello, then echoes each command back.
function fakeWatcher(): string {
  const dir = mkdtempSync(join(tmpdir(), 'tandem-watch-'))
  const script = join(dir, 'fake-python')
  writeFileSync(script, '#!/bin/sh\necho \'{"type":"hello"}\'\nexec cat\n')
  chmodSync(script, 0o755)
  return script
}

const started = (id: string): PaneCommand => ({
  type: 'pane_started',
  pane_id: id,
  agent: 'codex',
  pid: 1,
  spawned_at: '2026-11-04T00:00:00.000Z',
})

async function until(cond: () => boolean): Promise<void> {
  for (let i = 0; i < 200 && !cond(); i++) await new Promise((r) => setTimeout(r, 10))
  expect(cond()).toBe(true)
}

let bridge: WatcherBridge | null = null
afterEach(() => bridge?.stop())

describe('WatcherBridge', () => {
  it('queues commands until the watcher is running', async () => {
    process.env.TANDEM_PYTHON = fakeWatcher()
    const got: WatcherMsg[] = []
    bridge = new WatcherBridge('/repo', '/nowhere', (m) => got.push(m), () => [], 10)
    bridge.send(started('p1'))
    bridge.start()
    await until(() => got.some((m) => (m as unknown as PaneCommand).type === 'pane_started'))
  })

  it('replays live panes after the watcher restarts', async () => {
    process.env.TANDEM_PYTHON = fakeWatcher()
    const got: unknown[] = []
    const live = [started('p7')]
    bridge = new WatcherBridge('/repo', '/nowhere', (m) => got.push(m), () => live, 10)
    bridge.start()
    await until(() => got.length === 1)
    bridge.crashForTest()
    await until(() => got.some((m) => (m as PaneCommand).type === 'pane_started'))
  })

  it('reset starts a fresh watcher that says hello again', async () => {
    process.env.TANDEM_PYTHON = fakeWatcher()
    const got: unknown[] = []
    bridge = new WatcherBridge('/repo', '/nowhere', (m) => got.push(m), () => [], 10)
    bridge.start()
    await until(() => got.length === 1)
    bridge.reset()
    await until(() => got.filter((m) => (m as { type: string }).type === 'hello').length === 2)
  })
})
