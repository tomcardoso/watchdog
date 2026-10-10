// The Python backend: finding an interpreter that can run Watchdog, starting
// `python -m watchdog.gui.server`, and routing JSON-RPC requests and events over its stdio.

import { app } from 'electron'
import log from 'electron-log/main'
import { ChildProcessWithoutNullStreams, spawn, spawnSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { delimiter, dirname, join } from 'node:path'
import { createInterface } from 'node:readline'
import type { BackendStatus } from '@shared/api'
import { getPref } from './prefs'
import { accessFile } from './access'
import { Engine, PENDING_ENV, versionAtLeast } from './engine'

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

interface Candidate { python: string; source: string; external?: boolean }

/** Directory holding the `watchdog` package source to run instead of an installed copy: the repo
 * checkout, in development only. A packaged app runs the managed engine, which is the matching
 * version already (the wheel is built from the same source), so it overlays nothing. */
export function bundledSource(): string | null {
  const fromEnv = process.env.WATCHDOG_SRC
  if (fromEnv && existsSync(join(fromEnv, 'watchdog', 'gui', 'server.py'))) return fromEnv
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

async function candidates(engine: Engine): Promise<Candidate[]> {
  const out: Candidate[] = []
  if (process.env.WATCHDOG_PYTHON) out.push({ python: process.env.WATCHDOG_PYTHON, source: 'env', external: true })
  const chosen = await getPref<string>('pythonPath')
  if (chosen) out.push({ python: chosen, source: 'settings', external: true })
  // The managed engine comes first in a packaged app. A development checkout keeps its own Python
  // and uses the managed engine only as the last resort.
  const managed = engine.managedPython()
  if (managed && !engine.isDev && engine.engineState() === 'ready') out.push({ python: managed, source: 'managed' })
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
    if (existsSync(py)) out.push({ python: py, source: 'pipx', external: true })
  }
  // GUI apps on macOS start with a minimal PATH, so also look in the usual install places.
  const extraDirs = [join(home, '.local', 'bin'), '/opt/homebrew/bin', '/usr/local/bin']
  process.env.PATH = [process.env.PATH ?? '', ...extraDirs].join(delimiter)
  const launcher = which('watchdog')
  if (launcher) {
    const py = shebangPython(launcher)
    if (py) out.push({ python: py, source: 'path', external: true })
  }
  for (const name of process.platform === 'win32' ? ['python', 'py'] : ['python3', 'python']) {
    const p = which(name)
    if (p) out.push({ python: p, source: 'path', external: true })
  }
  if (managed && engine.isDev) out.push({ python: managed, source: 'managed' })
  return out
}

function pythonPathEnv(src: string | null, engine?: Engine, managed = false): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8', NO_COLOR: '1' }
  delete env[PENDING_ENV]
  // While the engine's background phase is unfinished the backend refuses to add documents
  // (D272). The simulated engine sets it too, so the gated screens can be exercised.
  if (engine && (managed || engine.simulate) && !engine.complete()) env[PENDING_ENV] = '1'
  if (src) env.PYTHONPATH = [src, process.env.PYTHONPATH].filter(Boolean).join(delimiter)
  // Folder access (src/watchdog/access.py): the backend, every job and every command a Claude
  // session runs may change files only in folders the user has allowed. The engine and the app's
  // own data folder stay writable. WATCHDOG_ENFORCE_ACCESS=0 turns it off, for development only.
  if (process.env.WATCHDOG_ENFORCE_ACCESS !== '0') {
    env.WATCHDOG_ENFORCE_ACCESS = '1'
    env.WATCHDOG_ACCESS_FILE = accessFile()
    env.WATCHDOG_EXTRA_WRITE_ROOTS = [engine?.dir, app.getPath('userData'), process.env.WATCHDOG_EXTRA_WRITE_ROOTS]
      .filter(Boolean)
      .join(delimiter)
  } else {
    delete env.WATCHDOG_ENFORCE_ACCESS
  }
  if (engine) {
    const uv = engine.uvPath()
    if (uv) env.WATCHDOG_UV = uv
    // Commands the app runs by name (`watchdog add …`) resolve to the managed engine.
    if (managed) env.PATH = [engine.venvBin(), process.env.PATH ?? ''].join(delimiter)
  }
  return env
}

/** Whether `python` can import Watchdog (and its GUI server) with `src` first on the path. */
function probe(python: string, src: string | null): { ok: boolean; message: string; version: string } {
  const r = spawnSync(python, ['-c', 'import watchdog.gui.server, watchdog.cli, watchdog; print("ok", watchdog.__version__)'], {
    env: pythonPathEnv(src),
    encoding: 'utf8',
    timeout: 30000
  })
  if (r.error) return { ok: false, message: r.error.message, version: '' }
  if (r.status === 0 && r.stdout.includes('ok')) return { ok: true, message: '', version: r.stdout.trim().split(/\s+/)[1] ?? '' }
  const err = (r.stderr || '').trim().split('\n').slice(-1)[0] || `exit ${r.status}`
  return { ok: false, message: err, version: '' }
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
    private onStatus: (s: BackendStatus) => void,
    private engine: Engine,
    /** Run once a backend has started, before anything else may call it: hands it the stored keys
     * (secrets.ts, D295). A failure is logged and the backend is used anyway. */
    private beforeReady?: (request: (method: string, params: unknown) => Promise<unknown>) => Promise<void>
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
    this.setStatus({ state: 'starting', message: 'Looking for Watchdog…', needsEngine: false })
    const tried: string[] = []
    const bundled = this.engine.bundledVersion()
    for (const c of await candidates(this.engine)) {
      // Only the development checkout's source is laid over a Python; the managed engine needs none.
      const src = c.source === 'managed' ? null : bundledSource()
      const res = probe(c.python, src)
      if (res.ok && c.external && !this.engine.isDev && !src && bundled && res.version && !versionAtLeast(res.version, bundled)) {
        tried.push(`${c.python} (${c.source}): Watchdog ${res.version} is older than the version this app needs (${bundled})`)
        continue
      }
      if (res.ok) {
        this.engine.externalPython = c.source === 'managed' || this.engine.simulate ? null : c.python
        this.launch(c, src)
        return this.status
      }
      tried.push(`${c.python} (${c.source}): ${res.message}`)
    }
    this.engine.externalPython = null
    this.setStatus({
      state: 'error',
      needsEngine: this.engine.canInstall(),
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
      env: pythonPathEnv(src, this.engine, c.source === 'managed'),
      stdio: ['pipe', 'pipe', 'pipe']
    })
    this.proc = proc
    this.setStatus({ state: 'starting', python: c.python, source: src ? `${c.source}+src` : c.source, message: null })

    createInterface({ input: proc.stdout }).on('line', (line) => this.onLine(line))
    createInterface({ input: proc.stderr }).on('line', (line) => {
      this.stderrTail.push(line)
      if (this.stderrTail.length > 400) this.stderrTail.splice(0, 200)
      // Kept in the app's log file too (Help → Show Log File), so a backend failure a user reports
      // can be diagnosed after the window has closed.
      log.info('[py]', line)
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
      if (msg.event === 'server.ready') void this.ready()
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

  private async ready(): Promise<void> {
    const proc = this.proc
    if (this.beforeReady && proc) {
      try {
        await this.beforeReady((method, params) => this.request(proc, method, params))
      } catch (e) {
        log.warn('backend start-up step failed:', (e as Error)?.message)
      }
    }
    if (this.proc === proc) this.setStatus({ state: 'ready', message: null })
  }

  waitReady(timeoutMs = 60000): Promise<void> {
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
    return this.request(proc, method, params)
  }

  private request(proc: ChildProcessWithoutNullStreams, method: string, params: unknown): Promise<unknown> {
    const id = this.nextId++
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject, method })
      proc.stdin.write(JSON.stringify({ id, method, params: params ?? {} }) + '\n')
    })
  }

  /** Tell a running backend that the engine's background phase has finished, so it accepts
   * commands that add documents (engine.setReady). A backend that is not running reads the state
   * afresh when it next starts. */
  async engineReady(): Promise<void> {
    if (this.status.state !== 'ready') return
    try {
      await this.call('engine.setReady', {})
    } catch (e) {
      log.warn('engine.setReady failed; restarting the backend', e)
      await this.start()
    }
  }

  /** Stop the backend and wait for it to exit, so its files can be replaced (an engine repair).
   * The app shows the engine screen while it is suspended. */
  async suspend(reason: string): Promise<void> {
    const proc = this.proc
    this.stop()
    if (proc && proc.exitCode === null) {
      await new Promise<void>((resolve) => {
        const t = setTimeout(() => {
          proc.kill()
          resolve()
        }, 4000)
        proc.once('exit', () => {
          clearTimeout(t)
          resolve()
        })
      })
    }
    for (const [, p] of this.pending) p.reject(new RpcError('The Watchdog backend stopped.', 'backend_stopped', null))
    this.pending.clear()
    this.setStatus({ state: 'error', python: null, source: null, message: reason, needsEngine: true })
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
