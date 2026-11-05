import { afterEach, describe, expect, it } from 'vitest'
import { loginShell, paneEnv } from './agents'

describe('paneEnv', () => {
  it('drops parent agent session markers but keeps user configuration', () => {
    const env = paneEnv({
      PATH: '/bin',
      CLAUDECODE: '1',
      CLAUDE_CODE_CHILD_SESSION: '1',
      CLAUDE_CODE_SESSION_ID: 'x',
      CLAUDE_CODE_MESSAGING_TOKEN: 't',
      CLAUDE_PID: '9',
      CLAUDE_CODE_USE_BEDROCK: '1',
      CODEX_THREAD_ID: 'c',
      CODEX_SANDBOX: 'seatbelt',
      CODEX_HOME: '/h',
    })
    expect(env).toEqual({
      PATH: '/bin',
      CLAUDE_CODE_USE_BEDROCK: '1',
      CODEX_HOME: '/h',
      TERM: 'xterm-256color',
      COLORTERM: 'truecolor',
    })
  })
})

describe('loginShell', () => {
  const saved = process.env.SHELL
  afterEach(() => {
    if (saved === undefined) delete process.env.SHELL
    else process.env.SHELL = saved
  })

  it('uses $SHELL when set', () => {
    process.env.SHELL = '/usr/bin/fish'
    expect(loginShell('linux')).toBe('/usr/bin/fish')
  })

  it('falls back to zsh on macOS and bash elsewhere', () => {
    delete process.env.SHELL
    expect(loginShell('darwin')).toBe('/bin/zsh')
    expect(loginShell('linux')).toBe('/bin/bash')
  })
})
