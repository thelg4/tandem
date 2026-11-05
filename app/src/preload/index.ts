import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron'
import type { TandemBridge } from './bridge'

function listen<A extends unknown[]>(channel: string, cb: (...args: A) => void): () => void {
  const handler = (_event: IpcRendererEvent, ...args: unknown[]): void => cb(...(args as A))
  ipcRenderer.on(channel, handler)
  return () => ipcRenderer.removeListener(channel, handler)
}

const bridge: TandemBridge = {
  platform: process.platform,
  accent: () => ipcRenderer.invoke('accent'),
  onAccent: (cb) => listen('accent', cb),
  repo: () => ipcRenderer.invoke('repo'),
  projects: () => ipcRenderer.invoke('projects:list'),
  openProject: (path) => ipcRenderer.invoke('project:open', path),
  chooseProject: () => ipcRenderer.invoke('project:choose'),
  usage: () => ipcRenderer.invoke('usage'),
  readFile: (rel) => ipcRenderer.invoke('file:read', rel),
  fileEdits: (rel) => ipcRenderer.invoke('file:edits', rel),
  onUsage: (cb) => listen('usage', cb),
  notify: (notice) => ipcRenderer.send('notify', notice),
  setBadge: (count) => ipcRenderer.send('badge', count),
  ready: () => ipcRenderer.send('ready'),
  createPane: (agent) => ipcRenderer.invoke('pane:create', agent),
  write: (paneId, data) => ipcRenderer.send('pane:write', paneId, data),
  resize: (paneId, cols, rows) => ipcRenderer.send('pane:resize', paneId, cols, rows),
  kill: (paneId) => ipcRenderer.send('pane:kill', paneId),
  restart: (paneId) => ipcRenderer.invoke('pane:restart', paneId),
  onPaneData: (cb) => listen('pane:data', cb),
  onPaneExit: (cb) => listen('pane:exit', cb),
  onWatcher: (cb) => listen('watcher:msg', cb),
  onMenu: (cb) => listen('menu', cb),
}

contextBridge.exposeInMainWorld('tandem', bridge)
