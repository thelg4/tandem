import { open, realpath, stat } from 'node:fs/promises'
import { isAbsolute, join, relative, sep } from 'node:path'
import type { FileContents } from '../shared/protocol'

export const MAX_FILE_BYTES = 2 * 1024 * 1024

/**
 * Read a file inside the repository for display. The path is relative to the repo and
 * resolved through symlinks; anything that lands outside the repository is refused.
 */
export async function readRepoFile(repo: string, rel: string): Promise<FileContents> {
  if (!rel || isAbsolute(rel) || rel.split(/[\\/]/).includes('..')) return { kind: 'missing' }
  let real: string
  try {
    const top = await realpath(repo)
    real = await realpath(join(top, rel))
    const inside = relative(top, real)
    if (!inside || inside.startsWith(`..${sep}`) || inside === '..' || isAbsolute(inside)) return { kind: 'missing' }
  } catch {
    return { kind: 'missing' }
  }
  const info = await stat(real)
  if (!info.isFile()) return { kind: 'missing' }
  if (info.size > MAX_FILE_BYTES) return { kind: 'too_large', size: info.size }
  const fh = await open(real, 'r')
  try {
    const buf = Buffer.alloc(info.size)
    await fh.read(buf, 0, info.size, 0)
    if (buf.subarray(0, 8192).includes(0)) return { kind: 'binary', size: info.size }
    return { kind: 'text', text: buf.toString('utf8'), size: info.size }
  } finally {
    await fh.close()
  }
}
