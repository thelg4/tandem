import type { Agent, Clash, ClashLabel, ScanStatus } from '../shared/protocol'

/** Wait this long after the last edit before scanning, so a burst of edits scans once. */
export const SCAN_DEBOUNCE_MS = 15_000
/** Never start scans closer together than this. */
export const SCAN_MIN_GAP_MS = 20_000
/** Clashes whose later edit is older than this are history, not news. */
export const CLASH_RECENT_MS = 60 * 60_000

const LABELS: ReadonlySet<string> = new Set<ClashLabel>(['exact_revert', 'partial_revert', 'rewrite', 'removal'])

interface ReportSide {
  agent?: unknown
  session?: unknown
  time?: unknown
}

interface ReportInteraction {
  interaction_id?: unknown
  path?: unknown
  label?: unknown
  lines_affected?: unknown
  start_line?: unknown
  evidence_class?: unknown
  original?: ReportSide
  later?: ReportSide
}

function side(s: ReportSide | undefined): Clash['original'] | null {
  if (!s || (s.agent !== 'claude_code' && s.agent !== 'codex') || typeof s.session !== 'string') return null
  return { agent: s.agent as Agent, session: s.session, time: typeof s.time === 'string' ? s.time : null }
}

/**
 * Cross-agent interactions from `tandem report --format json` worth telling the user
 * about: one agent reverted, rewrote or removed lines the other wrote, recently, on
 * verified or supported evidence. Overlap candidates (order unknown) are left out.
 */
export function clashesFromReport(report: unknown, now: number): Clash[] {
  const list = (report as { interactions?: unknown } | null)?.interactions
  if (!Array.isArray(list)) return []
  const out: Clash[] = []
  for (const raw of list as ReportInteraction[]) {
    const original = side(raw.original)
    const later = side(raw.later)
    if (!original || !later || original.agent === later.agent) continue
    if (typeof raw.label !== 'string' || !LABELS.has(raw.label)) continue
    if (raw.evidence_class !== 'verified' && raw.evidence_class !== 'supported') continue
    if (typeof raw.interaction_id !== 'string' || typeof raw.path !== 'string') continue
    const at = later.time ? Date.parse(later.time) : NaN
    if (!(now - at <= CLASH_RECENT_MS)) continue
    out.push({
      id: raw.interaction_id,
      path: raw.path,
      label: raw.label as ClashLabel,
      lines: typeof raw.lines_affected === 'number' ? raw.lines_affected : 0,
      startLine: typeof raw.start_line === 'number' ? raw.start_line : 0,
      evidence: raw.evidence_class,
      original,
      later,
    })
  }
  return out.sort((a, b) => Date.parse(b.later.time ?? '') - Date.parse(a.later.time ?? ''))
}

export interface ScanDeps {
  /** Run one scan; resolves true on success. */
  scan: () => Promise<boolean>
  now: () => number
  setTimer: (fn: () => void, ms: number) => unknown
  clearTimer: (handle: unknown) => void
  onStatus: (status: ScanStatus) => void
  /** Called after each successful scan. */
  onScanned: () => void
}

/**
 * Keeps the scan database close to live: one scan when a project opens, then one shortly
 * after agents stop editing. At most one scan runs at a time; a request during a scan
 * runs once more when it finishes.
 */
export class ScanScheduler {
  private timer: unknown = null
  private due = 0
  private running = false
  private again = false
  private lastStart = -Infinity
  private stopped = false
  private status: ScanStatus = { state: 'never', at: null }

  constructor(private readonly deps: ScanDeps) {}

  current(): ScanStatus {
    return this.status
  }

  /** Scan after ``delayMs`` (pushed back by later requests), respecting the minimum gap. */
  request(delayMs = SCAN_DEBOUNCE_MS): void {
    if (this.stopped) return
    if (this.running) {
      this.again = true
      return
    }
    const now = this.deps.now()
    const at = Math.max(now + delayMs, this.lastStart + SCAN_MIN_GAP_MS)
    if (this.timer !== null) this.deps.clearTimer(this.timer)
    this.due = at
    this.timer = this.deps.setTimer(() => void this.fire(), Math.max(0, at - now))
  }

  stop(): void {
    this.stopped = true
    if (this.timer !== null) this.deps.clearTimer(this.timer)
    this.timer = null
  }

  /** Test hook: when the pending scan is due, or 0 when none is. */
  pendingAt(): number {
    return this.timer === null ? 0 : this.due
  }

  private setStatus(status: ScanStatus): void {
    this.status = status
    this.deps.onStatus(status)
  }

  private async fire(): Promise<void> {
    this.timer = null
    if (this.stopped) return
    this.running = true
    this.lastStart = this.deps.now()
    this.setStatus({ state: 'scanning', at: this.status.at })
    let ok = false
    try {
      ok = await this.deps.scan()
    } catch {
      ok = false
    }
    this.running = false
    if (this.stopped) return
    if (ok) {
      this.setStatus({ state: 'ok', at: new Date(this.deps.now()).toISOString() })
      this.deps.onScanned()
    } else {
      this.setStatus({ state: 'failed', at: this.status.at })
    }
    if (this.again) {
      this.again = false
      this.request()
    }
  }
}
