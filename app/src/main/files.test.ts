import { mkdirSync, mkdtempSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { MAX_FILE_BYTES, readRepoFile } from './files'

function repo(): string {
  const root = mkdtempSync(join(tmpdir(), 'tandem-files-'))
  mkdirSync(join(root, 'repo', 'src'), { recursive: true })
  writeFileSync(join(root, 'repo', 'src', 'a.ts'), 'export const a = 1\n')
  writeFileSync(join(root, 'secret.txt'), 'outside')
  return root
}

describe('readRepoFile', () => {
  it('reads text inside the repository', async () => {
    const root = repo()
    expect(await readRepoFile(join(root, 'repo'), 'src/a.ts')).toEqual({ kind: 'text', text: 'export const a = 1\n', size: 19 })
  })

  it('refuses paths and links that leave the repository', async () => {
    const root = repo()
    symlinkSync(join(root, 'secret.txt'), join(root, 'repo', 'link.txt'))
    for (const rel of ['../secret.txt', join(root, 'secret.txt'), 'link.txt', 'src', 'nope.ts', '']) {
      expect(await readRepoFile(join(root, 'repo'), rel)).toEqual({ kind: 'missing' })
    }
  })

  it('flags binary and oversized files without returning their bytes', async () => {
    const root = repo()
    writeFileSync(join(root, 'repo', 'b.bin'), Buffer.from([1, 0, 2]))
    writeFileSync(join(root, 'repo', 'big.txt'), Buffer.alloc(MAX_FILE_BYTES + 1, 97))
    expect(await readRepoFile(join(root, 'repo'), 'b.bin')).toEqual({ kind: 'binary', size: 3 })
    expect(await readRepoFile(join(root, 'repo'), 'big.txt')).toEqual({ kind: 'too_large', size: MAX_FILE_BYTES + 1 })
  })
})
