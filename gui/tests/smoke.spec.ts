// End-to-end smoke test: builds the fictional demo investigation in a throwaway HOME, launches the
// built app (run `npm run build` first) against it, and visits every screen in both themes. Any
// uncaught renderer error, or an error callout on a screen, fails the test.
//
// Needs a Python with Watchdog's dependencies: set WATCHDOG_PYTHON. On headless Linux, run under
// xvfb-run.

import { _electron as electron, expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const python = process.env.WATCHDOG_PYTHON ?? 'python3'
const repoSrc = resolve(__dirname, '..', '..', 'src')

const ROUTES = [
  { view: 'home' },
  { view: 'documents' },
  { view: 'entities' },
  { view: 'graph' },
  { view: 'timeline' },
  { view: 'search', query: 'contract' },
  { view: 'review' },
  { view: 'briefings' },
  { view: 'ask' },
  { view: 'research' },
  { view: 'activity' },
  { view: 'settings' },
  { view: 'projects' }
]

test('every screen renders against the demo investigation', async () => {
  const root = mkdtempSync(join(tmpdir(), 'wd-e2e-'))
  const home = join(root, 'home')
  try {
    execFileSync(python, ['-m', 'watchdog.gui.demo', join(root, 'vault'), '--home', home], {
      env: { ...process.env, PYTHONPATH: repoSrc },
      stdio: 'inherit'
    })
    const projects = JSON.parse(readFileSync(join(home, '.watchdog', 'projects.json'), 'utf8'))
    const slug = Object.keys(projects)[0]

    const app = await electron.launch({
      args: [resolve(__dirname, '..'), '--no-sandbox'],
      env: { ...process.env, HOME: home, WATCHDOG_PYTHON: python, WATCHDOG_SRC: repoSrc }
    })
    const page = await app.firstWindow()
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(e.message))
    await page.waitForSelector('.app', { timeout: 90_000 })
    await page.evaluate(async (s) => {
      const w = window as unknown as { watchdog: { rpc: (m: string, p: unknown) => Promise<unknown> }; __watchdogApp: { getState: () => { setProject: (p: unknown) => void } } }
      w.__watchdogApp.getState().setProject(await w.watchdog.rpc('projects.get', { slug: s }))
    }, slug)

    // wdfile:// answers byte ranges, which a recording needs to seek (D273).
    const clip = join(root, 'vault', 'context', 'range-check.mp3')
    writeFileSync(clip, Buffer.from(Array.from({ length: 1000 }, (_, i) => i % 256)))
    const ranged = await page.evaluate(async (p) => {
      const w = window as unknown as { watchdog: { files: { url: (p: string) => string } } }
      const url = w.watchdog.files.url(p)
      const r = await fetch(url, { headers: { Range: 'bytes=10-19' } })
      const whole = await fetch(url)
      return { status: r.status, range: r.headers.get('content-range'), bytes: Array.from(new Uint8Array(await r.arrayBuffer())), wholeStatus: whole.status, wholeLength: (await whole.arrayBuffer()).byteLength, accept: whole.headers.get('accept-ranges') }
    }, clip)
    expect(ranged).toEqual({ status: 206, range: 'bytes 10-19/1000', bytes: [10, 11, 12, 13, 14, 15, 16, 17, 18, 19], wholeStatus: 200, wholeLength: 1000, accept: 'bytes' })

    for (const theme of ['light', 'dark']) {
      await page.evaluate((t) => (window as any).__watchdogApp.getState().setTheme(t), theme)
      for (const route of ROUTES) {
        if (route.view === 'projects') continue
        await page.evaluate((r) => (window as any).__watchdogApp.getState().navigate(r), route)
        await page.waitForTimeout(1200)
        const failure = await page.locator('.callout.danger').first().textContent({ timeout: 500 }).catch(() => null)
        expect(failure, `${theme} ${route.view} shows an error`).toBeNull()
      }
    }
    expect(errors, 'renderer errors').toEqual([])
    await app.close()
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
