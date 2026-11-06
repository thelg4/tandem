import { cp, lstat, mkdir, readFile, readdir, realpath, stat } from 'node:fs/promises'
import { homedir } from 'node:os'
import { basename, join } from 'node:path'
import type { Agent, AgentExtras, McpEntry, PluginEntry, Skill, SkillCopy, SkillScope } from '../shared/protocol'

export const AGENTS: readonly Agent[] = ['claude_code', 'codex']

/** A folder name that is safe to join onto a skills directory. */
export const SKILL_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/

export interface Homes {
  /** ~/.claude, or $CLAUDE_CONFIG_DIR */
  claude: string
  /** ~/.codex, or $CODEX_HOME */
  codex: string
  /** ~/.claude.json, where Claude keeps user and per-project MCP servers. */
  claudeJson: string
}

export function agentHomes(env: NodeJS.ProcessEnv = process.env, home = homedir()): Homes {
  return {
    claude: env.CLAUDE_CONFIG_DIR || join(home, '.claude'),
    codex: env.CODEX_HOME || join(home, '.codex'),
    claudeJson: env.CLAUDE_CONFIG_DIR ? join(env.CLAUDE_CONFIG_DIR, '.claude.json') : join(home, '.claude.json'),
  }
}

/**
 * Where each agent loads skills from. Claude reads ~/.claude/skills and .claude/skills
 * in the project; Codex reads $CODEX_HOME/skills and .agents/skills in the project.
 */
export function skillDirs(homes: Homes, repo: string | null): AgentExtras['dirs'] {
  return {
    user: { claude_code: join(homes.claude, 'skills'), codex: join(homes.codex, 'skills') },
    project: repo ? { claude_code: join(repo, '.claude', 'skills'), codex: join(repo, '.agents', 'skills') } : null,
  }
}

/** The name and description from a SKILL.md's YAML front matter (simple keys and folded blocks). */
export function parseFrontmatter(text: string): { name?: string; description?: string } {
  const m = /^---\r?\n([\s\S]*?)\r?\n---/.exec(text.replace(/^\uFEFF/, ''))
  if (!m) return {}
  const out: Record<string, string> = {}
  const lines = (m[1] ?? '').split(/\r?\n/)
  for (let i = 0; i < lines.length; i++) {
    const kv = /^([A-Za-z_][\w-]*):\s*(.*)$/.exec(lines[i] ?? '')
    if (!kv) continue
    const key = kv[1]!
    let value = (kv[2] ?? '').trim()
    if (/^[>|][+-]?$/.test(value)) {
      const block: string[] = []
      for (let next = lines[i + 1]; next !== undefined && (/^\s+\S/.test(next) || next.trim() === ''); next = lines[i + 1]) {
        block.push(next.trim())
        i++
      }
      value = value.startsWith('|') ? block.join('\n').trim() : block.filter(Boolean).join(' ')
    } else if (/^(["']).*\1$/.test(value)) {
      value = value.slice(1, -1).replace(/\\"/g, '"').replace(/''/g, "'")
    }
    out[key] = value
  }
  return { name: out.name || undefined, description: out.description || undefined }
}

interface Found {
  name: string
  description: string
  copy: SkillCopy
  text: string
}

/** Skill folders (with a SKILL.md) in one directory. Hidden folders are the agent's own and are skipped. */
export async function readSkillDir(dir: string): Promise<Found[]> {
  let names: string[]
  try {
    names = await readdir(dir)
  } catch {
    return []
  }
  const out: Found[] = []
  for (const name of names.sort()) {
    if (name.startsWith('.') || !SKILL_NAME.test(name)) continue
    const path = join(dir, name)
    try {
      const text = await readFile(join(path, 'SKILL.md'), 'utf8')
      const linked = (await lstat(path)).isSymbolicLink()
      out.push({ name, description: parseFrontmatter(text).description ?? '', copy: { path, linked }, text })
    } catch {
      // Not a skill (no SKILL.md, or a dangling link).
    }
  }
  return out
}

/** One row per skill name and scope, with each agent's copy. */
export function mergeSkills(found: { scope: SkillScope; agent: Agent; skills: Found[] }[]): Skill[] {
  const rows = new Map<string, Skill & { texts: Partial<Record<Agent, string>> }>()
  for (const { scope, agent, skills } of found) {
    for (const f of skills) {
      const key = `${scope}\0${f.name}`
      const row = rows.get(key) ?? { name: f.name, scope, description: '', copies: {}, differs: false, texts: {} }
      row.copies[agent] = f.copy
      row.texts[agent] = f.text
      row.description ||= f.description
      rows.set(key, row)
    }
  }
  return [...rows.values()]
    .map(({ texts, ...row }) => ({
      ...row,
      differs: texts.claude_code !== undefined && texts.codex !== undefined && texts.claude_code !== texts.codex,
    }))
    .sort((a, b) => (a.scope === b.scope ? a.name.localeCompare(b.name) : a.scope === 'project' ? -1 : 1))
}

type TomlValue = string | boolean | number
export type TomlTables = Map<string, Record<string, TomlValue>>

function splitKey(key: string): string[] {
  const parts: string[] = []
  const re = /\s*(?:"((?:[^"\\]|\\.)*)"|'([^']*)'|([A-Za-z0-9_-]+))\s*(?:\.|$)/y
  let m: RegExpExecArray | null
  while (re.lastIndex < key.length && (m = re.exec(key))) parts.push(m[1] ?? m[2] ?? m[3] ?? '')
  return parts
}

/**
 * Just enough TOML to read Codex's config: tables, and string, boolean and number
 * values. Arrays and inline tables are skipped. Keys are joined with NUL.
 */
export function parseToml(text: string): TomlTables {
  const tables: TomlTables = new Map([['', {}]])
  let current = tables.get('')!
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line || line.startsWith('#')) continue
    const header = /^\[([^[\]]+)\]\s*(#.*)?$/.exec(line)
    if (header) {
      const key = splitKey(header[1] ?? '').join('\0')
      current = tables.get(key) ?? {}
      tables.set(key, current)
      continue
    }
    if (line.startsWith('[[')) {
      current = {}
      continue
    }
    const kv = /^("(?:[^"\\]|\\.)*"|'[^']*'|[A-Za-z0-9_-]+)\s*=\s*(.+)$/.exec(line)
    if (!kv) continue
    const key = splitKey(kv[1] ?? '')[0]
    const v = (kv[2] ?? '').trim()
    let value: TomlValue | undefined
    const str = /^"((?:[^"\\]|\\.)*)"|^'([^']*)'/.exec(v)
    if (str) value = str[2] ?? (str[1] ?? '').replace(/\\(["\\])/g, '$1')
    else if (/^(true|false)\b/.test(v)) value = v.startsWith('true')
    else if (/^-?\d+(\.\d+)?\b/.test(v)) value = Number(/^-?\d+(\.\d+)?/.exec(v)![0])
    if (key !== undefined && value !== undefined) current[key] = value
  }
  return tables
}

function splitId(id: string): { name: string; marketplace: string | null } {
  const at = id.lastIndexOf('@')
  return at > 0 ? { name: id.slice(0, at), marketplace: id.slice(at + 1) } : { name: id, marketplace: null }
}

async function readJson(path: string): Promise<unknown> {
  try {
    return JSON.parse(await readFile(path, 'utf8'))
  } catch {
    return null
  }
}

function str(v: unknown): string | null {
  return typeof v === 'string' && v ? v : null
}

/** Claude's installed plugins, with enabled flags from settings.json. */
export async function claudePlugins(homes: Homes, repo: string | null): Promise<PluginEntry[]> {
  const installed = (await readJson(join(homes.claude, 'plugins', 'installed_plugins.json'))) as {
    plugins?: Record<string, { scope?: string; projectPath?: string; installPath?: string; version?: string }[]>
  } | null
  const settings = (await readJson(join(homes.claude, 'settings.json'))) as { enabledPlugins?: Record<string, unknown> } | null
  const enabled = settings?.enabledPlugins ?? {}
  const out: PluginEntry[] = []
  for (const [id, installs] of Object.entries(installed?.plugins ?? {})) {
    if (!Array.isArray(installs)) continue
    const inst = installs.find((i) => i.scope === 'user' || (repo && i.projectPath === repo)) ?? null
    if (!inst) continue
    const manifest = inst.installPath
      ? ((await readJson(join(inst.installPath, '.claude-plugin', 'plugin.json'))) as { description?: unknown } | null)
      : null
    out.push({
      agent: 'claude_code',
      id,
      ...splitId(id),
      version: str(inst.version),
      description: str(manifest?.description),
      enabled: enabled[id] !== false,
    })
  }
  return out.sort((a, b) => a.name.localeCompare(b.name))
}

async function newestDir(dir: string): Promise<string | null> {
  try {
    const entries = await readdir(dir)
    const dated = await Promise.all(entries.map(async (e) => ({ e, t: (await stat(join(dir, e))).mtimeMs })))
    dated.sort((a, b) => b.t - a.t)
    return dated[0] ? join(dir, dated[0].e) : null
  } catch {
    return null
  }
}

/** Codex's plugins from config.toml, described from its plugin cache when present. */
export async function codexPlugins(homes: Homes, tables: TomlTables): Promise<PluginEntry[]> {
  const out: PluginEntry[] = []
  for (const [key, values] of tables) {
    const [head, id, ...rest] = key.split('\0')
    if (head !== 'plugins' || !id || rest.length) continue
    const { name, marketplace } = splitId(id)
    let description: string | null = null
    let version: string | null = null
    if (marketplace && SKILL_NAME.test(marketplace) && SKILL_NAME.test(name)) {
      const dir = await newestDir(join(homes.codex, 'plugins', 'cache', marketplace, name))
      if (dir) {
        const manifest = (await readJson(join(dir, '.codex-plugin', 'plugin.json'))) as {
          description?: unknown
          version?: unknown
        } | null
        description = str(manifest?.description)
        version = str(manifest?.version) ?? basename(dir)
      }
    }
    out.push({ agent: 'codex', id, name, marketplace, version, description, enabled: values.enabled !== false })
  }
  return out.sort((a, b) => a.name.localeCompare(b.name))
}

type McpConfig = Record<string, { command?: unknown; args?: unknown; url?: unknown }>

function mcpTarget(cfg: { command?: unknown; url?: unknown }): string | null {
  return str(cfg.command) ?? str(cfg.url)
}

/** MCP servers: Claude's from ~/.claude.json and the project's .mcp.json; Codex's from config.toml. */
export async function mcpServers(homes: Homes, repo: string | null, tables: TomlTables): Promise<McpEntry[]> {
  const out: McpEntry[] = []
  const claude = (await readJson(homes.claudeJson)) as {
    mcpServers?: McpConfig
    projects?: Record<string, { mcpServers?: McpConfig }>
  } | null
  for (const [name, cfg] of Object.entries(claude?.mcpServers ?? {})) {
    out.push({ agent: 'claude_code', name, scope: 'user', target: mcpTarget(cfg) })
  }
  if (repo) {
    const local = claude?.projects?.[repo]?.mcpServers ?? {}
    const shared = ((await readJson(join(repo, '.mcp.json'))) as { mcpServers?: McpConfig } | null)?.mcpServers ?? {}
    for (const [name, cfg] of Object.entries({ ...shared, ...local })) {
      out.push({ agent: 'claude_code', name, scope: 'project', target: mcpTarget(cfg) })
    }
  }
  for (const [key, values] of tables) {
    const [head, name, ...rest] = key.split('\0')
    if (head !== 'mcp_servers' || !name || rest.length) continue
    out.push({ agent: 'codex', name, scope: 'user', target: mcpTarget(values) })
  }
  return out.sort((a, b) => a.name.localeCompare(b.name))
}

/** Everything both agents have installed, for the Skills sheet. */
export async function listExtras(homes: Homes, repo: string | null): Promise<AgentExtras> {
  const dirs = skillDirs(homes, repo)
  const found: { scope: SkillScope; agent: Agent; skills: Found[] }[] = []
  for (const scope of ['user', 'project'] as const) {
    const d = dirs[scope]
    if (!d) continue
    for (const agent of AGENTS) found.push({ scope, agent, skills: await readSkillDir(d[agent]) })
  }
  let toml: TomlTables = new Map()
  try {
    toml = parseToml(await readFile(join(homes.codex, 'config.toml'), 'utf8'))
  } catch {
    // No Codex config.
  }
  const [claude, codex, mcp] = await Promise.all([claudePlugins(homes, repo), codexPlugins(homes, toml), mcpServers(homes, repo, toml)])
  return { skills: mergeSkills(found), plugins: [...claude, ...codex], mcp, dirs }
}

/**
 * Skill folders to install from a folder the user picked: the folder itself when it has
 * a SKILL.md, otherwise each child folder that does.
 */
export async function skillsIn(picked: string): Promise<{ name: string; path: string }[]> {
  let text: string | null = null
  try {
    text = await readFile(join(picked, 'SKILL.md'), 'utf8')
  } catch {
    return (await readSkillDir(picked)).map((f) => ({ name: f.name, path: f.copy.path }))
  }
  // The folder's own name, or the front matter's when the folder's is not usable.
  const name = SKILL_NAME.test(basename(picked)) ? basename(picked) : (parseFrontmatter(text).name ?? '')
  return SKILL_NAME.test(name) ? [{ name, path: picked }] : []
}

/**
 * Copy one skill folder into a skills directory. Symlinks are followed, so the copy
 * stands alone. Refuses to replace an existing skill.
 */
export async function installSkill(from: string, toDir: string, name: string): Promise<'installed' | 'exists'> {
  if (!SKILL_NAME.test(name)) throw new Error(`Not a valid skill name: ${name}`)
  const dest = join(toDir, name)
  try {
    await lstat(dest)
    return 'exists'
  } catch {
    // Free.
  }
  const src = await realpath(from)
  await mkdir(toDir, { recursive: true })
  await cp(src, dest, { recursive: true, dereference: true, errorOnExist: true, force: false })
  return 'installed'
}
