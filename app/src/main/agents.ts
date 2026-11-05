import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import type { Agent } from '../shared/protocol'

const CODEX_APP_BUNDLE = '/Applications/ChatGPT.app/Contents/Resources/codex'

/** The user's shell; when $SHELL is unset, the platform's default login shell. */
export function loginShell(platform: NodeJS.Platform = process.platform): string {
  return process.env.SHELL || (platform === 'darwin' ? '/bin/zsh' : '/bin/bash')
}

/** Where `bin` is on the login PATH. An app opened from the Dock or a desktop launcher has a bare PATH. */
export function onLoginPath(bin: string): string | null {
  try {
    const out = execFileSync(loginShell(), ['-l', '-c', `command -v ${bin}`], {
      encoding: 'utf8',
      timeout: 5000,
    }).trim()
    return out || null
  } catch {
    return null
  }
}

let codexCache: string | null = null

/** TANDEM_CODEX_BIN, then `codex` on the login PATH, then the copy bundled with the app. */
export function resolveCodex(): string {
  if (codexCache) return codexCache
  codexCache =
    process.env.TANDEM_CODEX_BIN ||
    onLoginPath('codex') ||
    (existsSync(CODEX_APP_BUNDLE) ? CODEX_APP_BUNDLE : 'codex')
  return codexCache
}

let pythonCache: string | null = null

/** TANDEM_PYTHON, then `python3` on the login PATH, then plain `python3`. */
export function resolvePython(): string {
  if (process.env.TANDEM_PYTHON) return process.env.TANDEM_PYTHON
  pythonCache ??= onLoginPath('python3') || 'python3'
  return pythonCache
}

export function shellQuote(s: string): string {
  return `'${s.replace(/'/g, `'\\''`)}'`
}

export function agentCommand(agent: Agent, claudeSessionId?: string): string {
  if (agent === 'claude_code') {
    const bin = process.env.TANDEM_CLAUDE_BIN || 'claude'
    return `${shellQuote(bin)} --session-id ${shellQuote(claudeSessionId ?? '')}`
  }
  return shellQuote(resolveCodex())
}

// Variables a running agent sets for its own children. A pane launched from inside a
// Claude Code or Codex session must not inherit them: Claude treats an inherited
// CLAUDE_CODE_CHILD_SESSION as a subprocess and stops saving its transcript, which
// would leave the watcher blind. User configuration (CLAUDE_CODE_USE_BEDROCK,
// CODEX_HOME, ...) is kept.
const SESSION_VARS = new Set([
  'CLAUDECODE',
  'CLAUDE_PID',
  'CLAUDE_EFFORT',
  'CLAUDE_CODE_ENTRYPOINT',
  'CLAUDE_CODE_EXECPATH',
  'CLAUDE_CODE_SESSION_ID',
  'CLAUDE_CODE_CHILD_SESSION',
  'CLAUDE_CODE_SESSION_ATTENDED',
  'CLAUDE_CODE_MESSAGING_SOCKET',
  'CLAUDE_CODE_MESSAGING_TOKEN',
  'CODEX_THREAD_ID',
  'CODEX_SANDBOX',
  'CODEX_SANDBOX_NETWORK_DISABLED',
  'CODEX_CI',
])

export function paneEnv(env: NodeJS.ProcessEnv): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [key, value] of Object.entries(env)) {
    if (value !== undefined && !SESSION_VARS.has(key)) out[key] = value
  }
  return { ...out, TERM: 'xterm-256color', COLORTERM: 'truecolor' }
}
