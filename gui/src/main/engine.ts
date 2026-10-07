// The managed engine: a private Python environment, built by the app on first run, that holds
// Watchdog and everything it needs. A journalist never opens a terminal or installs Python.
//
//   <userData>/engine/
//     python/       Python 3.12, downloaded by uv (UV_PYTHON_INSTALL_DIR)
//     cache/        uv's download cache (UV_CACHE_DIR), so a repair does not download twice
//     venv/         the environment Watchdog runs in
//     engine.json   what was installed: the wheel's version and digest, the model results
//
// Install steps: `uv python install` -> `uv venv` -> `uv pip install <bundled wheel>` ->
// `python -m watchdog.gui.engine_setup models`. Every step is safe to repeat, so a cancelled or
// failed install resumes where it stopped. uv ships inside the app (scripts/fetch-uv.mjs) and the
// wheel is built from this repository at packaging time (scripts/build-wheel.mjs), so the engine is
// always the same version as the app. This file imports nothing from Electron, so
// scripts/engine-cli.mjs can run it headless.

import { ChildProcess, spawn, spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, statfsSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { delimiter, dirname, join } from 'node:path'
import { createInterface } from 'node:readline'
import type { EngineProgress, EngineState, EngineStatus, EngineStep } from '../shared/api'

export const PYTHON_VERSION = '3.12'
const PROGRESS_PREFIX = '\x1eWDP '
const MIN_FREE_BYTES = 7 * 1024 ** 3

const STEP_DEFS: { id: string; label: string; optional: boolean }[] = [
  { id: 'python', label: 'Python', optional: false },
  { id: 'packages', label: 'Watchdog and its libraries', optional: false },
  { id: 'docling', label: 'Document conversion (Docling)', optional: true },
  { id: 'gliner', label: 'Name detection (GLiNER)', optional: true },
  { id: 'embedding', label: 'Search embedding', optional: true },
  { id: 'reranker', label: 'Search reranker', optional: true },
  { id: 'ocr', label: 'Text recognition for scans', optional: true }
]
const MODEL_STEPS = STEP_DEFS.slice(2).map((s) => s.id)

interface EngineRecord {
  schema: 1
  wheel: string
  wheelSha256: string
  wheelVersion: string
  python: string
  installedAt: string
  /** null until the model step has run to the end (warnings included). */
  models: Record<string, 'ok' | 'warn'> | null
}

export interface EngineOptions {
  /** app.getPath('userData') */
  userData: string
  /** Packaged: process.resourcesPath. Development: the gui/resources folder. */
  resources: string
  isPackaged: boolean
  /** The repository checkout, in development; the fallback install source when no wheel is built. */
  repoRoot: string | null
  emit: (e: EngineProgress) => void
  /** Environment overrides, for tests. Defaults to process.env. */
  env?: NodeJS.ProcessEnv
}

const osName = (): string => ({ darwin: 'mac', win32: 'win' } as Record<string, string>)[process.platform] ?? 'linux'

function readJson<T>(path: string): T | null {
  try {
    return JSON.parse(readFileSync(path, 'utf8')) as T
  } catch {
    return null
  }
}

function sha256(path: string): string {
  return createHash('sha256').update(readFileSync(path)).digest('hex')
}

function wheelVersion(file: string): string {
  // watchdog_intel-1.0.3-py3-none-any.whl
  return file.split('-')[1] ?? ''
}

/** Whether version `a` is at least `b`, comparing leading numeric parts ("1.0.3" >= "1.0.2"). */
export function versionAtLeast(a: string, b: string): boolean {
  const parts = (v: string) => v.split('.').map((p) => parseInt(p, 10) || 0)
  const x = parts(a)
  const y = parts(b)
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    const d = (x[i] ?? 0) - (y[i] ?? 0)
    if (d !== 0) return d > 0
  }
  return true
}

function friendlyError(text: string): string {
  const low = text.toLowerCase()
  if (/dns|resolve|connect|network|timed out|timeout|tls|certificate|error sending request|unreachable/.test(low)) {
    return 'Watchdog could not download what it needs. Check the internet connection (a VPN or a firewall can block the downloads) and try again.'
  }
  if (/no space|disk full|enospc/.test(low)) return 'There is not enough free space on this computer. Free up some space and try again.'
  return text
}

export class Engine {
  readonly dir: string
  private opts: EngineOptions
  private env: NodeJS.ProcessEnv
  private child: ChildProcess | null = null
  private cancelled = false
  private running = false
  private runId = 0
  private state: EngineState = 'idle'
  private steps: EngineStep[] = []
  private error: string | null = null
  private log: string[] = []
  private pendingLog: string[] = []
  private flushTimer: ReturnType<typeof setTimeout> | null = null
  private simulatedDone = false
  /** Set by the caller when the backend is running on a Python that is not the managed one. */
  externalPython: string | null = null

  constructor(opts: EngineOptions) {
    this.opts = opts
    this.env = opts.env ?? process.env
    this.dir = join(opts.userData, 'engine')
    this.steps = this.freshSteps()
  }

  // ── what is on disk ─────────────────────────────────────────────────────────────

  get isDev(): boolean {
    return !this.opts.isPackaged || !!this.env.WATCHDOG_SRC
  }

  get simulate(): string | null {
    return this.env.WATCHDOG_ENGINE_SIMULATE ?? null
  }

  venvDir(): string {
    return join(this.dir, 'venv')
  }

  venvPython(): string {
    return process.platform === 'win32' ? join(this.venvDir(), 'Scripts', 'python.exe') : join(this.venvDir(), 'bin', 'python')
  }

  /** The venv's bin directory (where the `watchdog` command lives). */
  venvBin(): string {
    return dirname(this.venvPython())
  }

  private record(): EngineRecord | null {
    return readJson<EngineRecord>(join(this.dir, 'engine.json'))
  }

  private writeRecord(r: EngineRecord): void {
    mkdirSync(this.dir, { recursive: true })
    const tmp = join(this.dir, 'engine.json.tmp')
    writeFileSync(tmp, JSON.stringify(r, null, 2))
    renameSync(tmp, join(this.dir, 'engine.json'))
  }

  uvPath(): string | null {
    const exe = process.platform === 'win32' ? 'uv.exe' : 'uv'
    const arch = process.arch === 'arm64' ? 'arm64' : 'x64'
    const candidates = this.opts.isPackaged
      ? [join(this.opts.resources, 'bin', exe)]
      : [join(this.opts.resources, 'bin', `${osName()}-${arch}`, exe)]
    for (const c of candidates) if (existsSync(c)) return c
    if (!this.opts.isPackaged) {
      // Development without a downloaded uv: any uv on the PATH will do.
      const dirs = [...(this.env.PATH ?? '').split(delimiter), join(homedir(), '.local', 'bin'), join(homedir(), '.cargo', 'bin'), '/opt/homebrew/bin', '/usr/local/bin']
      for (const d of dirs) {
        const p = join(d, exe)
        if (d && existsSync(p)) return p
      }
    }
    return null
  }

  /** The bundled wheel, or null. */
  wheelPath(): string | null {
    const dir = join(this.opts.resources, 'python-wheel')
    try {
      const f = readdirSync(dir).find((n) => n.endsWith('.whl'))
      return f ? join(dir, f) : null
    } catch {
      return null
    }
  }

  bundledVersion(): string | null {
    const w = this.wheelPath()
    return w ? wheelVersion(w.split(/[\\/]/).pop()!) : null
  }

  /** Whether an install can be attempted from this app: uv, plus a wheel or (in development) the repository. */
  canInstall(): boolean {
    if (this.simulate) return true
    return !!this.uvPath() && (!!this.wheelPath() || (this.isDev && !!this.opts.repoRoot))
  }

  private venvWorks(): boolean {
    const py = this.venvPython()
    if (!existsSync(py)) return false
    const r = spawnSync(py, ['-c', 'import sys; print(sys.version_info[:2] >= (3, 10))'], { encoding: 'utf8', timeout: 30000 })
    return r.status === 0 && r.stdout.trim() === 'True'
  }

  private packagesCurrent(): boolean {
    const rec = this.record()
    const wheel = this.wheelPath()
    if (!rec || !wheel) return !!rec && !wheel // nothing to compare against: whatever is installed stands
    return rec.wheelSha256 === sha256(wheel)
  }

  /** Python of the managed engine when it is installed (current or not), else null. */
  managedPython(): string | null {
    if (this.simulate) return null
    return this.record() && existsSync(this.venvPython()) ? this.venvPython() : null
  }

  /** installed and matching the app; 'outdated' when the bundled wheel differs from what is installed. */
  engineState(): 'missing' | 'outdated' | 'ready' {
    if (this.simulate) return this.simulatedDone ? 'ready' : 'missing'
    const rec = this.record()
    if (!rec || !existsSync(this.venvPython())) return 'missing'
    return this.packagesCurrent() ? 'ready' : 'outdated'
  }

  status(): EngineStatus {
    const rec = this.record()
    const es = this.engineState()
    return {
      state: this.running ? 'installing' : this.state === 'failed' || this.state === 'cancelled' ? this.state : es,
      engine: es,
      dir: this.dir,
      installedVersion: rec?.wheelVersion ?? null,
      bundledVersion: this.bundledVersion(),
      modelsDone: this.simulate ? this.simulatedDone : !!rec?.models,
      modelResults: rec?.models ?? null,
      canInstall: this.canInstall(),
      usingExternal: this.externalPython,
      forceOnboarding: this.env.WATCHDOG_FORCE_ONBOARDING ?? null,
      simulated: !!this.simulate,
      setupConfigExists: existsSync(join(homedir(), '.watchdog', 'config.json')),
      run: { id: this.runId, state: this.state, steps: this.steps, log: this.log.slice(-400), error: this.error }
    }
  }

  // ── installing ──────────────────────────────────────────────────────────────────

  private freshSteps(): EngineStep[] {
    return STEP_DEFS.map((d) => ({ ...d, state: 'pending', detail: null }))
  }

  private step(id: string): EngineStep {
    return this.steps.find((s) => s.id === id)!
  }

  private setStep(id: string, state: EngineStep['state'], detail?: string | null): void {
    const s = this.step(id)
    s.state = state
    if (detail !== undefined) s.detail = detail
    this.publish()
  }

  private publish(): void {
    const log = this.pendingLog.splice(0)
    this.opts.emit({ id: this.runId, state: this.state, steps: this.steps.map((s) => ({ ...s })), log, error: this.error })
  }

  private addLog(line: string): void {
    const clean = line.replace(/\x1b\[[0-9;]*[A-Za-z]/g, '').trimEnd()
    if (!clean) return
    this.log.push(clean)
    if (this.log.length > 600) this.log.splice(0, 200)
    this.pendingLog.push(clean)
    if (!this.flushTimer) {
      this.flushTimer = setTimeout(() => {
        this.flushTimer = null
        this.publish()
      }, 200)
    }
  }

  private uvEnv(): NodeJS.ProcessEnv {
    const env: NodeJS.ProcessEnv = { ...this.env }
    for (const k of Object.keys(env)) if (k.startsWith('UV_') || k === 'VIRTUAL_ENV' || k === 'PYTHONPATH' || k === 'PYTHONHOME') delete env[k]
    Object.assign(env, {
      UV_PYTHON_INSTALL_DIR: join(this.dir, 'python'),
      UV_PYTHON_BIN_DIR: join(this.dir, 'bin'),
      UV_TOOL_DIR: join(this.dir, 'tools'),
      UV_CACHE_DIR: join(this.dir, 'cache'),
      UV_PYTHON_PREFERENCE: 'only-managed',
      UV_PYTHON_DOWNLOADS: 'automatic',
      // Offices that inspect TLS install their own root certificate in the operating system.
      UV_SYSTEM_CERTS: '1',
      UV_NO_CONFIG: '1',
      UV_NO_PROGRESS: '1',
      NO_COLOR: '1'
    })
    return env
  }

  private run(cmd: string, args: string[], env: NodeJS.ProcessEnv, onLine?: (line: string) => void): Promise<number> {
    return new Promise((resolve, reject) => {
      this.addLog(`$ ${[cmd, ...args].map((a) => (/\s/.test(a) ? JSON.stringify(a) : a)).join(' ')}`)
      const child = spawn(cmd, args, { env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true })
      this.child = child
      const handle = (line: string) => {
        if (onLine) onLine(line)
        else this.addLog(line)
      }
      createInterface({ input: child.stdout! }).on('line', handle)
      createInterface({ input: child.stderr! }).on('line', (l) => this.addLog(l))
      child.on('error', (e) => reject(e))
      child.on('close', (code) => {
        if (this.child === child) this.child = null
        resolve(code ?? 1)
      })
    })
  }

  private async runOrThrow(label: string, cmd: string, args: string[], env: NodeJS.ProcessEnv, step: string): Promise<void> {
    const code = await this.run(cmd, args, env, (line) => {
      this.addLog(line)
      this.setStep(step, 'running', line.replace(/^\s*[-+]?\s*/, '').slice(0, 140))
    })
    if (this.cancelled) throw new Error('cancelled')
    if (code !== 0) {
      const tail = this.log.slice(-6).join('\n')
      throw new Error(`${label} failed (exit ${code}).\n${tail}`)
    }
  }

  cancel(): void {
    if (!this.running) return
    this.cancelled = true
    const child = this.child
    if (child?.pid) {
      if (process.platform === 'win32') spawnSync('taskkill', ['/pid', String(child.pid), '/T', '/F'])
      else child.kill('SIGTERM')
    }
  }

  isRunning(): boolean {
    return this.running
  }

  /**
   * Install or repair. Skips whatever is already in place. `external` is the Python the backend
   * already runs on when it is not a managed one: then only the models are fetched, into that
   * environment. `fresh` throws the environment away first (repair or reinstall).
   */
  async install(opts: { external?: string | null; fresh?: boolean } = {}): Promise<EngineState> {
    if (this.running) return this.state
    this.running = true
    this.cancelled = false
    this.runId += 1
    this.state = 'running'
    this.error = null
    this.log = []
    this.pendingLog = []
    this.steps = this.freshSteps()
    this.publish()
    try {
      if (this.simulate) await this.simulateRun()
      else await this.realRun(opts)
      this.state = 'done'
    } catch (e) {
      const msg = (e as Error).message
      if (this.cancelled || msg === 'cancelled') {
        this.state = 'cancelled'
        this.error = 'The installation was cancelled. You can start it again; what was already downloaded is kept.'
      } else {
        this.state = 'failed'
        this.error = friendlyError(msg)
        this.addLog(`error: ${msg}`)
        const running = this.steps.find((s) => s.state === 'running')
        if (running) running.state = 'failed'
      }
    } finally {
      this.running = false
      if (this.flushTimer) {
        clearTimeout(this.flushTimer)
        this.flushTimer = null
      }
      this.publish()
    }
    return this.state
  }

  private async realRun(opts: { external?: string | null; fresh?: boolean }): Promise<void> {
    mkdirSync(this.dir, { recursive: true })
    let python: string
    if (opts.external) {
      python = opts.external
      this.setStep('python', 'skipped', 'Already on this computer')
      this.setStep('packages', 'skipped', 'Watchdog is already installed')
    } else {
      const uv = this.uvPath()
      if (!uv) throw new Error('The app is missing its installer (uv). Reinstall the app.')
      const wheel = this.wheelPath()
      const source = wheel ?? (this.isDev ? this.opts.repoRoot : null)
      if (!source) throw new Error('The app is missing the Watchdog package. Reinstall the app.')
      const env = this.uvEnv()
      try {
        const free = statfsSync(this.dir)
        if (free.bavail * free.bsize < MIN_FREE_BYTES) {
          throw new Error('There is not enough free space on this computer: Watchdog needs about 7 GB while it installs.')
        }
      } catch (e) {
        if ((e as Error).message.startsWith('There is not')) throw e
      }

      // 1. Python and the environment
      if (opts.fresh) {
        this.addLog('Removing the existing environment')
        rmSync(this.venvDir(), { recursive: true, force: true })
        rmSync(join(this.dir, 'engine.json'), { force: true })
      }
      this.setStep('python', 'running', 'Downloading Python')
      if (!this.venvWorks()) {
        rmSync(this.venvDir(), { recursive: true, force: true })
        await this.runOrThrow('Installing Python', uv, ['python', 'install', PYTHON_VERSION, '--no-bin'], env, 'python')
        await this.runOrThrow('Creating the environment', uv, ['venv', this.venvDir(), '--python', PYTHON_VERSION, '--seed'], env, 'python')
      }
      python = this.venvPython()
      this.setStep('python', 'done', `Python ${PYTHON_VERSION}`)

      // 2. Watchdog and its libraries
      const rec = this.record()
      const current = !!rec && (wheel ? rec.wheelSha256 === sha256(wheel) : true)
      if (current) {
        this.setStep('packages', 'done', `Version ${rec!.wheelVersion}`)
      } else {
        this.setStep('packages', 'running', 'Downloading libraries')
        const args = ['pip', 'install', '--python', python]
        // PyPI's Linux torch wheels pull in about 2.5 GB of NVIDIA libraries Watchdog never uses
        // (it converts documents on the CPU), so Linux on x86 takes PyTorch's CPU build instead.
        if (process.platform === 'linux' && process.arch === 'x64') {
          args.push('--extra-index-url', 'https://download.pytorch.org/whl/cpu', '--index-strategy', 'unsafe-best-match')
        }
        if (rec) args.push('--reinstall-package', 'watchdog-intel')
        args.push(source)
        await this.runOrThrow('Installing Watchdog', uv, args, env, 'packages')
        const version = wheel ? wheelVersion(wheel.split(/[\\/]/).pop()!) : this.pythonPackageVersion(python)
        this.writeRecord({
          schema: 1,
          wheel: wheel ? wheel.split(/[\\/]/).pop()! : 'source',
          wheelSha256: wheel ? sha256(wheel) : 'source',
          wheelVersion: version,
          python: PYTHON_VERSION,
          installedAt: new Date().toISOString(),
          models: null
        })
        this.setStep('packages', 'done', `Version ${version}`)
      }
    }

    // 3. Local models
    const modelEnv: NodeJS.ProcessEnv = { ...this.env, WATCHDOG_PROGRESS: '1', PYTHONUNBUFFERED: '1', PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', NO_COLOR: '1' }
    for (const k of ['PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV']) delete modelEnv[k]
    const uv = this.uvPath()
    if (uv) modelEnv.WATCHDOG_UV = uv
    if (opts.external && this.isDev) {
      // A development checkout's Python runs the repository's source, as the backend does.
      const src = join(this.opts.repoRoot ?? '', 'src')
      if (this.opts.repoRoot && existsSync(src)) modelEnv.PYTHONPATH = src
    }
    for (const id of MODEL_STEPS) this.setStep(id, 'pending')
    const results: Record<string, 'ok' | 'warn'> = {}
    const code = await this.run(python, ['-m', 'watchdog.gui.engine_setup', 'models'], modelEnv, (line) => {
      if (line.startsWith(PROGRESS_PREFIX)) {
        try {
          const ev = JSON.parse(line.slice(PROGRESS_PREFIX.length)) as { kind: string; step: string; state: string; detail: string | null }
          if (ev.kind !== 'engine' || !MODEL_STEPS.includes(ev.step)) return
          if (ev.state === 'start') this.setStep(ev.step, 'running', 'Downloading')
          else if (ev.state === 'ok') {
            results[ev.step] = 'ok'
            this.setStep(ev.step, ev.detail?.startsWith('skipped') ? 'skipped' : 'done', ev.detail?.replace(/^skipped: /, '') ?? null)
          } else if (ev.state === 'warn') {
            results[ev.step] = 'warn'
            this.setStep(ev.step, 'warning', ev.detail)
            this.addLog(`warning: ${ev.step}: ${ev.detail}`)
          }
        } catch {
          /* not a progress line */
        }
        return
      }
      this.addLog(line)
    })
    if (this.cancelled) throw new Error('cancelled')
    if (code !== 0) {
      // The model step never aborts on a failed download, so a non-zero exit means the environment
      // itself is broken.
      throw new Error(`The model step could not start (exit ${code}).\n${this.log.slice(-6).join('\n')}`)
    }
    for (const id of MODEL_STEPS) if (this.step(id).state === 'pending' || this.step(id).state === 'running') this.setStep(id, 'warning', 'No result')
    const rec = this.record()
    if (rec) this.writeRecord({ ...rec, models: results })
  }

  private pythonPackageVersion(python: string): string {
    const r = spawnSync(python, ['-c', 'import watchdog; print(watchdog.__version__)'], { encoding: 'utf8', timeout: 30000 })
    return r.status === 0 ? r.stdout.trim() : 'unknown'
  }

  /** Remove the managed engine entirely (keeps nothing). */
  async remove(): Promise<void> {
    rmSync(this.dir, { recursive: true, force: true })
    this.state = 'idle'
    this.steps = this.freshSteps()
  }

  // ── development stand-in ────────────────────────────────────────────────────────
  // WATCHDOG_ENGINE_SIMULATE=1 plays a timed install without downloading anything, so the
  // onboarding screens can be exercised and photographed. "fail:<step>" fails at that step the
  // first time; "slow" doubles the pace.

  private async simulateRun(): Promise<void> {
    const failAt = this.simulate?.startsWith('fail:') ? this.simulate.slice(5) : null
    const pause = (ms: number) =>
      new Promise<void>((resolve, reject) => {
        const t = setTimeout(resolve, ms)
        const poll = setInterval(() => {
          if (this.cancelled) {
            clearTimeout(t)
            clearInterval(poll)
            reject(new Error('cancelled'))
          }
        }, 100)
        setTimeout(() => clearInterval(poll), ms + 50)
      })
    const lines: Record<string, string[]> = {
      python: ['Installed Python 3.12.13 in 2.1s', 'Creating virtual environment at: venv'],
      packages: ['Resolved 135 packages in 3.5s', 'Downloading torch (188.5MiB)', 'Downloading onnxruntime (17.2MiB)', 'Prepared 134 packages in 41s', 'Installed 135 packages in 219ms']
    }
    for (const def of STEP_DEFS) {
      const id = def.id
      this.setStep(id, 'running', 'Downloading')
      for (const l of lines[id] ?? [`Fetching ${def.label}`]) {
        this.addLog(l)
        await pause(this.simulate === 'slow' ? 1400 : 600)
      }
      if (failAt === id && !this.simulatedFailed) {
        this.simulatedFailed = true
        if (def.optional) {
          this.setStep(id, 'warning', 'Could not download. It will be fetched when first needed.')
          continue
        }
        throw new Error('Watchdog could not download what it needs. Check the internet connection and try again.')
      }
      this.setStep(id, 'done', null)
    }
    this.simulatedDone = true
  }
  private simulatedFailed = false
}
