// Signing in to Claude with a subscription, using the Claude Code program that ships inside the
// claude-agent-sdk package (the same copy the in-app chat runs). `claude auth login --claudeai`
// opens the browser; this module then polls `claude auth status --json` until it reports a login.

import { ChildProcess, execFile, spawn, spawnSync } from 'node:child_process'
import { delimiter } from 'node:path'
import { createInterface } from 'node:readline'

/** How Claude Code on this computer is signed in. `claude.ai` is a subscription login (what
 * `claude auth login --claudeai` makes); `oauth_token` is a long-lived subscription token from the
 * environment; `api_key` is an Anthropic API key. Anything else (a cloud provider, a gateway) is `other`. */
export type ClaudeMethod = 'subscription' | 'token' | 'api_key' | 'other'
export interface ClaudeStatus {
  installed: boolean
  loggedIn: boolean
  /** Null when not signed in. */
  method: ClaudeMethod | null
  /** The subscription plan (for example `pro` or `max`), when the program reports one. */
  plan: string | null
  email: string | null
}

const NOT_SIGNED_IN: ClaudeStatus = { installed: true, loggedIn: false, method: null, plan: null, email: null }

/** True for a login that runs on a Claude subscription, not an API key. */
export const usesSubscription = (s: Pick<ClaudeStatus, 'loggedIn' | 'method'>): boolean => s.loggedIn && (s.method === 'subscription' || s.method === 'token')

/** Read the JSON of `claude auth status --json`. */
export function parseAuthStatus(stdout: string): ClaudeStatus {
  try {
    const j = JSON.parse(stdout) as Record<string, unknown>
    if (j.loggedIn !== true) return NOT_SIGNED_IN
    const text = (v: unknown) => (typeof v === 'string' && v.trim() ? v.trim() : null)
    const method: ClaudeMethod = j.authMethod === 'claude.ai' ? 'subscription' : j.authMethod === 'oauth_token' ? 'token' : j.authMethod === 'api_key' ? 'api_key' : 'other'
    return { installed: true, loggedIn: true, method, plan: text(j.subscriptionType), email: text(j.email) }
  } catch {
    return NOT_SIGNED_IN
  }
}

const POLL_MS = 2000
const GIVE_UP_MS = 10 * 60 * 1000

export class ClaudeSignIn {
  private cli: () => string | null
  private onUrl: (url: string | null) => void
  private env: () => NodeJS.ProcessEnv
  private child: ChildProcess | null = null
  private stop: (() => void) | null = null
  private simulate: boolean

  constructor(cli: () => string | null, onUrl: (url: string | null) => void, env: () => NodeJS.ProcessEnv, simulate: boolean) {
    this.cli = cli
    this.onUrl = onUrl
    this.env = env
    this.simulate = simulate
  }

  /** The sign-in check while a login is in progress; blocks, so only used where nothing else waits on it. */
  status(): ClaudeStatus {
    if (this.simulate) return NOT_SIGNED_IN
    const cli = this.cli()
    if (!cli) return { ...NOT_SIGNED_IN, installed: false }
    const r = spawnSync(cli, ['auth', 'status', '--json'], { encoding: 'utf8', timeout: 20000, env: this.env() })
    return parseAuthStatus(r.stdout ?? '')
  }

  /** The same check without blocking the main process, for the screen that asks on load. */
  statusAsync(): Promise<ClaudeStatus> {
    if (this.simulate) return Promise.resolve(NOT_SIGNED_IN)
    const cli = this.cli()
    if (!cli) return Promise.resolve({ ...NOT_SIGNED_IN, installed: false })
    return new Promise((resolve) => {
      execFile(cli, ['auth', 'status', '--json'], { encoding: 'utf8', timeout: 20000, env: this.env(), windowsHide: true }, (_err, stdout) => resolve(parseAuthStatus(stdout ?? '')))
    })
  }

  cancel(): void {
    this.stop?.()
  }

  signIn(): Promise<{ ok: boolean; message: string | null }> {
    this.cancel()
    if (this.simulate) {
      return new Promise((resolve) => {
        this.onUrl('https://claude.ai/login')
        const t = setTimeout(() => resolve({ ok: true, message: null }), 2500)
        this.stop = () => {
          clearTimeout(t)
          resolve({ ok: false, message: 'Sign-in was cancelled.' })
        }
      })
    }
    const cli = this.cli()
    if (!cli) return Promise.resolve({ ok: false, message: 'Claude was not found in the engine. Repair the engine in Settings, then try again.' })
    return new Promise((resolve) => {
      let done = false
      const finish = (r: { ok: boolean; message: string | null }) => {
        if (done) return
        done = true
        clearInterval(poll)
        clearTimeout(giveUp)
        const c = this.child
        this.child = null
        this.stop = null
        if (c && c.exitCode === null) c.kill()
        this.onUrl(null)
        resolve(r)
      }
      const env = { ...this.env(), PATH: [this.env().PATH ?? '', '/usr/bin'].join(delimiter) }
      const child = spawn(cli, ['auth', 'login', '--claudeai'], { env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true })
      this.child = child
      const tail: string[] = []
      const watch = (line: string) => {
        tail.push(line)
        if (tail.length > 10) tail.shift()
        const m = line.match(/https:\/\/\S+/)
        if (m) this.onUrl(m[0])
      }
      createInterface({ input: child.stdout! }).on('line', watch)
      createInterface({ input: child.stderr! }).on('line', watch)
      child.on('error', (e) => finish({ ok: false, message: `Claude could not be started: ${e.message}` }))
      child.on('close', (code) => {
        if (done) return
        // The command can exit once the browser step is complete; the status is the truth.
        if (usesSubscription(this.status())) finish({ ok: true, message: null })
        else finish({ ok: false, message: code === 0 ? 'Sign-in did not complete.' : tail.slice(-2).join(' ') || 'Sign-in did not complete.' })
      })
      const poll = setInterval(() => {
        if (usesSubscription(this.status())) finish({ ok: true, message: null })
      }, POLL_MS)
      const giveUp = setTimeout(() => finish({ ok: false, message: 'Sign-in timed out. Try again.' }), GIVE_UP_MS)
      this.stop = () => finish({ ok: false, message: 'Sign-in was cancelled.' })
    })
  }
}
