import hljs from 'highlight.js/lib/core'
import bash from 'highlight.js/lib/languages/bash'
import css from 'highlight.js/lib/languages/css'
import go from 'highlight.js/lib/languages/go'
import ini from 'highlight.js/lib/languages/ini'
import java from 'highlight.js/lib/languages/java'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import markdown from 'highlight.js/lib/languages/markdown'
import python from 'highlight.js/lib/languages/python'
import ruby from 'highlight.js/lib/languages/ruby'
import rust from 'highlight.js/lib/languages/rust'
import sql from 'highlight.js/lib/languages/sql'
import swift from 'highlight.js/lib/languages/swift'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import yaml from 'highlight.js/lib/languages/yaml'

const LANGS = { bash, css, go, ini, java, javascript, json, markdown, python, ruby, rust, sql, swift, typescript, xml, yaml }
for (const [name, lang] of Object.entries(LANGS)) hljs.registerLanguage(name, lang)

const BY_EXT: Record<string, string> = {
  sh: 'bash', bash: 'bash', zsh: 'bash', css: 'css', go: 'go', toml: 'ini', ini: 'ini', cfg: 'ini',
  java: 'java', js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'javascript', json: 'json',
  md: 'markdown', py: 'python', rb: 'ruby', rs: 'rust', sql: 'sql', swift: 'swift', ts: 'typescript',
  tsx: 'typescript', mts: 'typescript', html: 'xml', xml: 'xml', svg: 'xml', yml: 'yaml', yaml: 'yaml',
}

/** Highlighting is skipped past this size; the text is shown plain. */
const MAX_HIGHLIGHT_CHARS = 400_000

export function languageFor(path: string): string | null {
  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  return BY_EXT[ext] ?? null
}

/**
 * Highlighted HTML for each line of ``text`` (escaped by highlight.js), or null when the
 * language is unknown or the file is too big. Spans that cross lines are closed and
 * reopened so every line stands alone.
 */
export function highlightLines(text: string, path: string): string[] | null {
  const lang = languageFor(path)
  if (!lang || text.length > MAX_HIGHLIGHT_CHARS) return null
  const html = hljs.highlight(text, { language: lang, ignoreIllegals: true }).value
  const out: string[] = []
  let open: string[] = []
  for (const raw of html.split('\n')) {
    const line = open.join('') + raw
    const stack = [...open]
    for (const m of raw.matchAll(/<span class="[^"]*">|<\/span>/g)) {
      if (m[0] === '</span>') stack.pop()
      else stack.push(m[0])
    }
    out.push(line + '</span>'.repeat(stack.length))
    open = stack
  }
  return out
}
