// The managed engine: a private Python environment, built by the app on first run, that holds
// Watchdog and everything it needs. A journalist never opens a terminal or installs Python.
//
//   <userData>/engine/
//     python/            Python 3.12, downloaded by uv (UV_PYTHON_INSTALL_DIR)
//     cache/             uv's download cache (UV_CACHE_DIR), so a repair does not download twice
//     venv/              the environment Watchdog runs in
//     requirements.in    the one requirement: the bundled wheel
//     requirements.lock  every library, resolved once for both phases (`uv pip compile`)
//     core.txt           phase 1's requirements (`engine_setup core-requirements`)
//     engine.json        what was installed: the wheel's version and digest, whether phase 2's
//                        libraries are in, the model results
//
// The install runs in two phases (D272), so the app is usable within a minute or so:
//
//   Phase 1, which first-run setup waits for: `uv python install` -> `uv venv` -> `uv pip compile`
//   (the full resolution, written to requirements.lock) -> the wheel with `--no-deps` -> the
//   light libraries the backend needs to start, read investigations, change settings and sign in,
//   pinned to the lock. The caller restarts the backend at this point (`onCore`).
//
//   Phase 2, in the background while the person works: every library in the lock (torch,
//   docling, fastembed, gliner…), then `python -m watchdog.gui.engine_setup models`. Because both
//   phases install from one resolution, phase 2 only adds packages beside the ones the running
//   backend has loaded; it never replaces one. Until phase 2 has finished the backend runs with
//   WATCHDOG_ENGINE_PENDING=1, and it refuses to add documents (`engine_not_ready`).
//
// Every step is safe to repeat, so a cancelled or failed install, or one interrupted by quitting
// the app, resumes where it stopped: the app starts phase 2 again at the next launch. uv ships
// inside the app (scripts/fetch-uv.mjs) and the wheel is built from this repository at packaging
// time (scripts/build-wheel.mjs), so the engine is always the same version as the app. This file
// imports nothing from Electron, so scripts/engine-cli.mjs can run it headless.

import { ChildProcess, spawn, spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, statfsSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { delimiter, dirname, join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { createInterface } from 'node:readline'
import type { EngineProgress, EngineState, EngineStatus, EngineStep } from '../shared/api'

export const PYTHON_VERSION = '3.12'
const PROGRESS_PREFIX = '\x1eWDP '
const MIN_FREE_BYTES = 8 * 1024 ** 3
/** The environment variable the backend reads while phase 2 is incomplete (engine_setup.PENDING_ENV). */
export const PENDING_ENV = 'WATCHDOG_ENGINE_PENDING'

const STEP_DEFS: { id: string; label: string; optional: boolean; phase: 1 | 2 }[] = [
  { id: 'python', label: 'Python', optional: false, phase: 1 },
  { id: 'packages', label: 'Watchdog', optional: false, phase: 1 },
  { id: 'libraries', label: 'Document and search libraries', optional: false, phase: 2 },
  { id: 'docling', label: 'Document conversion (Docling)', optional: true, phase: 2 },
  { id: 'gliner', label: 'Name detection (GLiNER)', optional: true, phase: 2 },
  { id: 'embedding', label: 'Search embedding', optional: true, phase: 2 },
  { id: 'reranker', label: 'Search reranker', optional: true, phase: 2 },
  { id: 'ocr', label: 'Text recognition for scans', optional: true, phase: 2 }
]
const MODEL_STEPS = STEP_DEFS.filter((s) => s.phase === 2 && s.id !== 'libraries').map((s) => s.id)

/**
 * engine.json's format. A record with any other value (an older app's, or a newer app's after a
 * downgrade) is not interpreted: the environment is rebuilt from scratch, which costs a download
 * but can never leave a half-understood engine in place.
 */
export const RECORD_SCHEMA = 2
/**
 * The install phases this app defines, in order. A record lists the ones finished for its wheel;
 * the engine is complete when every phase named here is listed and the model step has run. A
 * later app that adds, splits or renames a phase finds it missing from the list and runs it, and
 * a new wheel (every update brings one) rewrites the list from the first phase, so no change to the
 * phases can strand an engine that looks finished but is not.
 */
export const PHASES = ['core', 'libraries'] as const

interface EngineRecord {
  schema: typeof RECORD_SCHEMA
  wheel: string
  wheelSha256: string
  wheelVersion: string
  python: string
  installedAt: string
  /** The PHASES finished for this wheel. */
  phases: string[]
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
  /** process.platform, for tests. */
  platform?: NodeJS.Platform
}

export interface InstallOptions {
  /** The Python the backend already runs on when it is not a managed one: only the models are fetched. */
  external?: string | null
  /** Throw the environment away first (repair or reinstall). */
  fresh?: boolean
  /** Called once phase 1 is in place, before phase 2 starts: the moment to (re)start the backend. */
  onCore?: () => Promise<void> | void
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

/**
 * uv arguments that choose PyTorch's build. PyPI's Linux torch wheels pull in several GB of
 * NVIDIA libraries Watchdog never uses (it converts documents on the CPU): measured on Linux x64,
 * a 6.2 GB environment against 1.8 GB with the CPU build. `--torch-backend cpu` takes only the
 * PyTorch-ecosystem packages from PyTorch's CPU index, everything else from PyPI. Windows' PyPI
 * build is already CPU-only, so the flag changes nothing there but keeps the platforms alike. macOS
 * is left to PyPI, whose wheels are the CPU (and Apple GPU) build.
 */
export function torchArgs(platform: NodeJS.Platform = process.platform): string[] {
  return platform === 'darwin' ? [] : ['--torch-backend', 'cpu']
}

function friendlyError(text: string): string {
  const low = text.toLowerCase()
  if (/dns|resolve|connect|network|timed out|timeout|tls|certificate|error sending request|unreachable/.test(low)) {
    return 'Watchdog could not download what it needs. Check the internet connection (a VPN or a firewall can block the downloads) and try again.'
  }
  if (/no space|disk full|enospc/.test(low)) return 'There is not enough free space on this computer. Free up some space and try again.'
  return text
}

/** WATCHDOG_ENGINE_SIMULATE, parsed. "1", "slow", "fail:<step>", "slow-phase2" (phase 2 takes a
 * few minutes, for screenshots of the background indicator) and "resume" (phase 1 counts as
 * already installed, so the app opens straight away and finishes phase 2 in the background) can be
 * combined with commas: "resume,slow-phase2". */
export function parseSimulate(value: string | null | undefined): { slow: boolean; slowPhase2: boolean; resume: boolean; failAt: string | null } | null {
  if (!value) return null
  const parts = value.split(',').map((p) => p.trim())
  const fail = parts.find((p) => p.startsWith('fail:'))
  return { slow: parts.includes('slow'), slowPhase2: parts.includes('slow-phase2'), resume: parts.includes('resume'), failAt: fail ? fail.slice(5) : null }
}

export class Engine {
  readonly dir: string
  private opts: EngineOptions
  private env: NodeJS.ProcessEnv
  private platform: NodeJS.Platform
  private child: ChildProcess | null = null
  private cancelled = false
  private running = false
  private runId = 0
  private state: EngineState = 'idle'
  private phase: 1 | 2 | null = null
  private steps: EngineStep[] = []
  private error: string | null = null
  private log: string[] = []
  private pendingLog: string[] = []
  private flushTimer: ReturnType<typeof setTimeout> | null = null
  private simulatedCore = false
  private simulatedDone = false
  private simulatedFailed = false
  /** Set by the caller when the backend is running on a Python that is not the managed one. */
  externalPython: string | null = null

  constructor(opts: EngineOptions) {
    this.opts = opts
    this.env = opts.env ?? process.env
    this.platform = opts.platform ?? process.platform
    this.dir = join(opts.userData, 'engine')
    this.steps = this.freshSteps()
    this.simulatedCore = !!this.sim?.resume
  }

  // ── what is on disk ─────────────────────────────────────────────────────────────

  get isDev(): boolean {
    return !this.opts.isPackaged || !!this.env.WATCHDOG_SRC
  }

  get simulate(): string | null {
    return this.env.WATCHDOG_ENGINE_SIMULATE ?? null
  }

  private get sim() {
    return parseSimulate(this.simulate)
  }

  venvDir(): string {
    return join(this.dir, 'venv')
  }

  venvPython(): string {
    return this.platform === 'win32' ? join(this.venvDir(), 'Scripts', 'python.exe') : join(this.venvDir(), 'bin', 'python')
  }

  /** The venv's bin directory (where the `watchdog` command lives). */
  venvBin(): string {
    return dirname(this.venvPython())
  }

  /** engine.json when it is in this app's format, else null (see RECORD_SCHEMA). */
  private record(): EngineRecord | null {
    const r = readJson<EngineRecord>(join(this.dir, 'engine.json'))
    return r && r.schema === RECORD_SCHEMA && Array.isArray(r.phases) ? r : null
  }

  /** An engine.json exists that this app does not read: rebuild rather than guess. */
  private foreignRecord(): boolean {
    return existsSync(join(this.dir, 'engine.json')) && !this.record()
  }

  private writeRecord(r: EngineRecord): void {
    mkdirSync(this.dir, { recursive: true })
    const tmp = join(this.dir, 'engine.json.tmp')
    writeFileSync(tmp, JSON.stringify(r, null, 2))
    renameSync(tmp, join(this.dir, 'engine.json'))
  }

  uvPath(): string | null {
    const exe = this.platform === 'win32' ? 'uv.exe' : 'uv'
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

  private packagesCurrent(rec = this.record()): boolean {
    const wheel = this.wheelPath()
    if (!rec || !wheel) return !!rec && !wheel // nothing to compare against: whatever is installed stands
    return rec.wheelSha256 === sha256(wheel)
  }

  /** Python of the managed engine when it is installed (current or not), else null. */
  managedPython(): string | null {
    if (this.simulate) return null
    return this.record() && existsSync(this.venvPython()) ? this.venvPython() : null
  }

  /** Phase 1 installed and matching the app ('ready'); 'outdated' when the bundled wheel differs. */
  engineState(): 'missing' | 'outdated' | 'ready' {
    if (this.simulate) return this.simulatedCore || this.simulatedDone ? 'ready' : 'missing'
    const rec = this.record()
    if (!rec || !existsSync(this.venvPython())) return 'missing'
    return this.packagesCurrent(rec) && rec.phases.includes('core') ? 'ready' : 'outdated'
  }

  /**
   * Everything installed: phase 2's libraries and the model step. With a Python the app did not
   * install, there are no libraries to add, so only a model download in progress counts.
   */
  complete(): boolean {
    if (this.simulate) return this.simulatedDone
    if (this.externalPython) return !(this.running && this.phase === 2)
    const rec = this.record()
    return this.engineState() === 'ready' && !!rec && PHASES.every((p) => rec.phases.includes(p)) && !!rec.models
  }

  /** Phase 1 is in place and phase 2 is not: what the app resumes at launch. */
  needsBackground(): boolean {
    return !this.running && this.canInstall() && this.engineState() === 'ready' && !this.complete()
  }

  status(): EngineStatus {
    const rec = this.record()
    const es = this.engineState()
    return {
      state: this.running ? 'installing' : this.state === 'failed' || this.state === 'cancelled' ? this.state : es,
      engine: es,
      complete: this.complete(),
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
      run: { id: this.runId, state: this.state, phase: this.phase, steps: this.steps, log: this.log.slice(-400), error: this.error }
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
    this.opts.emit({ id: this.runId, state: this.state, phase: this.phase, steps: this.steps.map((s) => ({ ...s })), log, error: this.error })
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
      UV_CREDENTIALS_DIR: join(this.dir, 'credentials'),
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

  /** The environment for Python the engine runs itself (the model step, core-requirements). */
  private pythonEnv(external: boolean): NodeJS.ProcessEnv {
    const env: NodeJS.ProcessEnv = { ...this.env, WATCHDOG_PROGRESS: '1', PYTHONUNBUFFERED: '1', PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', NO_COLOR: '1' }
    for (const k of ['PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV']) delete env[k]
    const uv = this.uvPath()
    if (uv) env.WATCHDOG_UV = uv
    if (external && this.isDev) {
      // A development checkout's Python runs the repository's source, as the backend does.
      const src = join(this.opts.repoRoot ?? '', 'src')
      if (this.opts.repoRoot && existsSync(src)) env.PYTHONPATH = src
    }
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
      if (this.platform === 'win32') spawnSync('taskkill', ['/pid', String(child.pid), '/T', '/F'])
      else child.kill('SIGTERM')
    }
  }

  isRunning(): boolean {
    return this.running
  }

  /** The phase of the run in progress, or null. */
  runningPhase(): 1 | 2 | null {
    return this.running ? this.phase : null
  }

  /**
   * Install, repair or finish. Skips whatever is already in place: a run started with phase 1
   * current goes straight to phase 2. `onCore` runs between the phases, whether phase 1 had
   * anything to do or not.
   */
  async install(opts: InstallOptions = {}): Promise<EngineState> {
    if (this.running) return this.state
    this.running = true
    this.cancelled = false
    this.runId += 1
    this.state = 'running'
    this.phase = 1
    this.error = null
    this.log = []
    this.pendingLog = []
    this.steps = this.freshSteps()
    this.publish()
    try {
      if (this.simulate) await this.simulateRun(opts)
      else await this.realRun(opts)
      this.state = 'done'
    } catch (e) {
      const msg = (e as Error).message
      if (this.cancelled || msg === 'cancelled') {
        this.state = 'cancelled'
        this.error =
          (this.phase as 1 | 2 | null) === 2
            ? 'Setup was paused. Watchdog finishes it the next time it opens, or when you choose Try again; what was already downloaded is kept.'
            : 'The installation was cancelled. You can start it again; what was already downloaded is kept.'
      } else {
        this.state = 'failed'
        this.error = friendlyError(msg)
        this.addLog(`error: ${msg}`)
        const running = this.steps.find((s) => s.state === 'running')
        if (running) {
          running.state = 'failed'
          running.detail = null
        }
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

  private async enterPhase2(opts: InstallOptions): Promise<void> {
    if (opts.onCore) await opts.onCore()
    if (this.cancelled) throw new Error('cancelled')
    this.phase = 2
    this.publish()
  }

  private async realRun(opts: InstallOptions): Promise<void> {
    mkdirSync(this.dir, { recursive: true })
    let python: string
    const uv = this.uvPath()
    if (opts.external) {
      python = opts.external
      this.setStep('python', 'skipped', 'Already on this computer')
      this.setStep('packages', 'skipped', 'Watchdog is already installed')
      await this.enterPhase2(opts)
      this.setStep('libraries', 'skipped', 'Already installed')
    } else {
      if (!uv) throw new Error('The app is missing its installer (uv). Reinstall the app.')
      const wheel = this.wheelPath()
      const source = wheel ?? (this.isDev ? this.opts.repoRoot : null)
      if (!source) throw new Error('The app is missing the Watchdog package. Reinstall the app.')
      const env = this.uvEnv()

      // ── phase 1 ──
      if (opts.fresh || this.foreignRecord()) {
        this.addLog(opts.fresh ? 'Removing the existing environment' : 'The existing environment was made by another version of the app; rebuilding it')
        rmSync(this.venvDir(), { recursive: true, force: true })
        for (const f of ['engine.json', 'requirements.in', 'requirements.lock', 'core.txt']) rmSync(join(this.dir, f), { force: true })
      }
      let rec = this.record()
      const coreCurrent = !!rec && rec.phases.includes('core') && (wheel ? rec.wheelSha256 === sha256(wheel) : true)
      if (!coreCurrent || !existsSync(this.venvPython())) this.checkFreeSpace()

      this.setStep('python', 'running', 'Downloading Python')
      if (!this.venvWorks()) {
        rmSync(this.venvDir(), { recursive: true, force: true })
        rmSync(join(this.dir, 'engine.json'), { force: true })
        rec = null
        await this.runOrThrow('Installing Python', uv, ['python', 'install', PYTHON_VERSION, '--no-bin'], env, 'python')
        await this.runOrThrow('Creating the environment', uv, ['venv', this.venvDir(), '--python', PYTHON_VERSION, '--seed'], env, 'python')
      }
      python = this.venvPython()
      this.setStep('python', 'done', `Python ${PYTHON_VERSION}`)

      if (rec && coreCurrent) {
        this.setStep('packages', 'done', `Version ${rec.wheelVersion}`)
      } else {
        this.setStep('packages', 'running', 'Resolving libraries')
        await this.compileLock(uv, python, source, env)
        const args = ['pip', 'install', '--python', python, '--no-deps']
        if (rec) args.push('--reinstall-package', 'watchdog-intel')
        args.push(source)
        this.setStep('packages', 'running', 'Installing Watchdog')
        await this.runOrThrow('Installing Watchdog', uv, args, env, 'packages')
        await this.writeCoreRequirements(python)
        await this.runOrThrow(
          'Installing Watchdog’s libraries',
          uv,
          ['pip', 'install', '--python', python, ...torchArgs(this.platform), '-c', this.lockPath(), '-r', join(this.dir, 'core.txt')],
          env,
          'packages'
        )
        const version = wheel ? wheelVersion(wheel.split(/[\\/]/).pop()!) : this.pythonPackageVersion(python)
        this.writeRecord({
          schema: RECORD_SCHEMA,
          wheel: wheel ? wheel.split(/[\\/]/).pop()! : 'source',
          wheelSha256: wheel ? sha256(wheel) : 'source',
          wheelVersion: version,
          python: PYTHON_VERSION,
          installedAt: new Date().toISOString(),
          phases: ['core'],
          models: null
        })
        this.setStep('packages', 'done', `Version ${version}`)
      }

      // ── phase 2: libraries ──
      await this.enterPhase2(opts)
      rec = this.record()
      if (rec && rec.phases.includes('libraries')) {
        this.setStep('libraries', 'done', null)
      } else {
        this.setStep('libraries', 'running', 'Downloading libraries')
        if (!existsSync(this.lockPath())) await this.compileLock(uv, python, source, env, 'libraries')
        await this.runOrThrow('Installing the document and search libraries', uv, ['pip', 'install', '--python', python, ...torchArgs(this.platform), '-r', this.lockPath()], env, 'libraries')
        const now = this.record()
        if (now) this.writeRecord({ ...now, phases: [...new Set([...now.phases, 'libraries'])] })
        this.setStep('libraries', 'done', null)
      }
    }

    // ── phase 2: local models ──
    for (const id of MODEL_STEPS) this.setStep(id, 'pending')
    const results: Record<string, 'ok' | 'warn'> = {}
    const code = await this.run(python, ['-m', 'watchdog.gui.engine_setup', 'models'], this.pythonEnv(!!opts.external), (line) => {
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

  private lockPath(): string {
    return join(this.dir, 'requirements.lock')
  }

  private checkFreeSpace(): void {
    try {
      const free = statfsSync(this.dir)
      if (free.bavail * free.bsize < MIN_FREE_BYTES) {
        throw new Error('There is not enough free space on this computer: Watchdog needs about 7 GB while it installs.')
      }
    } catch (e) {
      if ((e as Error).message.startsWith('There is not')) throw e
    }
  }

  /** Resolve every library once, for both phases. Small: package metadata only. */
  private async compileLock(uv: string, python: string, source: string, env: NodeJS.ProcessEnv, step = 'packages'): Promise<void> {
    const input = join(this.dir, 'requirements.in')
    writeFileSync(input, `watchdog-intel @ ${pathToFileURL(source).href}\n`)
    await this.runOrThrow('Resolving Watchdog’s libraries', uv, ['pip', 'compile', '--python', python, ...torchArgs(this.platform), '--quiet', input, '-o', this.lockPath()], env, step)
  }

  /** core.txt: phase 1's requirement list, read from the installed package's own metadata. */
  private async writeCoreRequirements(python: string): Promise<void> {
    const lines: string[] = []
    const code = await this.run(python, ['-m', 'watchdog.gui.engine_setup', 'core-requirements'], this.pythonEnv(false), (l) => lines.push(l))
    if (this.cancelled) throw new Error('cancelled')
    if (code !== 0 || !lines.length) throw new Error(`Listing Watchdog’s libraries failed (exit ${code}).\n${this.log.slice(-6).join('\n')}`)
    writeFileSync(join(this.dir, 'core.txt'), lines.join('\n') + '\n')
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
  // WATCHDOG_ENGINE_SIMULATE plays a timed install without downloading anything, so the
  // onboarding screens and the background indicator can be exercised and photographed (see
  // parseSimulate for the values).

  private async simulateRun(opts: InstallOptions): Promise<void> {
    const sim = this.sim!
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
      packages: ['Resolved 135 packages in 2.4s', 'Installed 1 package in 12ms', 'Prepared 50 packages in 5.8s', 'Installed 50 packages in 81ms'],
      libraries: ['Resolved 135 packages in 539ms', 'Downloading torch (188.5MiB)', 'Downloading onnxruntime (17.2MiB)', 'Prepared 84 packages in 13.7s', 'Installed 85 packages in 249ms']
    }
    for (const def of STEP_DEFS) {
      const id = def.id
      if (def.phase === 2 && this.phase === 1) await this.enterPhase2(opts)
      if (def.phase === 1 && this.simulatedCore) {
        this.setStep(id, 'done', null)
        continue
      }
      this.setStep(id, 'running', 'Downloading')
      const pace = def.phase === 2 && sim.slowPhase2 ? 9000 : sim.slow ? 1400 : 600
      for (const l of lines[id] ?? [`Fetching ${def.label}`]) {
        this.addLog(l)
        await pause(pace)
      }
      if (sim.failAt === id && !this.simulatedFailed) {
        this.simulatedFailed = true
        if (def.optional) {
          this.setStep(id, 'warning', 'Could not download. It will be fetched when first needed.')
          continue
        }
        throw new Error('Watchdog could not download what it needs. Check the internet connection and try again.')
      }
      this.setStep(id, 'done', null)
      if (id === 'packages') this.simulatedCore = true
    }
    this.simulatedDone = true
  }
}
