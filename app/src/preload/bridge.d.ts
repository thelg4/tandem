import type { Agent, MenuEvent, OpenResult, ProjectList, UsageSummary, WatcherMsg } from '../shared/protocol'

export interface TandemBridge {
  /** The OS, for platform-specific chrome: darwin gets the inset title bar and vibrancy. */
  platform: string
  /** The system accent color (#rrggbb), or null where the OS has none to offer. */
  accent(): Promise<string | null>
  onAccent(cb: (color: string | null) => void): () => void
  /** The open repository, or null when the app is showing the project picker. */
  repo(): Promise<{ root: string } | null>
  projects(): Promise<ProjectList>
  /** Switch to a repository; the window reloads on success. */
  openProject(path: string): Promise<OpenResult>
  /** Pick a folder with the system dialog, then open it. */
  chooseProject(): Promise<OpenResult>
  /** The latest usage summary (refreshed in the background), or null before the first. */
  usage(): Promise<UsageSummary | null>
  onUsage(cb: (usage: UsageSummary) => void): () => void
  /** Start the watcher once the renderer is listening, so no early message is lost. */
  ready(): void
  createPane(agent: Agent): Promise<{ paneId: string }>
  write(paneId: string, data: string): void
  resize(paneId: string, cols: number, rows: number): void
  kill(paneId: string): void
  restart(paneId: string): Promise<void>
  onPaneData(cb: (paneId: string, data: string) => void): () => void
  onPaneExit(cb: (paneId: string, code: number) => void): () => void
  onWatcher(cb: (msg: WatcherMsg) => void): () => void
  onMenu(cb: (event: MenuEvent) => void): () => void
}

declare global {
  interface Window {
    tandem: TandemBridge
  }
}
