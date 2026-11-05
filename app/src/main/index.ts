import { execFileSync } from 'node:child_process'
import { join } from 'node:path'
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
import type { Agent, MenuEvent } from '../shared/protocol'
import { PaneManager } from './panes'
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

async function chooseRepo(): Promise<string | null> {
  const given = repoArg()
  if (given) return gitTopLevel(given)
  const picked = await dialog.showOpenDialog({
    title: 'Open a Git repository',
    properties: ['openDirectory'],
  })
  const path = picked.filePaths[0]
  return picked.canceled || !path ? null : gitTopLevel(path)
}

function sendMenu(event: MenuEvent): void {
  win?.webContents.send('menu', event)
}

function buildMenu(): void {
  const newPane = (agent: Agent) => () => sendMenu({ kind: 'new-pane', agent })
  const mac = process.platform === 'darwin'
  const template: MenuItemConstructorOptions[] = [
    mac ? { role: 'appMenu' } : { label: 'File', submenu: [{ role: 'quit' }] },
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
    title: repoRoot ? `Tandem — ${repoRoot.split('/').pop()}` : 'Tandem',
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
  win.on('focus', () => win?.webContents.send('accent', accentColor()))
  win.on('closed', () => {
    win = null
  })
}

function wire(repo: string): void {
  watcher = new WatcherBridge(
    repo,
    app.getAppPath(),
    (msg) => win?.webContents.send('watcher:msg', msg),
    () => panes?.livePanes() ?? [],
  )
  panes = new PaneManager(repo, {
    data: (paneId, data) => win?.webContents.send('pane:data', paneId, data),
    exit: (paneId, code) => win?.webContents.send('pane:exit', paneId, code),
    command: (cmd) => watcher?.send(cmd),
  })
  let started = false
  ipcMain.on('ready', () => {
    if (!started) {
      started = true
      watcher?.start()
      return
    }
    // The renderer reloaded and lost its panes: end their ptys and start a fresh
    // watcher so the new renderer receives hello, tree and sessions again.
    panes?.killAll()
    watcher?.reset()
  })
  ipcMain.handle('repo', () => ({ root: repo }))
  ipcMain.handle('accent', () => accentColor())
  ipcMain.handle('pane:create', (_e, agent: Agent) => ({ paneId: panes!.create(agent) }))
  ipcMain.handle('pane:restart', (_e, paneId: string) => panes!.restart(paneId))
  ipcMain.on('pane:write', (_e, paneId: string, data: string) => panes?.write(paneId, data))
  ipcMain.on('pane:resize', (_e, paneId: string, cols: number, rows: number) =>
    panes?.resize(paneId, cols, rows),
  )
  ipcMain.on('pane:kill', (_e, paneId: string) => panes?.kill(paneId))
}

app.whenReady().then(async () => {
  repoRoot = await chooseRepo()
  if (!repoRoot) {
    dialog.showErrorBox('Tandem', 'Pick a folder inside a Git repository.')
    app.quit()
    return
  }
  buildMenu()
  wire(repoRoot)
  createWindow()
})

app.on('before-quit', () => {
  panes?.killAll()
  watcher?.stop()
})

app.on('window-all-closed', () => app.quit())
