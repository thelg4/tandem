import { memo, useEffect, useRef, useState } from 'react'
import { AGENT_COLORS, type AgentUsage, type LimitWindow, type UsageSummary } from '../../shared/protocol'
import { compact, relativeTime, untilTime } from '../format'

function windowName(w: LimitWindow): string {
  const m = w.window_minutes
  if (m === 10080) return 'Weekly limit'
  if (m && m % 60 === 0) return `${m / 60}-hour limit`
  return m ? `${m}-minute limit` : 'Limit'
}

/** A window whose reset time has passed since the reading no longer says anything current. */
function isStale(w: LimitWindow, now: number): boolean {
  return w.resets_at !== null && Date.parse(w.resets_at) <= now
}

function Meter({ w, now }: { w: LimitWindow; now: number }): JSX.Element {
  const stale = isStale(w, now)
  const pct = stale ? 0 : Math.min(100, Math.max(0, w.used_percent))
  const level = pct >= 90 ? 'high' : pct >= 70 ? 'mid' : ''
  return (
    <div className="limit">
      <div className="limit-row">
        <span>{windowName(w)}</span>
        <span className="muted">{stale ? 'reset since last reading' : `${Math.round(pct)}% used`}</span>
      </div>
      <div className={`meter ${level}`}>
        <span style={{ width: `${pct}%` }} />
      </div>
      {!stale && w.resets_at && <div className="limit-reset muted">Resets {untilTime(w.resets_at, now)}</div>}
    </div>
  )
}

function Tokens({ u }: { u: AgentUsage }): JSX.Element {
  return (
    <dl className="tokens">
      <dt>Today</dt>
      <dd>
        <b>{compact(u.today.output)}</b> output · {compact(u.today.input)} input
      </dd>
      <dt>Last 7 days</dt>
      <dd>
        <b>{compact(u.week.output)}</b> output · {compact(u.week.input)} input
      </dd>
    </dl>
  )
}

/** The toolbar's usage readout: Claude output today and the tightest Codex limit. */
export const Usage = memo(function Usage(): JSX.Element | null {
  const [usage, setUsage] = useState<UsageSummary | null>(null)
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    void window.tandem.usage().then((u) => u && setUsage(u))
    return window.tandem.onUsage(setUsage)
  }, [])

  useEffect(() => {
    if (!open) return
    void window.tandem.usage().then((u) => u && setUsage(u))
    const close = (e: MouseEvent): void => {
      if (!box.current?.contains(e.target as Node)) setOpen(false)
    }
    const esc = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('mousedown', close)
    window.addEventListener('keydown', esc)
    return () => {
      window.removeEventListener('mousedown', close)
      window.removeEventListener('keydown', esc)
    }
  }, [open])

  if (!usage) return null
  const now = Date.now()
  const limits = usage.codex.limits
  const windows = [limits?.primary, limits?.secondary].filter((w): w is LimitWindow => !!w && !isStale(w, now))
  const tightest = windows.sort((a, b) => b.used_percent - a.used_percent)[0]

  return (
    <div className="usage" ref={box}>
      <button className={`tool ${open ? 'on' : ''}`} onClick={() => setOpen((o) => !o)} title="Usage">
        <span className="usage-chip">
          <span className="agent-dot" style={{ ['--agent' as string]: AGENT_COLORS.claude_code }} />
          {compact(usage.claude_code.today.output)}
        </span>
        <span className="usage-chip">
          <span className="agent-dot" style={{ ['--agent' as string]: AGENT_COLORS.codex }} />
          {tightest ? `${Math.round(tightest.used_percent)}%` : compact(usage.codex.today.output)}
        </span>
      </button>
      {open && (
        <div className="popover usage-pop" role="dialog" aria-label="Usage">
          <section>
            <h3>
              <span className="agent-dot" style={{ ['--agent' as string]: AGENT_COLORS.claude_code }} />
              Claude Code
            </h3>
            <Tokens u={usage.claude_code} />
            <p className="note">Claude Code’s logs don’t record plan limits, so only tokens are shown.</p>
          </section>
          <section>
            <h3>
              <span className="agent-dot" style={{ ['--agent' as string]: AGENT_COLORS.codex }} />
              Codex
              {limits?.plan && <span className="badge">{limits.plan}</span>}
            </h3>
            {limits?.primary && <Meter w={limits.primary} now={now} />}
            {limits?.secondary && <Meter w={limits.secondary} now={now} />}
            <Tokens u={usage.codex} />
            <p className="note">
              {limits
                ? `Limits as of ${relativeTime(limits.at, now).toLowerCase()}, from the last Codex session on this computer.`
                : 'No Codex sessions in the last 7 days.'}
            </p>
          </section>
          <footer className="muted">All projects, from local logs · updated {relativeTime(usage.at, now).toLowerCase()}</footer>
        </div>
      )}
    </div>
  )
})
