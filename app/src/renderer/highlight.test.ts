import { describe, expect, it } from 'vitest'
import { highlightLines, languageFor } from './highlight'

describe('highlightLines', () => {
  it('highlights by extension and keeps every line self-contained', () => {
    const lines = highlightLines('/* one\n two */\nconst a = "<b>"\n', 'x.ts')!
    expect(lines).toHaveLength(4)
    expect(lines[0]).toBe('<span class="hljs-comment">/* one</span>')
    expect(lines[1]).toBe('<span class="hljs-comment"> two */</span>')
    expect(lines[2]).toContain('<span class="hljs-keyword">const</span>')
    expect(lines[2]).toContain('&lt;b&gt;')
  })

  it('leaves unknown types plain', () => {
    expect(languageFor('notes.unknownext')).toBeNull()
    expect(highlightLines('x', 'notes.unknownext')).toBeNull()
  })
})
