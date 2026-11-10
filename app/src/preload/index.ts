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
  fileBlame: (rel) => ipcRenderer.invoke('file:blame', rel),
  scanStatus: () => ipcRenderer.invoke('scan:status'),
  onScanStatus: (cb) => listen('scan:status', cb),
  clashes: () => ipcRenderer.invoke('clashes'),
  onClashes: (cb) => listen('clashes', cb),
  onUsage: (cb) => listen('usage', cb),
  extras: () => ipcRenderer.invoke('extras:list'),
  copySkill: (name, scope, to) => ipcRenderer.invoke('skills:copy', name, scope, to),
  addSkills: (scope) => ipcRenderer.invoke('skills:add', scope),
  removeSkill: (name, scope, agent) => ipcRenderer.invoke('skills:remove', name, scope, agent),
  revealSkill: (name, scope, agent) => ipcRenderer.send('skills:reveal', name, scope, agent),
  notify: (notice) => ipcRenderer.send('notify', notice),
  setBadge: (count) => ipcRenderer.send('badge', count),
  ready: () => ipcRenderer.send('ready'),
  createPane: (agent, resume) => ipcRenderer.invoke('pane:create', agent, resume),
  sessions: () => ipcRenderer.invoke('sessions:list'),
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
