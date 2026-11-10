import type { PaneStatus } from './selectors'

/** An agent must have been busy this long before "finished" is worth a notification. */
export const FINISH_MIN_MS = 20_000
/** The waiting heuristic can flicker; one "needs you" per pane in this window. */
export const WAITING_REPEAT_MS = 15_000

export interface PaneMemory {
  status: PaneStatus
  /** When the pane last went from idle to busy (working or waiting), or null when idle. */
  busySince: number | null
  waitingNoticeAt: number
}

export interface Notice {
  paneId: string
  reason: 'waiting' | 'finished'
}

const BUSY: ReadonlySet<PaneStatus> = new Set(['working', 'waiting'])

/**
 * Compare each pane's status with what it was on the last tick and decide what to tell
 * the user: a pane that starts waiting on them, or one that went quiet after real work.
 * A pane seen for the first time only gets remembered, so a reload notifies nothing.
 */
export function paneNotices(
  memory: Record<string, PaneMemory>,
  statuses: Record<string, PaneStatus>,
  now: number,
): { memory: Record<string, PaneMemory>; notices: Notice[] } {
  const next: Record<string, PaneMemory> = {}
  const notices: Notice[] = []
  for (const [paneId, status] of Object.entries(statuses)) {
    const prev = memory[paneId]
    if (!prev) {
      next[paneId] = { status, busySince: BUSY.has(status) ? now : null, waitingNoticeAt: -Infinity }
      continue
    }
    let { busySince, waitingNoticeAt } = prev
    if (status === 'waiting' && prev.status !== 'waiting' && now - waitingNoticeAt >= WAITING_REPEAT_MS) {
      notices.push({ paneId, reason: 'waiting' })
      waitingNoticeAt = now
    }
    if (BUSY.has(status)) {
      busySince ??= now
    } else {
      if (status === 'idle' && busySince !== null && now - busySince >= FINISH_MIN_MS) {
        notices.push({ paneId, reason: 'finished' })
      }
      busySince = null
    }
    next[paneId] = { status, busySince, waitingNoticeAt }
  }
  return { memory: next, notices }
}
