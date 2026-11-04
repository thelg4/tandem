import { describe, expect, it } from 'vitest'
import { paneEnv } from './agents'

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
