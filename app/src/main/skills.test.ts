import { mkdir, mkdtemp, readFile, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { installSkill, listExtras, parseFrontmatter, parseToml, skillsIn, type Homes } from './skills'

async function tmp(): Promise<string> {
  return mkdtemp(join(tmpdir(), 'tandem-skills-'))
}

async function skill(dir: string, name: string, description: string): Promise<string> {
  const path = join(dir, name)
  await mkdir(path, { recursive: true })
  await writeFile(join(path, 'SKILL.md'), `---\nname: ${name}\ndescription: ${description}\n---\n\nBody.\n`)
  return path
}

describe('parseFrontmatter', () => {
  it('reads plain, quoted and folded values', () => {
    expect(parseFrontmatter('---\nname: a\ndescription: "Say \\"hi\\""\n---\n')).toEqual({ name: 'a', description: 'Say "hi"' })
    expect(parseFrontmatter('---\nname: b\ndescription: >\n  one\n  two\nother: x\n---\n')).toEqual({ name: 'b', description: 'one two' })
    expect(parseFrontmatter('no front matter')).toEqual({})
  })
})

describe('parseToml', () => {
  it('reads tables with quoted keys, and skips what it does not understand', () => {
    const t = parseToml(
      [
        'model = "x" # comment',
        '[plugins."pdf@openai"]',
        'enabled = false',
        '[mcp_servers.repl]',
        'command = "/bin/repl"',
        'args = ["a", "b"]',
        'timeout = 120',
        '[mcp_servers.repl.env]',
        "PATH = '/usr/bin'",
      ].join('\n'),
    )
    expect(t.get('')).toEqual({ model: 'x' })
    expect(t.get('plugins\0pdf@openai')).toEqual({ enabled: false })
    expect(t.get('mcp_servers\0repl')).toEqual({ command: '/bin/repl', timeout: 120 })
    expect(t.get('mcp_servers\0repl\0env')).toEqual({ PATH: '/usr/bin' })
  })
})

describe('listExtras', () => {
  it('merges both agents’ skills, plugins and MCP servers', async () => {
    const root = await tmp()
    const homes: Homes = { claude: join(root, 'claude'), codex: join(root, 'codex'), claudeJson: join(root, 'claude.json') }
    const repo = join(root, 'repo')
    await skill(join(homes.claude, 'skills'), 'shared', 'Both have it')
    await skill(join(homes.codex, 'skills'), 'shared', 'Both have it, differently')
    await skill(join(homes.claude, 'skills'), 'claude-only', 'Just Claude')
    await skill(join(homes.codex, 'skills', '.system'), 'builtin', 'Codex’s own')
    await mkdir(join(homes.claude, 'skills', 'not-a-skill'), { recursive: true })
    await skill(join(repo, '.agents', 'skills'), 'repo-skill', 'In the project')
    await mkdir(join(homes.claude, 'plugins'), { recursive: true })
    await writeFile(
      join(homes.claude, 'plugins', 'installed_plugins.json'),
      JSON.stringify({ plugins: { 'sp@official': [{ scope: 'user', version: '1.0' }], 'other@x': [{ scope: 'project', projectPath: '/elsewhere' }] } }),
    )
    await writeFile(join(homes.claude, 'settings.json'), JSON.stringify({ enabledPlugins: { 'sp@official': false } }))
    await writeFile(join(homes.claudeJson), JSON.stringify({ mcpServers: { gh: { command: 'gh-mcp' } } }))
    await writeFile(join(homes.codex, 'config.toml'), '[plugins."pdf@rt"]\nenabled = true\n[mcp_servers.web]\nurl = "https://x"\n')

    const out = await listExtras(homes, repo)
    expect(out.skills.map((s) => [s.scope, s.name, Object.keys(s.copies).sort(), s.differs])).toEqual([
      ['project', 'repo-skill', ['codex'], false],
      ['user', 'claude-only', ['claude_code'], false],
      ['user', 'shared', ['claude_code', 'codex'], true],
    ])
    expect(out.skills[1]?.description).toBe('Just Claude')
    expect(out.plugins.map((p) => [p.agent, p.name, p.marketplace, p.enabled])).toEqual([
      ['claude_code', 'sp', 'official', false],
      ['codex', 'pdf', 'rt', true],
    ])
    expect(out.mcp.map((m) => [m.agent, m.name, m.target])).toEqual([
      ['claude_code', 'gh', 'gh-mcp'],
      ['codex', 'web', 'https://x'],
    ])
    expect(out.dirs.project?.codex).toBe(join(repo, '.agents', 'skills'))
  })
})

describe('installing', () => {
  it('finds one skill or a folder of them', async () => {
    const root = await tmp()
    const one = await skill(root, 'one', 'x')
    await skill(root, 'two', 'y')
    expect(await skillsIn(one)).toEqual([{ name: 'one', path: one }])
    expect((await skillsIn(root)).map((s) => s.name)).toEqual(['one', 'two'])
  })

  it('copies through symlinks and never overwrites', async () => {
    const root = await tmp()
    const real = await skill(join(root, 'store'), 'linked', 'x')
    await mkdir(join(root, 'from'))
    await symlink(real, join(root, 'from', 'linked'))
    const dest = join(root, 'dest')
    expect(await installSkill(join(root, 'from', 'linked'), dest, 'linked')).toBe('installed')
    expect(await readFile(join(dest, 'linked', 'SKILL.md'), 'utf8')).toContain('name: linked')
    expect(await installSkill(real, dest, 'linked')).toBe('exists')
    await expect(installSkill(real, dest, '../escape')).rejects.toThrow()
  })
})
