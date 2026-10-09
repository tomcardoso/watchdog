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
  { view: 'review', kind: 'merges' },
  { view: 'briefings' },
  { view: 'ask' },
  { view: 'research' },
  { view: 'activity' },
  { view: 'activity', tab: 'versions' },
  { view: 'settings' },
  { view: 'settings', tab: 'history' },
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
        if ('kind' in route && route.kind === 'merges') {
          // The demo leaves one possible-same pair for the reporter, and logs every merge (D279).
          await expect(page.locator('.mg-side')).toHaveCount(2)
          expect(await page.locator('.mg-row').count()).toBeGreaterThan(0)
        }
      }
    }
    // Version history (D286): an entity's History lists the demo's runs and marks, with a diff.
    await page.evaluate(() => (window as any).__watchdogApp.getState().navigate({ view: 'entity', id: 'city-of-port-calder' }))
    await page.getByRole('button', { name: 'History', exact: true }).click()
    await expect(page.locator('.hist-item').first()).toBeVisible({ timeout: 10_000 })
    expect(await page.locator('.hist-item').count()).toBeGreaterThan(1)
    await expect(page.locator('.hist-line').first()).toBeVisible({ timeout: 10_000 })
    await expect(page.getByRole('button', { name: 'Restore my notes' })).toBeVisible()
    await page.keyboard.press('Escape')

    // Notes (D288): written in the app, saved even when the reporter leaves at once, rendered
    // when not editing.
    await page.getByRole('button', { name: 'Add notes' }).click()
    await page.getByRole('textbox', { name: 'Your notes' }).fill('Called the clerk about **Pier 9**.')
    await page.evaluate(() => (window as any).__watchdogApp.getState().navigate({ view: 'timeline' }))
    const notePath = join(root, 'vault', 'entities', 'public-body', 'city-of-port-calder.md')
    await expect.poll(() => readFileSync(notePath, 'utf8'), { timeout: 10_000 }).toContain('Called the clerk about **Pier 9**.')
    await page.evaluate(() => (window as any).__watchdogApp.getState().navigate({ view: 'entity', id: 'city-of-port-calder' }))
    await expect(page.locator('.notes-view strong')).toHaveText('Pier 9', { timeout: 10_000 })

    // Removing a version (D288): an older version goes after a confirmation, and the list marks
    // where it was.
    await page.getByRole('button', { name: 'History', exact: true }).click()
    await expect(page.locator('.hist-item').nth(1)).toBeVisible({ timeout: 10_000 })
    await page.locator('.hist-item').nth(1).click()
    await page.getByRole('button', { name: 'Remove…' }).click()
    await expect(page.getByText('This cannot be undone')).toBeVisible()
    await page.getByRole('button', { name: 'Remove this version' }).click()
    await expect(page.locator('.hist-removed').first()).toBeVisible({ timeout: 10_000 })
    await page.keyboard.press('Escape')

    // Tooltips (one layer for the app) stay inside the window, even for a control at the top edge of
    // a panel that hides its overflow, such as the document viewer's toolbar.
    await page.evaluate(() => (window as any).__watchdogApp.getState().navigate({ view: 'documents' }))
    await page.locator('.docs-card').first().click()
    const tipped = page.locator('.pdf-toolbar [data-tip]').first()
    await expect(tipped).toBeVisible({ timeout: 10_000 })
    {
      await tipped.hover()
      const tip = page.getByRole('tooltip')
      await expect(tip).toBeVisible({ timeout: 3000 })
      const box = (await tip.boundingBox())!
      const view = page.viewportSize() ?? (await page.evaluate(() => ({ width: innerWidth, height: innerHeight })))
      expect(box.y).toBeGreaterThanOrEqual(0)
      expect(box.x).toBeGreaterThanOrEqual(0)
      expect(box.x + box.width).toBeLessThanOrEqual(view.width)
      await page.mouse.move(1, 1)
      await expect(tip).toBeHidden()
    }

    // With no investigation open, the sidebar's All investigations returns to the list from any
    // other screen.
    await page.evaluate(() => (window as any).__watchdogApp.getState().setProject(null))
    await page.locator('.nav-item', { hasText: 'Activity' }).click()
    await expect(page.locator('.nav-item', { hasText: 'All investigations' })).not.toHaveAttribute('aria-current', 'page')
    await page.locator('.nav-item', { hasText: 'All investigations' }).click()
    await expect.poll(() => page.evaluate(() => (window as any).__watchdogApp.getState().route.view)).toBe('projects')
    await expect(page.locator('.nav-item', { hasText: 'All investigations' })).toHaveAttribute('aria-current', 'page')

    expect(errors, 'renderer errors').toEqual([])
    await app.close()
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

// The engine's background phase (D272): the app opens on the light phase-1 environment, shows the
// setup bar in the sidebar, and adding documents waits, in the interface and in the backend.
// WATCHDOG_PHASE1_PYTHON may name an environment holding only phase 1's libraries
// (`node scripts/engine-cli.mjs --core-only` builds one); otherwise WATCHDOG_PYTHON is used. The
// engine itself is simulated with a slow phase 2, so the gate stays closed while the test looks.
test('adding documents waits for the background setup', async () => {
  const root = mkdtempSync(join(tmpdir(), 'wd-e2e-'))
  const home = join(root, 'home')
  const phase1 = process.env.WATCHDOG_PHASE1_PYTHON ?? python
  try {
    execFileSync(phase1, ['-m', 'watchdog.gui.demo', join(root, 'vault'), '--home', home], {
      env: { ...process.env, PYTHONPATH: repoSrc },
      stdio: 'inherit'
    })
    const projects = JSON.parse(readFileSync(join(home, '.watchdog', 'projects.json'), 'utf8'))
    const slug = Object.keys(projects)[0]
    const app = await electron.launch({
      args: [resolve(__dirname, '..'), '--no-sandbox'],
      env: { ...process.env, HOME: home, WATCHDOG_PYTHON: phase1, WATCHDOG_SRC: repoSrc, WATCHDOG_ENGINE_SIMULATE: 'resume,slow-phase2' }
    })
    const page = await app.firstWindow()
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(e.message))
    await page.waitForSelector('.app', { timeout: 90_000 })
    await page.evaluate(async (s) => {
      const w = window as any
      w.__watchdogApp.getState().setProject(await w.watchdog.rpc('projects.get', { slug: s }))
    }, slug)
    await expect(page.locator('.setup-foot')).toContainText('Finishing setup', { timeout: 20_000 })
    await expect(page.locator('.topbar button', { hasText: 'Add documents' })).toBeDisabled()

    const refused = await page.evaluate(async (s) => {
      const w = window as any
      const p = await w.watchdog.rpc('projects.get', { slug: s })
      try {
        await w.watchdog.rpc('jobs.start', { vault: p.path, args: ['add', '--skip-warning'], label: 'x' })
        return 'started'
      } catch (e) {
        return String((e as Error).message ?? e)
      }
    }, slug)
    // The bridge carries the message (an error's own fields do not cross into the page).
    expect(refused).toContain('still setting up')

    const routes = [{ view: 'home' }, { view: 'documents' }, { view: 'search', query: 'contract' }, { view: 'activity', tab: 'maintenance' }, { view: 'settings', tab: 'setup' }]
    for (const route of routes) {
      await page.evaluate((r) => (window as any).__watchdogApp.getState().navigate(r), route)
      await page.waitForTimeout(1200)
      const failure = await page.locator('.callout.danger').first().textContent({ timeout: 500 }).catch(() => null)
      expect(failure, `${route.view} shows an error`).toBeNull()
    }
    await expect(page.locator('.card-title', { hasText: 'Finishing setup' })).toBeVisible()
    expect(errors, 'renderer errors').toEqual([])
    await app.close()
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

// When the background phase finishes, the bar goes away and the running backend accepts what it
// refused, without a restart (engine.setReady).
test('the gate lifts when the background setup finishes', async () => {
  const root = mkdtempSync(join(tmpdir(), 'wd-e2e-'))
  const home = join(root, 'home')
  try {
    execFileSync(python, ['-m', 'watchdog.gui.demo', join(root, 'vault'), '--home', home], { env: { ...process.env, PYTHONPATH: repoSrc }, stdio: 'inherit' })
    const app = await electron.launch({
      args: [resolve(__dirname, '..'), '--no-sandbox'],
      env: { ...process.env, HOME: home, WATCHDOG_PYTHON: python, WATCHDOG_SRC: repoSrc, WATCHDOG_ENGINE_SIMULATE: 'resume' }
    })
    const page = await app.firstWindow()
    await page.waitForSelector('.app', { timeout: 90_000 })
    await expect(page.locator('.setup-foot')).toBeVisible({ timeout: 20_000 })
    await expect(page.locator('.setup-foot')).toHaveCount(0, { timeout: 60_000 })
    const ready = await page.evaluate(() => (window as any).watchdog.rpc('engine.ready', {}))
    expect(ready).toEqual({ ready: true })
    await app.close()
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
