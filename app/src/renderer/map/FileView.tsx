import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AGENT_COLORS,
  AGENT_NAMES,
  type BlameSpan,
  type FileBlame,
  type FileContents,
  type FileEdit,
  type ScanStatus,
} from '../../shared/protocol'
import { Icon } from '../components/Icon'
import { relativeTime } from '../format'
import { highlightLines } from '../highlight'
import type { AppState } from '../state/store'

interface Props {
  state: AppState
  path: string
  now: number
  scan: ScanStatus
  onBack: () => void
}

/** An edit to bring into view: the one from this session closest to this time. */
interface EditFocus {
  session: string
  at: string
}

/** The edit in ``edits`` that ``focus`` points at, within 10 s; else -1. */
export function findEdit(edits: FileEdit[], focus: EditFocus): number {
  const t = Date.parse(focus.at)
  let best = -1
  let gap = 10_000
  edits.forEach((e, i) => {
    const d = Math.abs(Date.parse(e.at) - t)
    if (e.session_id === focus.session && d <= gap) {
      best = i
      gap = d
    }
  })
  return best
}

/** When anything last happened to this file, so open views reload after a new edit. */
function lastTouch(state: AppState, path: string): number {
  let at = state.changes[path]?.at ?? 0
  for (const t of Object.values(state.files[path] ?? {})) at = Math.max(at, t.edit ?? 0)
  return at
}

function sizeText(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`
}

const CHANGE_TEXT: Record<FileEdit['change'], string | null> = { update: null, create: 'Created', delete: 'Deleted' }

function Diff({ edit }: { edit: FileEdit }): JSX.Element {
  return (
    <div className="diff">
      {edit.hunks.map((h, i) => {
        let oldLn = h.old_start ?? 0
        let newLn = h.new_start ?? 0
        const numbered = h.new_start !== null
        return (
          <div className="hunk" key={i}>
            <div className="hunk-head">
              {numbered ? (h.old_start === 0 ? 'New file' : `Line ${h.new_start}`) : 'Patch (no line numbers in this Codex version)'}
            </div>
            {h.lines.map((line, j) => {
              const sign = line[0]
              const kind = sign === '+' ? 'add' : sign === '-' ? 'del' : 'ctx'
              const o = kind === 'add' ? '' : numbered ? String(oldLn++) : ''
              const n = kind === 'del' ? '' : numbered ? String(newLn++) : ''
              return (
                <div className={`dl ${kind}`} key={j}>
                  <span className="ln">{o}</span>
                  <span className="ln">{n}</span>
                  <span className="sign">{sign === ' ' ? '' : sign}</span>
                  <span className="code">{line.slice(1) || ' '}</span>
                </div>
              )
            })}
          </div>
        )
      })}
      {edit.truncated && <div className="hunk-head">Diff shortened; the first 400 lines are shown.</div>}
    </div>
  )
}

function EditCard({ edit, now, focused }: { edit: FileEdit; now: number; focused: boolean }): JSX.Element {
  const ref = useRef<HTMLElement>(null)
  useEffect(() => {
    if (focused) ref.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }, [focused])
  const added = edit.hunks.reduce((n, h) => n + h.lines.filter((l) => l[0] === '+').length, 0)
  const removed = edit.hunks.reduce((n, h) => n + h.lines.filter((l) => l[0] === '-').length, 0)
  const change = CHANGE_TEXT[edit.change]
  return (
    <article ref={ref} className={`edit ${focused ? 'focused' : ''}`} style={{ ['--agent' as string]: AGENT_COLORS[edit.agent] }}>
      <header className="edit-head">
        <span className="agent-dot" />
        <span className="edit-agent">{AGENT_NAMES[edit.agent]}</span>
        {edit.title && <span className="edit-title">{edit.title}</span>}
        <span className="spacer" />
        {change && <span className="pill grey">{change}</span>}
        <span className="edit-stat">
          <span className="plus">+{added}</span> <span className="minus">−{removed}</span>
        </span>
        <span className="edit-time" title={new Date(edit.at).toLocaleString()}>
          {relativeTime(edit.at, now)}
        </span>
      </header>
      {edit.user_modified && <p className="edit-note">You changed this edit while approving it.</p>}
      {edit.change === 'delete' ? <p className="edit-note">The file was deleted.</p> : <Diff edit={edit} />}
    </article>
  )
}

function Edits({ path, touch, now, focus }: { path: string; touch: number; now: number; focus: EditFocus | null }): JSX.Element {
  const [edits, setEdits] = useState<FileEdit[] | null | undefined>(undefined)
  useEffect(() => {
    let live = true
    void window.tandem.fileEdits(path).then((e) => live && setEdits(e))
    return () => {
      live = false
    }
  }, [path, touch])
  if (edits === undefined) return <p className="view-note">Reading the agents’ logs…</p>
  if (edits === null) return <p className="view-note">Couldn’t read the agents’ logs. Tandem needs Python 3.11+ as python3 on your PATH.</p>
  if (edits.length === 0) {
    return (
      <p className="view-note">
        No edits by Claude Code or Codex in the last 7 days. Changes made with shell commands don’t appear here, because the logs don’t
        record them.
      </p>
    )
  }
  const focused = focus ? findEdit(edits, focus) : -1
  return (
    <div className="edits">
      {focus && focused < 0 && (
        <p className="view-note">That edit isn’t in the last 7 days of logs, so its diff can’t be shown here.</p>
      )}
      {edits.map((e, i) => (
        <EditCard key={`${e.session_id}-${e.at}-${i}`} edit={e} now={now} focused={i === focused} />
      ))}
      {edits.length >= 50 && <p className="view-note">Showing the 50 most recent edits.</p>}
    </div>
  )
}

const KIND_TEXT: Record<BlameSpan['kind'], string> = {
  agent: '',
  unknown: 'No agent log explains these lines: a hand edit, a shell command or another tool.',
  baseline: 'Older than the analyzed history.',
  changed: 'Changed since the last scan, by something no scanned log records yet.',
}

const EVIDENCE_TEXT: Record<string, string> = {
  verified: 'verified: rebuilt exactly from the log and matched in Git',
  supported: 'supported: a strong match, but not an exact rebuild',
  ambiguous: 'ambiguous: more than one session could have written it',
  unknown: 'unknown evidence',
}

function spanTitle(s: BlameSpan, now: number): string {
  const lines = s.count === 1 ? `Line ${s.start}` : `Lines ${s.start}–${s.start + s.count - 1}`
  if (s.kind !== 'agent' || !s.agent) return `${lines}. ${KIND_TEXT[s.kind]}`
  const when = s.at ? ` ${relativeTime(s.at, now)}` : ''
  const committed = s.commit ? `Committed in ${s.commit.slice(0, 8)}.` : 'Not committed yet.'
  return `${lines}. Written by ${AGENT_NAMES[s.agent]}${when} (${EVIDENCE_TEXT[s.evidence ?? 'unknown']}). ${committed} Click to see the edit.`
}

function spanLabel(s: BlameSpan, now: number): string {
  if (s.kind === 'agent' && s.agent) {
    const when = s.at ? relativeTime(s.at, now).replace(' ago', '') : ''
    return [AGENT_NAMES[s.agent], when, s.commit ? '' : 'new'].filter(Boolean).join(' · ')
  }
  return s.kind === 'unknown' ? 'no log' : s.kind === 'changed' ? 'changed' : ''
}

/** Share of lines per owner, for the summary above the file. */
export function blameSummary(blame: FileBlame): { label: string; pct: number; key: string }[] {
  const counts = new Map<string, number>()
  for (const s of blame.spans) {
    const key = s.kind === 'agent' && s.agent ? s.agent : s.kind === 'baseline' ? 'baseline' : 'other'
    counts.set(key, (counts.get(key) ?? 0) + s.count)
  }
  const total = Math.max(1, blame.lines)
  const names: Record<string, string> = { ...AGENT_NAMES, baseline: 'older history', other: 'no agent log' }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([key, n]) => ({ key, label: names[key] ?? key, pct: Math.round((n / total) * 100) }))
}

function BlameColumn({ blame, now, onShow }: { blame: FileBlame; now: number; onShow: (s: BlameSpan) => void }): JSX.Element {
  return (
    <div className="blame-col" aria-label="Who wrote each line">
      {blame.spans.map((s, i) => {
        const agent = s.kind === 'agent' && s.agent
        // Neighbouring edits by the same agent at the same time read as one block.
        const label = spanLabel(s, now)
        const repeat = i > 0 && spanLabel(blame.spans[i - 1]!, now) === label
        return (
          <div
            key={s.start}
            className={`blame-span k-${s.kind} ev-${s.evidence ?? 'none'} ${s.commit ? '' : 'uncommitted'} ${agent ? 'clickable' : ''} ${repeat ? 'repeat' : ''}`}
            style={{ height: `calc(${s.count} * var(--source-line))`, ['--agent' as string]: agent ? AGENT_COLORS[s.agent!] : undefined }}
            title={spanTitle(s, now)}
            onClick={agent ? () => onShow(s) : undefined}
          >
            {!repeat && <span className="blame-label">{label}</span>}
          </div>
        )
      })}
    </div>
  )
}

function BlameNote({ blame, scan, now }: { blame: FileBlame | null | undefined; scan: ScanStatus; now: number }): JSX.Element {
  if (blame === undefined) return <p className="blame-note muted">Reading blame…</p>
  if (blame === null) {
    const text =
      scan.state === 'failed'
        ? 'Blame isn’t available: the last scan failed.'
        : scan.at === null
          ? 'Blame appears after the first scan of this repository…'
          : 'Blame isn’t available for this file.'
    return <p className="blame-note muted">{text}</p>
  }
  const parts = blameSummary(blame)
  return (
    <p className="blame-note">
      {parts.map((p) => (
        <span key={p.key} className={`blame-share s-${p.key}`} style={{ ['--agent' as string]: (AGENT_COLORS as Record<string, string>)[p.key] }}>
          <span className="agent-dot" /> {p.label} {p.pct}%
        </span>
      ))}
      <span className="muted">
        {blame.note === 'no_agent_edits' ? ' · no agent edits to this file in the scanned logs' : ''}
        {blame.scanned_at ? ` · as of the scan ${relativeTime(blame.scanned_at, now)}` : ''}
        {scan.state === 'scanning' ? ' · scanning…' : ''}
      </span>
    </p>
  )
}

function Source({
  path,
  touch,
  now,
  scan,
  onShowEdit,
}: {
  path: string
  touch: number
  now: number
  scan: ScanStatus
  onShowEdit: (focus: EditFocus) => void
}): JSX.Element {
  const [file, setFile] = useState<FileContents | undefined>(undefined)
  const [blame, setBlame] = useState<FileBlame | null | undefined>(undefined)
  useEffect(() => {
    let live = true
    void window.tandem.readFile(path).then((f) => live && setFile(f))
    return () => {
      live = false
    }
  }, [path, touch])
  useEffect(() => {
    let live = true
    void window.tandem.fileBlame(path).then((b) => live && setBlame(b))
    return () => {
      live = false
    }
  }, [path, touch, scan.at])
  const lines = useMemo(() => (file?.kind === 'text' ? highlightLines(file.text, path) : null), [file, path])
  if (file === undefined) return <p className="view-note">Loading…</p>
  if (file.kind === 'missing') return <p className="view-note">This file isn’t in the working tree. It may have been deleted or renamed.</p>
  if (file.kind === 'binary') return <p className="view-note">Binary file ({sizeText(file.size)}).</p>
  if (file.kind === 'too_large') return <p className="view-note">Too large to show ({sizeText(file.size)}; the limit is 2 MB).</p>
  const count = file.text.split('\n').length - (file.text.endsWith('\n') ? 1 : 0)
  // Blame is aligned with the file as the CLI read it; a stale result is not drawn.
  const usable = blame && blame.lines === Math.max(count, 0) ? blame : null
  return (
    <>
      <BlameNote blame={blame === undefined ? undefined : usable} scan={scan} now={now} />
      <div className="source">
        <pre className="gutter" aria-hidden="true">
          {Array.from({ length: Math.max(1, count) }, (_, i) => i + 1).join('\n')}
        </pre>
        {usable && usable.spans.length > 0 && (
          <BlameColumn
            blame={usable}
            now={now}
            onShow={(s) => s.session_id && s.at && onShowEdit({ session: s.session_id, at: s.at })}
          />
        )}
        {lines ? (
          <pre className="code hljs" dangerouslySetInnerHTML={{ __html: lines.slice(0, Math.max(1, count)).join('\n') }} />
        ) : (
          <pre className="code">{file.text}</pre>
        )}
      </div>
    </>
  )
}

/** One file: every agent edit to it from the logs, and its current contents. */
export function FileView({ state, path, now, scan, onBack }: Props): JSX.Element {
  const [tab, setTab] = useState<'edits' | 'file'>('edits')
  const [focus, setFocus] = useState<EditFocus | null>(null)
  const showEdit = (f: EditFocus): void => {
    setFocus(f)
    setTab('edits')
  }
  const touch = lastTouch(state, path)
  const slash = path.lastIndexOf('/')
  const overlap = state.overlaps[path]
  return (
    <div className="file-view">
      <div className="file-head">
        <button className="tool back" onClick={onBack} aria-label="Back">
          <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
            <path d="M6.5 1.5L3 5l3.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        <Icon name="doc" size={16} />
        <div className="file-title" title={path}>
          <span className="activity-name">{path.slice(slash + 1)}</span>
          {slash > 0 && <span className="activity-dir">{path.slice(0, slash)}</span>}
        </div>
        {overlap?.level === 'active' && <span className="pill red">Both editing</span>}
        {overlap?.level === 'recent' && <span className="pill amber">Overlap</span>}
        <span className="spacer" />
        <div className="segmented small" role="tablist" aria-label="File view">
          <button role="tab" aria-selected={tab === 'edits'} className={tab === 'edits' ? 'on' : ''} onClick={() => {
              setFocus(null)
              setTab('edits')
            }}>
            Edits
          </button>
          <button role="tab" aria-selected={tab === 'file'} className={tab === 'file' ? 'on' : ''} onClick={() => setTab('file')}>
            File
          </button>
        </div>
      </div>
      <div className="file-body">
        {tab === 'edits' ? (
          <Edits path={path} touch={touch} now={now} focus={focus} />
        ) : (
          <Source path={path} touch={touch} now={now} scan={scan} onShowEdit={showEdit} />
        )}
      </div>
    </div>
  )
}
