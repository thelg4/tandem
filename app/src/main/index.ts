import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { basename, join } from 'node:path'
import {
  BrowserWindow,
  Menu,
  app,
  dialog,
  ipcMain,
  nativeTheme,
  shell,
  systemPreferences,
  type BrowserWindowConstructorOptions,
  type MenuItemConstructorOptions,
} from 'electron'
import type { Agent, MenuEvent, OpenResult, ProjectEntry, ProjectList } from '../shared/protocol'
import { PaneManager } from './panes'
import { runTandemJson } from './python'
import { WatcherBridge } from './watcher'

let win: BrowserWindow | null = null
let panes: PaneManager | null = null
let watcher: WatcherBridge | null = null
let repoRoot: string | null = null

function repoArg(): string | null {
  const i = process.argv.indexOf('--repo')
  const fromArgv = i >= 0 ? process.argv[i + 1] : undefined
  return fromArgv || process.env.TANDEM_REPO || null
}

function gitTopLevel(path: string): string | null {
  try {
    return execFileSync('git', ['-C', path, 'rev-parse', '--show-toplevel'], {
      encoding: 'utf8',
      timeout: 10000,
    }).trim()
  } catch {
    return null
  }
}

function sendMenu(event: MenuEvent): void {
  win?.webContents.send('menu', event)
}

function buildMenu(): void {
  const newPane = (agent: Agent) => () => sendMenu({ kind: 'new-pane', agent })
  const mac = process.platform === 'darwin'
  const template: MenuItemConstructorOptions[] = [
    ...(mac ? [{ role: 'appMenu' } as MenuItemConstructorOptions] : []),
    {
      label: 'File',
      submenu: [
        { label: 'Open Project…', accelerator: 'CmdOrCtrl+O', click: () => sendMenu({ kind: 'show-projects' }) },
        ...(mac ? [] : [{ type: 'separator' } as MenuItemConstructorOptions, { role: 'quit' } as MenuItemConstructorOptions]),
      ],
    },
    {
      label: 'Pane',
      submenu: [
        { label: 'New Claude pane', accelerator: 'CmdOrCtrl+T', click: newPane('claude_code') },
        { label: 'New Codex pane', accelerator: 'CmdOrCtrl+Shift+T', click: newPane('codex') },
        { label: 'Close pane', accelerator: 'CmdOrCtrl+W', click: () => sendMenu({ kind: 'close-pane' }) },
      ],
    },
    {
      label: 'Edit',
      submenu: [{ role: 'copy' }, { role: 'paste' }, { role: 'selectAll' }],
    },
    { role: 'viewMenu' },
    { role: 'windowMenu' },
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

/** The system accent color as #rrggbb; macOS only, elsewhere the renderer keeps its default. */
function accentColor(): string | null {
  if (process.platform !== 'darwin') return null
  const rgba = systemPreferences.getAccentColor()
  return /^[0-9a-f]{6}/i.test(rgba) ? `#${rgba.slice(0, 6)}` : null
}

/**
 * macOS: the title bar folds into the toolbar (traffic lights inset) and the window
 * shows the desktop through a sidebar material. Elsewhere: a normal frame, solid fill.
 */
function chrome(): BrowserWindowConstructorOptions {
  if (process.platform === 'darwin') {
    return {
      titleBarStyle: 'hiddenInset',
      trafficLightPosition: { x: 18, y: 18 },
      vibrancy: 'sidebar',
      visualEffectState: 'followWindow',
      backgroundColor: '#00000000',
    }
  }
  return { backgroundColor: nativeTheme.shouldUseDarkColors ? '#1e1e1e' : '#ececec' }
}

function createWindow(): void {
  win = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 900,
    minHeight: 600,
    ...chrome(),
    title: repoRoot ? `Tandem — ${basename(repoRoot)}` : 'Tandem',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })
  // Nothing remote is ever loaded in the app window.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('https://')) void shell.openExternal(url)
    return { action: 'deny' }
  })
  win.webContents.on('will-navigate', (event) => event.preventDefault())
  if (process.env.ELECTRON_RENDERER_URL) {
    void win.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    void win.loadFile(join(__dirname, '../renderer/index.html'))
  }
  // The accent color can change in System Settings while the app runs.
  win.on('focus', () => {
    win?.webContents.send('accent', accentColor())
    if (Date.now() - usageAt > 15_000) void refreshUsage()
  })
  win.on('closed', () => {
    win = null
  })
}

interface RecentEntry {
  path: string
  opened: string
}

interface LoggedProject {
  path: string
  name: string
  last_active: string
  sessions: Partial<Record<Agent, number>>
}

const recentFile = (): string => join(app.getPath('userData'), 'recent-projects.json')

function readRecent(): RecentEntry[] {
  try {
    const raw: unknown = JSON.parse(readFileSync(recentFile(), 'utf8'))
    return Array.isArray(raw) ? raw.filter((r): r is RecentEntry => typeof r?.path === 'string' && typeof r?.opened === 'string') : []
  } catch {
    return []
  }
}

function rememberRecent(path: string): void {
  const entries = [{ path, opened: new Date().toISOString() }, ...readRecent().filter((r) => r.path !== path)].slice(0, 20)
  try {
    writeFileSync(recentFile(), JSON.stringify(entries), { mode: 0o600 })
  } catch {
    // A recents list is a convenience; failing to save it changes nothing else.
  }
}

/** Projects the agents worked in (from their logs) merged with projects opened here. */
async function listProjects(): Promise<ProjectList> {
  const logged = await runTandemJson<{ projects: LoggedProject[] }>(app.getAppPath(), ['projects'])
  const byPath = new Map<string, ProjectEntry>()
  for (const p of logged?.projects ?? []) {
    byPath.set(p.path, { path: p.path, name: p.name, lastActive: p.last_active, sessions: p.sessions, opened: null })
  }
  for (const r of readRecent()) {
    if (!existsSync(r.path)) continue
    const known = byPath.get(r.path)
    if (known) known.opened = r.opened
    else byPath.set(r.path, { path: r.path, name: basename(r.path), lastActive: null, sessions: {}, opened: r.opened })
  }
  const latest = (p: ProjectEntry): string => [p.lastActive ?? '', p.opened ?? ''].sort().at(-1)!
  const projects = [...byPath.values()].sort((a, b) => latest(b).localeCompare(latest(a)))
  return { projects, home: homedir(), logsRead: logged !== null }
}

let started = false

/** Points the app at a repository: ends the old panes and watcher, then reloads the window. */
function openRepo(path: string): boolean {
  const top = gitTopLevel(path)
  if (!top) return false
  panes?.killAll()
  watcher?.stop()
  repoRoot = top
  started = false
  watcher = new WatcherBridge(
    top,
    app.getAppPath(),
    (msg) => win?.webContents.send('watcher:msg', msg),
    () => panes?.livePanes() ?? [],
  )
  panes = new PaneManager(top, {
    data: (paneId, data) => win?.webContents.send('pane:data', paneId, data),
    exit: (paneId, code) => win?.webContents.send('pane:exit', paneId, code),
    command: (cmd) => watcher?.send(cmd),
  })
  rememberRecent(top)
  win?.setTitle(`Tandem — ${basename(top)}`)
  win?.webContents.reload()
  return true
}

/** Asks before ending running agents; true when there are none or the user agrees. */
async function confirmLeave(): Promise<boolean> {
  const live = panes?.livePanes().length ?? 0
  if (!win || live === 0) return true
  const { response } = await dialog.showMessageBox(win, {
    type: 'warning',
    message: `Close ${live} running agent${live === 1 ? '' : 's'}?`,
    detail: 'Switching projects ends the Claude Code and Codex sessions running in this window.',
    buttons: ['Switch Project', 'Cancel'],
    defaultId: 0,
    cancelId: 1,
  })
  return response === 0
}

let usage: unknown = null
let usageRunning = false
let usageAt = 0

async function refreshUsage(): Promise<void> {
  if (usageRunning) return
  usageRunning = true
  try {
    const next = await runTandemJson<unknown>(app.getAppPath(), ['usage'])
    usageAt = Date.now()
    if (next !== null) {
      usage = next
      win?.webContents.send('usage', usage)
    }
  } finally {
    usageRunning = false
  }
}

function registerIpc(): void {
  ipcMain.on('ready', () => {
    if (!watcher) return
    if (!started) {
      started = true
      watcher.start()
      return
    }
    // The renderer reloaded and lost its panes: end their ptys and start a fresh
    // watcher so the new renderer receives hello, tree and sessions again.
    panes?.killAll()
    watcher.reset()
  })
  ipcMain.handle('repo', () => (repoRoot ? { root: repoRoot } : null))
  ipcMain.handle('accent', () => accentColor())
  ipcMain.handle('projects:list', () => listProjects())
  ipcMain.handle('project:open', async (_e, path: string): Promise<OpenResult> => {
    if (path === repoRoot) return { ok: true }
    if (!(await confirmLeave())) return { ok: false, canceled: true }
    return openRepo(path) ? { ok: true } : { ok: false, error: 'That folder is not inside a Git repository.' }
  })
  ipcMain.handle('project:choose', async (): Promise<OpenResult> => {
    const picked = win
      ? await dialog.showOpenDialog(win, { title: 'Open a Git repository', properties: ['openDirectory'] })
      : await dialog.showOpenDialog({ title: 'Open a Git repository', properties: ['openDirectory'] })
    const path = picked.filePaths[0]
    if (picked.canceled || !path) return { ok: false, canceled: true }
    if (!gitTopLevel(path)) return { ok: false, error: 'That folder is not inside a Git repository.' }
    if (!(await confirmLeave())) return { ok: false, canceled: true }
    return openRepo(path) ? { ok: true } : { ok: false, error: 'That folder is not inside a Git repository.' }
  })
  ipcMain.handle('usage', () => {
    if (Date.now() - usageAt > 15_000) void refreshUsage()
    return usage
  })
  ipcMain.handle('pane:create', (_e, agent: Agent) => ({ paneId: panes!.create(agent) }))
  ipcMain.handle('pane:restart', (_e, paneId: string) => panes!.restart(paneId))
  ipcMain.on('pane:write', (_e, paneId: string, data: string) => panes?.write(paneId, data))
  ipcMain.on('pane:resize', (_e, paneId: string, cols: number, rows: number) =>
    panes?.resize(paneId, cols, rows),
  )
  ipcMain.on('pane:kill', (_e, paneId: string) => panes?.kill(paneId))
}

app.whenReady().then(() => {
  buildMenu()
  registerIpc()
  // Open a repository given on the command line before the window loads, so its first
  // load is the workspace; without one, the window opens on the project picker.
  const given = repoArg()
  if (given && !openRepo(given)) {
    dialog.showErrorBox('Tandem', `Not inside a Git repository: ${given}`)
  }
  createWindow()
  void refreshUsage()
  setInterval(() => void refreshUsage(), 60_000)
})

app.on('before-quit', () => {
  panes?.killAll()
  watcher?.stop()
})

app.on('window-all-closed', () => app.quit())
