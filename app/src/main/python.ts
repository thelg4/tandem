import { execFile } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { resolvePython } from './agents'

/** The checkout's `src` in development; the copy bundled in Resources in a packaged app. */
export function pythonSource(appRoot: string): string | null {
  const candidates = [resolve(appRoot, '..', 'src')]
  if (process.resourcesPath) candidates.push(join(process.resourcesPath, 'python'))
  return candidates.find((dir) => existsSync(join(dir, 'tandem'))) ?? null
}

/** Environment for `python -m tandem`, with the bundled or checkout source on the path. */
export function pythonEnv(appRoot: string): NodeJS.ProcessEnv {
  // No bytecode: the bundled source lives inside the app, which must stay unmodified.
  const env: NodeJS.ProcessEnv = { ...process.env, PYTHONUNBUFFERED: '1', PYTHONDONTWRITEBYTECODE: '1' }
  const src = pythonSource(appRoot)
  if (src) env.PYTHONPATH = env.PYTHONPATH ? `${src}:${env.PYTHONPATH}` : src
  return env
}

/** Runs a one-shot `tandem <args> --format json`; null when it fails or prints no JSON. */
export function runTandemJson<T>(appRoot: string, args: string[], timeoutMs = 30_000): Promise<T | null> {
  return new Promise((done) => {
    execFile(
      resolvePython(),
      ['-m', 'tandem', ...args, '--format', 'json'],
      { env: pythonEnv(appRoot), timeout: timeoutMs, maxBuffer: 8 * 1024 * 1024 },
      (err, stdout, stderr) => {
        if (err) {
          console.error(`[tandem ${args[0]}] ${err.message}${stderr ? `\n${stderr}` : ''}`)
          done(null)
          return
        }
        try {
          done(JSON.parse(stdout) as T)
        } catch {
          done(null)
        }
      },
    )
  })
}
