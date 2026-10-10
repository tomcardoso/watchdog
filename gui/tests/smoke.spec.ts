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

    // pdf.js's image decoders (JPEG 2000, JBIG2) are shipped next to the renderer and load from it;
    // without them a scanned page draws blank.
    const wasm = await page.evaluate(() => new Promise<{ status: number; bytes: number }>((done) => {
      const x = new XMLHttpRequest()
      x.open('GET', new URL('pdfjs/wasm/openjpeg.wasm', document.baseURI).href)
      x.responseType = 'arraybuffer'
      x.onload = () => done({ status: x.status, bytes: (x.response as ArrayBuffer)?.byteLength ?? 0 })
      x.onerror = () => done({ status: -1, bytes: 0 })
      x.send()
    }))
    expect(wasm.bytes, 'openjpeg.wasm reachable from the renderer').toBeGreaterThan(100_000)

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

    // Marking facts by keyboard carries on after a note: Enter saves it and returns to the row.
    await page.evaluate(() => (window as any).__watchdogApp.getState().navigate({ view: 'entity', id: 'city-of-port-calder' }))
    const factRow = page.locator('[data-fact-id]').first()
    await factRow.focus()
    await page.keyboard.press('v')
    await expect(factRow).toHaveClass(/is-marked-verified/, { timeout: 10_000 })
    await page.keyboard.press('n')
    await expect(page.locator('.fcheck-textarea')).toBeFocused()
    await page.keyboard.type('Checked against the minutes')
    await page.keyboard.press('Enter')
    await expect(factRow).toBeFocused()
    await page.keyboard.press('c')
    await expect(factRow).toHaveClass(/is-marked-unverifiable/, { timeout: 10_000 })
    await page.keyboard.press('c')
    await expect(factRow).not.toHaveClass(/is-marked-/, { timeout: 10_000 })

    // Briefings → Current state is written for the reporter: no instructions for Claude, no
    // command lines. What Claude is given (the session primer) is one click away.
    await page.evaluate(() => (window as any).__watchdogApp.getState().navigate({ view: 'briefings', path: 'session-primer' }))
    await expect(page.locator('.bf-article h2', { hasText: 'Waiting on you' })).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('.bf-article')).not.toContainText('watchdog search')
    await expect(page.locator('.bf-article')).not.toContainText('Citing')
    await page.getByRole('button', { name: 'What Claude is given' }).click()
    // A wikilink written inside code is shown as written, not turned into a link (the primer
    // quotes the citation form in code).
    await expect(page.locator('code', { hasText: '[[documents/<slug>#^f-<id>|p. N]]' }).first()).toBeVisible({ timeout: 10_000 })
    await page.getByRole('button', { name: 'Back to current state' }).click()
    await expect(page.locator('.bf-article h2', { hasText: 'Your questions' })).toBeVisible({ timeout: 10_000 })

    // A search typed on the Search screen keeps the route in step, so searching the earlier query
    // again (from the palette) runs it rather than doing nothing.
    await page.evaluate(() => (window as any).__watchdogApp.getState().navigate({ view: 'search', query: 'contract' }))
    await page.locator('.srch-input').fill('harbour')
    await page.locator('.srch-input').press('Enter')
    await expect.poll(() => page.evaluate(() => (window as any).__watchdogApp.getState().route.query)).toBe('harbour')
    await page.evaluate(() => (window as any).__watchdogApp.getState().navigate({ view: 'search', query: 'contract' }))
    await expect(page.locator('.srch-input')).toHaveValue('contract')

    // Releasing a recent lock leaves it in place: the app says so in its own words, never with the
    // CLI's "Use watchdog unlock --force".
    const lockFile = join(root, 'vault', '.watchdog', 'registry', '.processing-lock')
    writeFileSync(lockFile, `pid: cli\nstarted_at: ${new Date().toISOString().replace(/\.\d+Z$/, 'Z')}\n`)
    await page.evaluate(() => (window as any).__watchdogApp.getState().navigate({ view: 'activity', tab: 'maintenance' }))
    await page.getByRole('button', { name: 'Release lock', exact: true }).click()
    await expect(page.locator('.toast').last()).toContainText('left in place', { timeout: 15_000 })
    await expect(page.locator('.toast').last()).not.toContainText('watchdog')
    rmSync(lockFile, { force: true })

    // Tooltips (one layer for the app) stay inside the window, even for a control at the top edge of
    // a panel that hides its overflow, such as the document viewer's toolbar.
    await page.evaluate(() => (window as any).__watchdogApp.getState().navigate({ view: 'documents' }))
    await page.locator('.docs-card').first().click()
    const tipped = page.locator('.pdf-toolbar [data-tip]:not([disabled])').first()
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

    // Add documents belongs to one investigation: a gate left open there is not shown, or run, in
    // another one.
    const second = await page.evaluate(async (dir) => {
      const w = window as any
      const r = await w.watchdog.rpc('action.run', { vault: null, args: ['new', 'Second Look', '--dir', dir] })
      if (r.code !== 0) return r.stderr || r.stdout
      return (await w.watchdog.rpc('projects.list', {})).find((p: { name: string }) => p.name === 'Second Look')?.slug ?? 'not listed'
    }, root)
    expect(second).toMatch(/^second-look/)
    await page.evaluate(() => (window as any).__watchdogApp.getState().openAdd())
    await page.getByRole('button', { name: 'Read documents' }).click()
    await expect(page.getByText('Before anything is sent', { exact: true })).toBeVisible({ timeout: 60_000 })
    await page.locator('.modal').getByRole('button', { name: 'Cancel' }).click()
    await page.evaluate(async (s) => {
      const w = window as any
      w.__watchdogApp.getState().setProject(await w.watchdog.rpc('projects.get', { slug: s }))
      w.__watchdogApp.getState().openAdd()
    }, second)
    await expect(page.locator('.modal').getByText('Add documents', { exact: true })).toBeVisible()
    await expect(page.getByText('Before anything is sent', { exact: true })).toHaveCount(0)
    await page.locator('.modal').getByRole('button', { name: 'Cancel' }).click()

    // With no investigation open, the sidebar's All investigations returns to the list from any
    // other screen.
    await page.evaluate(() => (window as any).__watchdogApp.getState().setProject(null))
    await page.locator('.nav-item', { hasText: 'Activity' }).click()
    await expect(page.locator('.nav-item', { hasText: 'All investigations' })).not.toHaveAttribute('aria-current', 'page')
    await page.locator('.nav-item', { hasText: 'All investigations' }).click()
    await expect.poll(() => page.evaluate(() => (window as any).__watchdogApp.getState().route.view)).toBe('projects')
    await expect(page.locator('.nav-item', { hasText: 'All investigations' })).toHaveAttribute('aria-current', 'page')
    // A screen that needs an investigation, with none open, shows the list and says so.
    await page.evaluate(() => (window as any).__watchdogApp.getState().navigate({ view: 'timeline' }))
    await expect.poll(() => page.evaluate(() => (window as any).__watchdogApp.getState().route.view)).toBe('projects')
    await expect(page.locator('.topbar .crumbs')).toHaveText('Investigations')

    // Find in a document (D289): the box is always in the viewer's toolbar; a match on the
    // scanned cover page is highlighted on the layer built from its saved OCR positions, and a
    // query with no matches offers the extracted text instead.
    const foi = await page.evaluate(async (s) => {
      const w = window as any
      w.__watchdogApp.getState().setProject(await w.watchdog.rpc('projects.get', { slug: s }))
      const vault = w.__watchdogApp.getState().project.path
      const docs = await w.watchdog.rpc('vault.documents', { vault })
      return docs.find((d: { filename: string }) => d.filename.startsWith('foi-response')).sha as string
    }, slug)
    await page.evaluate((sha) => (window as any).__watchdogApp.getState().navigate({ view: 'document', sha }), foi)
    const findBox = page.getByRole('textbox', { name: 'Find in document' })
    await expect(findBox).toBeEnabled({ timeout: 20_000 })
    await findBox.fill('budgeting purposes')
    await expect(page.locator('.find-count')).toHaveText('1 of 2', { timeout: 20_000 })
    await findBox.press('Enter')
    await expect(page.locator('.ocr-layer mark.find-hit.current')).toBeVisible({ timeout: 20_000 })
    await expect(page.locator('.ocr-layer mark.find-hit.current')).toHaveText(/budgeting purposes/i)
    await findBox.fill('no such words anywhere')
    await page.getByRole('button', { name: 'Search the extracted text instead' }).click()
    await expect(page.getByPlaceholder('Search the extracted text')).toHaveValue('no such words anywhere')

    // The command palette: a full-height search row (it used to shrink to its text), and the panel
    // inside the window at a small size.
    await page.setViewportSize({ width: 1000, height: 700 })
    await page.evaluate(() => (window as any).__watchdogApp.getState().setPalette(true))
    await page.locator('[cmdk-input]').fill('port')
    await expect(page.locator('[cmdk-item]').nth(5)).toBeVisible({ timeout: 10_000 })
    {
      const row = (await page.locator('.palette-search').boundingBox())!
      const panel = (await page.locator('.palette').boundingBox())!
      expect(row.height).toBeGreaterThanOrEqual(44)
      expect(panel.y + panel.height).toBeLessThanOrEqual(700)
    }
    await page.keyboard.press('Escape')
    await expect(page.locator('.palette')).toHaveCount(0)

    // The incoming-folder watcher shows as a small chip, never a card over the screen.
    await page.evaluate(() => {
      const st = (window as any).__watchdogApp.getState()
      st.upsertJob({ id: 'e2e-watch', label: 'Watching incoming', kind: 'watch', vault: st.project.path, args: ['watch'], state: 'running', exit_code: null, started: new Date().toISOString(), finished: null, progress: {} })
      st.navigate({ view: 'settings', tab: 'about' })
    })
    await expect(page.locator('.job-chip', { hasText: 'Watching incoming' })).toBeVisible()
    await expect(page.locator('.job-card')).toHaveCount(0)
    await page.evaluate(() => (window as any).__watchdogApp.getState().upsertJob({ id: 'e2e-watch', label: 'Watching incoming', kind: 'watch', vault: null, args: ['watch'], state: 'cancelled', exit_code: null, started: new Date().toISOString(), finished: new Date().toISOString(), progress: {} }))
    await expect(page.locator('.job-dock')).toHaveCount(0)

    // Review at a small window: every queue tab and every tool is on screen, none scrolled out of
    // sight sideways.
    await page.evaluate(() => (window as any).__watchdogApp.getState().navigate({ view: 'review' }))
    await expect(page.getByRole('tab').first()).toBeVisible({ timeout: 10_000 })
    const offscreen = await page.evaluate(() => {
      const out: string[] = []
      const main = document.querySelector('.page')!.getBoundingClientRect()
      document.querySelectorAll('.rv [role="tab"], .rv-tools button').forEach((el) => {
        const r = el.getBoundingClientRect()
        if (r.width === 0 || r.left < main.left || r.right > main.right) out.push(el.textContent ?? '')
      })
      return out
    })
    expect(offscreen, 'Review tabs out of view at 1000 px').toEqual([])
    expect(await page.getByRole('tab').count()).toBe(6)
    await page.locator('.rv-tools').getByRole('button', { name: 'Watch list' }).click()
    // The structured editor: add a term, see it listed, refuse a duplicate, remove it.
    const addBox = page.getByLabel('Add a term')
    await expect(addBox).toBeVisible({ timeout: 10_000 })
    await addBox.fill('Smoke Test Holdings')
    await page.getByRole('button', { name: 'Add term' }).click()
    const list = page.getByRole('list', { name: 'Watch list terms' })
    await expect(list.getByText('Smoke Test Holdings')).toBeVisible({ timeout: 10_000 })
    await addBox.fill('smoke test holdings')
    await page.getByRole('button', { name: 'Add term' }).click()
    await expect(page.getByRole('alert')).toContainText('already on the list')
    await page.getByRole('button', { name: 'Remove Smoke Test Holdings' }).click()
    await expect(list.getByText('Smoke Test Holdings')).toHaveCount(0, { timeout: 10_000 })

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
