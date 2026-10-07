// The Python backend: finding an interpreter that can run Watchdog, starting
// `python -m watchdog.gui.server`, and routing JSON-RPC requests and events over its stdio.

import { ChildProcessWithoutNullStreams, spawn, spawnSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { delimiter, dirname, join } from 'node:path'
import { createInterface } from 'node:readline'
import type { BackendStatus } from '@shared/api'
import { getPref } from './prefs'

type Pending = { resolve: (v: unknown) => void; reject: (e: Error) => void; method: string }

export class RpcError extends Error {
  code: string
  data: unknown
  constructor(message: string, code: string, data: unknown) {
    super(message)
    this.code = code
    this.data = data
  }
}

interface Candidate { python: string; source: string }

/** Directory holding the `watchdog` package source to run instead of the installed copy:
 * the repo checkout in development, the copy bundled into the app when packaged. */
export function bundledSource(): string | null {
  const fromEnv = process.env.WATCHDOG_SRC
  if (fromEnv && existsSync(join(fromEnv, 'watchdog', 'gui', 'server.py'))) return fromEnv
  const packaged = join(process.resourcesPath ?? '', 'python')
  if (existsSync(join(packaged, 'watchdog', 'gui', 'server.py'))) return packaged
  // Development: gui/out/main → repo/src
  const dev = join(__dirname, '..', '..', '..', 'src')
  if (existsSync(join(dev, 'watchdog', 'gui', 'server.py'))) return dev
  return null
}

function shebangPython(script: string): string | null {
  try {
    const first = readFileSync(script, 'utf8').split('\n', 1)[0]
    if (first.startsWith('#!')) {
      const interp = first.slice(2).trim().split(/\s+/)[0]
      if (interp && existsSync(interp) && !interp.endsWith('/env')) return interp
    }
  } catch {
    /* not readable */
  }
  return null
}

function which(cmd: string): string | null {
  const exts = process.platform === 'win32' ? ['.exe', '.cmd', '.bat', ''] : ['']
  for (const dir of (process.env.PATH ?? '').split(delimiter)) {
    for (const ext of exts) {
      const p = join(dir, cmd + ext)
      if (dir && existsSync(p)) return p
    }
  }
  return null
}

async function candidates(): Promise<Candidate[]> {
  const out: Candidate[] = []
  if (process.env.WATCHDOG_PYTHON) out.push({ python: process.env.WATCHDOG_PYTHON, source: 'env' })
  const chosen = await getPref<string>('pythonPath')
  if (chosen) out.push({ python: chosen, source: 'settings' })
  // pipx installs Watchdog into its own venv; the `watchdog` launcher's shebang names that python.
  const home = homedir()
  const pipxVenvs = [
    process.env.PIPX_HOME && join(process.env.PIPX_HOME, 'venvs', 'watchdog-intel'),
    join(home, '.local', 'pipx', 'venvs', 'watchdog-intel'),
    join(home, '.local', 'share', 'pipx', 'venvs', 'watchdog-intel'),
    join(home, 'pipx', 'venvs', 'watchdog-intel')
  ].filter(Boolean) as string[]
  for (const venv of pipxVenvs) {
    const py = process.platform === 'win32' ? join(venv, 'Scripts', 'python.exe') : join(venv, 'bin', 'python')
    if (existsSync(py)) out.push({ python: py, source: 'pipx' })
  }
  // GUI apps on macOS start with a minimal PATH, so also look in the usual install places.
  const extraDirs = [join(home, '.local', 'bin'), '/opt/homebrew/bin', '/usr/local/bin']
  process.env.PATH = [process.env.PATH ?? '', ...extraDirs].join(delimiter)
  const launcher = which('watchdog')
  if (launcher) {
    const py = shebangPython(launcher)
    if (py) out.push({ python: py, source: 'path' })
  }
  for (const name of process.platform === 'win32' ? ['python', 'py'] : ['python3', 'python']) {
    const p = which(name)
    if (p) out.push({ python: p, source: 'path' })
  }
  return out
}

function pythonPathEnv(src: string | null): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8', NO_COLOR: '1' }
  if (src) env.PYTHONPATH = [src, process.env.PYTHONPATH].filter(Boolean).join(delimiter)
  return env
}

/** Whether `python` can import Watchdog (and its GUI server) with `src` first on the path. */
function probe(python: string, src: string | null): { ok: boolean; message: string } {
  const r = spawnSync(python, ['-c', 'import watchdog.gui.server, watchdog.cli; print("ok")'], {
    env: pythonPathEnv(src),
    encoding: 'utf8',
    timeout: 30000
  })
  if (r.error) return { ok: false, message: r.error.message }
  if (r.status === 0 && r.stdout.includes('ok')) return { ok: true, message: '' }
  const err = (r.stderr || '').trim().split('\n').slice(-1)[0] || `exit ${r.status}`
  return { ok: false, message: err }
}

export class PythonBackend {
  private proc: ChildProcessWithoutNullStreams | null = null
  private nextId = 1
  private pending = new Map<number, Pending>()
  private stderrTail: string[] = []
  private readyWaiters: (() => void)[] = []
  status: BackendStatus = { state: 'stopped', python: null, source: null, message: null, stderrTail: [] }

  constructor(
    private onEvent: (event: string, data: unknown) => void,
    private onStatus: (s: BackendStatus) => void
  ) {}

  private setStatus(patch: Partial<BackendStatus>): void {
    this.status = { ...this.status, ...patch, stderrTail: this.stderrTail.slice(-40) }
    this.onStatus(this.status)
    if (this.status.state === 'ready') {
      this.readyWaiters.splice(0).forEach((w) => w())
    }
  }

  async start(): Promise<BackendStatus> {
    this.stop()
    this.setStatus({ state: 'starting', message: 'Looking for Watchdog…' })
    const src = bundledSource()
    const tried: string[] = []
    for (const c of await candidates()) {
      const res = probe(c.python, src)
      if (res.ok) {
        this.launch(c, src)
        return this.status
      }
      tried.push(`${c.python} (${c.source}): ${res.message}`)
    }
    this.setStatus({
      state: 'error',
      python: null,
      source: null,
      message:
        tried.length === 0
          ? 'No Python installation was found.'
          : 'None of the Python installations found can run Watchdog.\n' + tried.join('\n')
    })
    return this.status
  }

  private launch(c: Candidate, src: string | null): void {
    const proc = spawn(c.python, ['-m', 'watchdog.gui.server'], {
      env: pythonPathEnv(src),
      stdio: ['pipe', 'pipe', 'pipe']
    })
    this.proc = proc
    this.setStatus({ state: 'starting', python: c.python, source: src ? `${c.source}+src` : c.source, message: null })

    createInterface({ input: proc.stdout }).on('line', (line) => this.onLine(line))
    createInterface({ input: proc.stderr }).on('line', (line) => {
      this.stderrTail.push(line)
      if (this.stderrTail.length > 400) this.stderrTail.splice(0, 200)
      if (process.env.WATCHDOG_GUI_DEBUG) console.error('[py]', line)
    })
    proc.on('exit', (code, signal) => {
      if (this.proc !== proc) return
      this.proc = null
      for (const [, p] of this.pending) p.reject(new RpcError('The Watchdog backend stopped.', 'backend_stopped', null))
      this.pending.clear()
      this.setStatus({
        state: code === 0 ? 'stopped' : 'error',
        message: code === 0 ? null : `The Watchdog backend exited (${signal ?? `code ${code}`}).`
      })
    })
  }

  private onLine(line: string): void {
    let msg: { id?: number; result?: unknown; error?: { message: string; code: string; data: unknown }; event?: string; data?: unknown }
    try {
      msg = JSON.parse(line)
    } catch {
      return
    }
    if (msg.event) {
      if (msg.event === 'server.ready') this.setStatus({ state: 'ready', message: null })
      this.onEvent(msg.event, msg.data)
      return
    }
    if (typeof msg.id === 'number') {
      const p = this.pending.get(msg.id)
      if (!p) return
      this.pending.delete(msg.id)
      if (msg.error) p.reject(new RpcError(msg.error.message, msg.error.code, msg.error.data))
      else p.resolve(msg.result)
    }
  }

  private waitReady(timeoutMs = 60000): Promise<void> {
    if (this.status.state === 'ready') return Promise.resolve()
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new RpcError('The Watchdog backend did not start.', 'backend_timeout', null)), timeoutMs)
      this.readyWaiters.push(() => {
        clearTimeout(t)
        resolve()
      })
    })
  }

  async call(method: string, params: unknown): Promise<unknown> {
    if (this.status.state !== 'ready') {
      if (this.status.state === 'error' || this.status.state === 'stopped') {
        throw new RpcError(this.status.message ?? 'The Watchdog backend is not running.', 'backend_down', null)
      }
      await this.waitReady()
    }
    const proc = this.proc
    if (!proc) throw new RpcError('The Watchdog backend is not running.', 'backend_down', null)
    const id = this.nextId++
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject, method })
      proc.stdin.write(JSON.stringify({ id, method, params: params ?? {} }) + '\n')
    })
  }

  stop(): void {
    const proc = this.proc
    this.proc = null
    if (proc) {
      try {
        proc.stdin.end()
      } catch {
        /* already closed */
      }
      setTimeout(() => {
        if (proc.exitCode === null) proc.kill()
      }, 3000)
    }
  }
}

export function pythonDir(python: string | null): string | null {
  return python ? dirname(python) : null
}
