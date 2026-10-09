// Signing in to Claude with a subscription, using the Claude Code program that ships inside the
// claude-agent-sdk package (the same copy the in-app chat runs). `claude auth login --claudeai`
// opens the browser; this module then polls `claude auth status --json` until it reports a login.

import { ChildProcess, spawn, spawnSync } from 'node:child_process'
import { delimiter } from 'node:path'
import { createInterface } from 'node:readline'

export interface ClaudeStatus { installed: boolean; loggedIn: boolean }

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

  status(): ClaudeStatus {
    if (this.simulate) return { installed: true, loggedIn: false }
    const cli = this.cli()
    if (!cli) return { installed: false, loggedIn: false }
    const r = spawnSync(cli, ['auth', 'status', '--json'], { encoding: 'utf8', timeout: 20000, env: this.env() })
    if (r.status !== 0 && !r.stdout) return { installed: true, loggedIn: false }
    try {
      return { installed: true, loggedIn: JSON.parse(r.stdout).loggedIn === true }
    } catch {
      return { installed: true, loggedIn: false }
    }
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
        if (this.status().loggedIn) finish({ ok: true, message: null })
        else finish({ ok: false, message: code === 0 ? 'Sign-in did not complete.' : tail.slice(-2).join(' ') || 'Sign-in did not complete.' })
      })
      const poll = setInterval(() => {
        if (this.status().loggedIn) finish({ ok: true, message: null })
      }, POLL_MS)
      const giveUp = setTimeout(() => finish({ ok: false, message: 'Sign-in timed out. Try again.' }), GIVE_UP_MS)
      this.stop = () => finish({ ok: false, message: 'Sign-in was cancelled.' })
    })
  }
}
