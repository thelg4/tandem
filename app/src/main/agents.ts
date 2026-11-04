import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import type { Agent } from '../shared/protocol'

const CODEX_APP_BUNDLE = '/Applications/ChatGPT.app/Contents/Resources/codex'

export function loginShell(): string {
  return process.env.SHELL || '/bin/zsh'
}

function onLoginPath(bin: string): string | null {
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
