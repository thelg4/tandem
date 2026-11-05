import { useEffect, useMemo, useState } from 'react'
import { AGENT_COLORS, AGENT_NAMES, type FileContents, type FileEdit } from '../../shared/protocol'
import { Icon } from '../components/Icon'
import { relativeTime } from '../format'
import { highlightLines } from '../highlight'
import type { AppState } from '../state/store'

interface Props {
  state: AppState
  path: string
  now: number
  onBack: () => void
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

function EditCard({ edit, now }: { edit: FileEdit; now: number }): JSX.Element {
  const added = edit.hunks.reduce((n, h) => n + h.lines.filter((l) => l[0] === '+').length, 0)
  const removed = edit.hunks.reduce((n, h) => n + h.lines.filter((l) => l[0] === '-').length, 0)
  const change = CHANGE_TEXT[edit.change]
  return (
    <article className="edit" style={{ ['--agent' as string]: AGENT_COLORS[edit.agent] }}>
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

function Edits({ path, touch, now }: { path: string; touch: number; now: number }): JSX.Element {
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
  return (
    <div className="edits">
      {edits.map((e, i) => (
        <EditCard key={`${e.session_id}-${e.at}-${i}`} edit={e} now={now} />
      ))}
      {edits.length >= 50 && <p className="view-note">Showing the 50 most recent edits.</p>}
    </div>
  )
}

function Source({ path, touch }: { path: string; touch: number }): JSX.Element {
  const [file, setFile] = useState<FileContents | undefined>(undefined)
  useEffect(() => {
    let live = true
    void window.tandem.readFile(path).then((f) => live && setFile(f))
    return () => {
      live = false
    }
  }, [path, touch])
  const lines = useMemo(() => (file?.kind === 'text' ? highlightLines(file.text, path) : null), [file, path])
  if (file === undefined) return <p className="view-note">Loading…</p>
  if (file.kind === 'missing') return <p className="view-note">This file isn’t in the working tree. It may have been deleted or renamed.</p>
  if (file.kind === 'binary') return <p className="view-note">Binary file ({sizeText(file.size)}).</p>
  if (file.kind === 'too_large') return <p className="view-note">Too large to show ({sizeText(file.size)}; the limit is 2 MB).</p>
  const count = file.text.split('\n').length - (file.text.endsWith('\n') ? 1 : 0)
  return (
    <div className="source">
      <pre className="gutter" aria-hidden="true">
        {Array.from({ length: Math.max(1, count) }, (_, i) => i + 1).join('\n')}
      </pre>
      {lines ? (
        <pre className="code hljs" dangerouslySetInnerHTML={{ __html: lines.slice(0, Math.max(1, count)).join('\n') }} />
      ) : (
        <pre className="code">{file.text}</pre>
      )}
    </div>
  )
}

/** One file: every agent edit to it from the logs, and its current contents. */
export function FileView({ state, path, now, onBack }: Props): JSX.Element {
  const [tab, setTab] = useState<'edits' | 'file'>('edits')
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
          <button role="tab" aria-selected={tab === 'edits'} className={tab === 'edits' ? 'on' : ''} onClick={() => setTab('edits')}>
            Edits
          </button>
          <button role="tab" aria-selected={tab === 'file'} className={tab === 'file' ? 'on' : ''} onClick={() => setTab('file')}>
            File
          </button>
        </div>
      </div>
      <div className="file-body">{tab === 'edits' ? <Edits path={path} touch={touch} now={now} /> : <Source path={path} touch={touch} />}</div>
    </div>
  )
}
