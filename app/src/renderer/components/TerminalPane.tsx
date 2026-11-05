import { FitAddon } from '@xterm/addon-fit'
import { WebglAddon } from '@xterm/addon-webgl'
import { Terminal } from '@xterm/xterm'
import { memo, useEffect, useRef } from 'react'
import { AGENT_COLORS, AGENT_NAMES, type Agent } from '../../shared/protocol'
import { MONO_FONT, onAppearanceChange, prefersDark, shortcut, terminalTheme } from '../platform'
import { Icon } from './Icon'
import { noteBell, noteInput } from '../state/paneActivity'
import { attach } from '../state/paneData'
import type { PaneStatus } from '../state/selectors'

export interface PaneHeader {
  status: PaneStatus
  title: string | null
  /** The pane's current session and how many files it has touched, for the timeline chip. */
  sessionId: string | null
  files: number
  currentFile: { path: string; kind: 'read' | 'edit' } | null
  overlap: { path: string; others: string } | null
}

interface Props {
  paneId: string
  agent: Agent
  exited: boolean
  header: PaneHeader
  onFocus: (paneId: string) => void
  onClose: (paneId: string) => void
  onRestart: (paneId: string) => void
  onTimeline: (sessionId: string) => void
}

// Terminals register here so a notification click can put the cursor in its pane.
const focusers = new Map<string, () => void>()

export function focusPane(paneId: string): boolean {
  const f = focusers.get(paneId)
  f?.()
  return f !== undefined
}

const STATUS_TEXT: Record<PaneStatus, string> = {
  working: 'working',
  waiting: 'needs you',
  idle: 'idle',
  exited: 'exited',
  starting: 'starting',
}

export const TerminalPane = memo(function TerminalPane({
  paneId,
  agent,
  exited,
  header,
  onFocus,
  onClose,
  onRestart,
  onTimeline,
}: Props): JSX.Element {
  const host = useRef<HTMLDivElement>(null)
  const exitedRef = useRef(exited)
  exitedRef.current = exited
  const restartRef = useRef(onRestart)
  restartRef.current = onRestart

  useEffect(() => {
    const el = host.current
    if (!el) return
    const term = new Terminal({
      fontFamily: MONO_FONT,
      fontSize: 12,
      lineHeight: 1.15,
      cursorBlink: true,
      scrollback: 10_000,
      macOptionIsMeta: true,
      allowProposedApi: true,
      theme: terminalTheme(prefersDark(), AGENT_COLORS[agent]),
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(el)
    try {
      const gl = new WebglAddon()
      gl.onContextLoss(() => gl.dispose())
      term.loadAddon(gl)
    } catch {
      // Canvas renderer fallback.
    }
    const resize = (): void => {
      try {
        fit.fit()
        window.tandem.resize(paneId, term.cols, term.rows)
      } catch {
        // Hidden or zero-size host.
      }
    }
    resize()
    const detach = attach(paneId, (data) => term.write(data))
    const input = term.onData((data) => {
      if (exitedRef.current) {
        if (data === '\r') restartRef.current(paneId)
        return
      }
      noteInput(paneId, Date.now())
      window.tandem.write(paneId, data)
    })
    const offAppearance = onAppearanceChange((dark) => {
      term.options.theme = terminalTheme(dark, AGENT_COLORS[agent])
    })
    const bell = term.onBell(() => noteBell(paneId, Date.now()))
    let frame = 0
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(resize)
    })
    observer.observe(el)
    const focus = (): void => onFocus(paneId)
    term.textarea?.addEventListener('focus', focus)
    focusers.set(paneId, () => term.focus())
    term.focus()
    return () => {
      focusers.delete(paneId)
      observer.disconnect()
      cancelAnimationFrame(frame)
      term.textarea?.removeEventListener('focus', focus)
      offAppearance()
      input.dispose()
      bell.dispose()
      detach()
      term.dispose()
    }
    // The terminal lives for the pane's lifetime; header props re-render around it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paneId, agent])

  const color = AGENT_COLORS[agent]
  const { status, title, currentFile, overlap, sessionId, files } = header
  return (
    <div className={`pane ${overlap ? 'pane-overlap' : ''}`} style={{ ['--agent' as string]: color }}>
      <div className="pane-header">
        <span className={`dot ${status}`} title={STATUS_TEXT[status]} />
        <span className="pane-agent">{AGENT_NAMES[agent]}</span>
        <span className={`pane-status ${status}`}>{STATUS_TEXT[status]}</span>
        <span className="pane-title">{title ?? ''}</span>
        {currentFile && !overlap && (
          <span className="pane-file">
            {currentFile.kind === 'edit' ? '✎' : '◌'} {currentFile.path}
          </span>
        )}
        {overlap && (
          <span className="pane-file overlap-text">
            {overlap.others} also editing {overlap.path}
          </span>
        )}
        {sessionId && files > 0 && (
          <button
            className={`pane-files ${currentFile || overlap ? '' : 'push-right'}`}
            title="Show the files this session read and edited"
            onClick={() => onTimeline(sessionId)}
          >
            <Icon name="list" size={12} />
            {files} {files === 1 ? 'file' : 'files'}
          </button>
        )}
        <button className="pane-close" title={`Close pane (${shortcut('W')})`} onClick={() => onClose(paneId)}>
          <Icon name="xmark" size={12} />
        </button>
      </div>
      <div className="pane-body" ref={host} />
      {exited && (
        <div className="pane-exited" onClick={() => onRestart(paneId)}>
          <span>Exited · press Return to restart</span>
        </div>
      )}
    </div>
  )
})
