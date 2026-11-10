import type { ITheme } from '@xterm/xterm'

export const isMac = window.tandem.platform === 'darwin'

/** Shortcut text in the platform's own notation: ⇧⌘T on macOS, Ctrl+Shift+T elsewhere. */
export function shortcut(key: string, shift = false): string {
  if (isMac) return `${shift ? '⇧' : ''}⌘${key}`
  return `Ctrl+${shift ? 'Shift+' : ''}${key}`
}

const darkQuery = window.matchMedia('(prefers-color-scheme: dark)')

export function prefersDark(): boolean {
  return darkQuery.matches
}

/** Calls `cb` whenever the system switches between light and dark. */
export function onAppearanceChange(cb: (dark: boolean) => void): () => void {
  const handler = (e: MediaQueryListEvent): void => cb(e.matches)
  darkQuery.addEventListener('change', handler)
  return () => darkQuery.removeEventListener('change', handler)
}

/** Marks the document with its platform and keeps --accent on the system accent color. */
export function installAppearance(): void {
  const root = document.documentElement
  root.dataset.platform = window.tandem.platform
  const setAccent = (color: string | null): void => {
    if (color) root.style.setProperty('--accent', color)
    else root.style.removeProperty('--accent')
  }
  void window.tandem.accent().then(setAccent)
  window.tandem.onAccent(setAccent)
}

// Terminal palettes modeled on Terminal.app's Basic profile, so agent output keeps
// readable contrast in both appearances.
const DARK: ITheme = {
  background: '#1e1e1e',
  foreground: '#e5e5e5',
  selectionBackground: '#3f638b',
  black: '#000000',
  red: '#ff6b68',
  green: '#4ec66b',
  yellow: '#e5c454',
  blue: '#5a9cf8',
  magenta: '#d08af0',
  cyan: '#4fc6d6',
  white: '#e5e5e5',
  brightBlack: '#7f7f7f',
  brightRed: '#ff8a87',
  brightGreen: '#6fdc89',
  brightYellow: '#f5d77a',
  brightBlue: '#7fb4ff',
  brightMagenta: '#e0a8ff',
  brightCyan: '#77dbe8',
  brightWhite: '#ffffff',
}

const LIGHT: ITheme = {
  background: '#ffffff',
  foreground: '#1d1d1f',
  selectionBackground: '#b3d7ff',
  black: '#000000',
  red: '#c41a16',
  green: '#1f7a35',
  yellow: '#8a6d00',
  blue: '#0b4fbf',
  magenta: '#9b2393',
  cyan: '#0e7490',
  white: '#8e8e93',
  brightBlack: '#5e5e63',
  brightRed: '#e0312c',
  brightGreen: '#2c9447',
  brightYellow: '#a07f00',
  brightBlue: '#1f6feb',
  brightMagenta: '#b13ab0',
  brightCyan: '#1590a8',
  brightWhite: '#3a3a3c',
}

export function terminalTheme(dark: boolean, cursor: string): ITheme {
  return { ...(dark ? DARK : LIGHT), cursor, cursorAccent: dark ? DARK.background : LIGHT.background }
}

/** SF Mono on macOS, the usual monospace faces on Linux. */
export const MONO_FONT = isMac
  ? '"SF Mono", SFMono-Regular, ui-monospace, Menlo, monospace'
  : '"DejaVu Sans Mono", "Liberation Mono", "Ubuntu Mono", monospace'
