import type {
  Agent,
  AgentExtras,
  Clash,
  ExtrasResult,
  FileBlame,
  FileContents,
  FileEdit,
  HistoryEntry,
  MenuEvent,
  OpenResult,
  PaneNotice,
  ProjectList,
  ScanStatus,
  SkillScope,
  UsageSummary,
  WatcherMsg,
} from '../shared/protocol'

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
  /** Skills, plugins and MCP servers both agents have installed. */
  extras(): Promise<AgentExtras>
  /** Copy a skill the other agent has into ``to``'s skills folder. */
  copySkill(name: string, scope: SkillScope, to: Agent): Promise<ExtrasResult>
  /** Pick a folder with the system dialog and install its skills for both agents. */
  addSkills(scope: SkillScope): Promise<ExtrasResult>
  /** Remove one agent's copy, after a native confirmation; folders go to the Trash. */
  removeSkill(name: string, scope: SkillScope, agent: Agent): Promise<ExtrasResult>
  revealSkill(name: string, scope: SkillScope, agent: Agent): void
  /** A file in the open repository, by repo-relative path; refused outside it. */
  readFile(rel: string): Promise<FileContents>
  /** Successful agent edits to a file from the logs, newest first; null if unavailable. */
  fileEdits(rel: string): Promise<FileEdit[] | null>
  /** Per-line agent blame from the latest scan, aligned with the file on disk; null if unavailable. */
  fileBlame(rel: string): Promise<FileBlame | null>
  scanStatus(): Promise<ScanStatus>
  onScanStatus(cb: (status: ScanStatus) => void): () => void
  /** Recent cross-agent reverts, rewrites and removals from the latest scan. */
  clashes(): Promise<Clash[]>
  onClashes(cb: (clashes: Clash[]) => void): () => void
  /** Show a system notification unless Tandem is focused or notifications are off. */
  notify(notice: PaneNotice): void
  /** Panes waiting on the user, for the Dock badge; 0 clears it. */
  setBadge(count: number): void
  /** Start the watcher once the renderer is listening, so no early message is lost. */
  ready(): void
  /** Start an agent; ``resume`` (the agent's own session id) continues that session. */
  createPane(agent: Agent, resume?: string): Promise<{ paneId: string }>
  /** This repository's agent sessions from the logs, newest first; null if unavailable. */
  sessions(): Promise<HistoryEntry[] | null>
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
