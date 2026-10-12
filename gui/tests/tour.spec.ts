// End-to-end test of the sample investigation and its guided tour (D303): from an empty list of
// investigations, the welcome card's link builds the sample through the real `sample-create`
// operation (prepared answers, no model, no keys), opens it and starts the tour; every step must
// then ring a visible element on the screen it opened. Run `npm run build` first; needs
// WATCHDOG_PYTHON, and xvfb-run on headless Linux.

import { _electron as electron, expect, test } from '@playwright/test'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const python = process.env.WATCHDOG_PYTHON ?? 'python3'
const repoSrc = resolve(__dirname, '..', '..', 'src')
const STEPS = 14

test('the sample builds from the welcome card and every tour step points at something on screen', async () => {
  const root = mkdtempSync(join(tmpdir(), 'wd-tour-'))
  const home = join(root, 'home')
  const investigations = join(root, 'Investigations')
  try {
    mkdirSync(join(home, '.watchdog'), { recursive: true })
    mkdirSync(investigations)
    // First-run setup is done and the investigations folder is allowed, as after onboarding.
    writeFileSync(join(home, '.watchdog', 'config.json'), JSON.stringify({ projects_dir: investigations }))
    writeFileSync(join(home, '.watchdog', 'access.json'), JSON.stringify({ version: 1, folders: [{ path: investigations, label: 'new investigations', granted: new Date().toISOString() }] }))

    const app = await electron.launch({
      args: [resolve(__dirname, '..'), '--no-sandbox'],
      env: { ...process.env, HOME: home, WATCHDOG_PYTHON: python, WATCHDOG_SRC: repoSrc }
    })
    const page = await app.firstWindow()
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(e.message))
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.waitForSelector('.app', { timeout: 90_000 })

    // The empty list offers the sample; building it is a job, then the tour starts by itself.
    await page.getByRole('button', { name: 'Explore a sample investigation' }).click()
    await expect(page.getByText('It uses prepared answers instead of an AI model')).toBeVisible()
    await page.getByRole('button', { name: 'Build the sample' }).click()
    const panel = page.locator('.tour-panel')
    await expect(panel).toBeVisible({ timeout: 120_000 })

    const projects = JSON.parse(readFileSync(join(home, '.watchdog', 'projects.json'), 'utf8'))
    const entry = projects['port-calder-waterfront-sample']
    expect(entry.sample).toBe(true)
    expect(entry.path).toBe(join(investigations, 'port-calder-waterfront-sample'))
    expect(existsSync(join(home, '.watchdog', 'telemetry.db'))).toBe(false)

    const seen: string[] = []
    for (let i = 1; i <= STEPS; i++) {
      await expect(panel.locator('.tour-count')).toHaveText(`Step ${i} of ${STEPS}`)
      const ring = page.locator('.tour-ring')
      await expect(ring, `step ${i} rings its target`).toBeVisible({ timeout: 20_000 })
      // The ring sits on screen, around something real.
      await expect.poll(async () => {
        const box = await ring.boundingBox()
        return !!box && box.width > 20 && box.height > 10 && box.y >= 0 && box.y + box.height <= 900 + 12
      }, { message: `step ${i} ring on screen`, timeout: 10_000 }).toBe(true)
      await expect(panel.locator('.tour-missing')).toHaveCount(0)
      seen.push((await panel.getAttribute('data-step')) ?? '')
      // The panel's main button has the keyboard focus, so Enter moves on.
      const main = i === STEPS ? panel.getByRole('button', { name: 'Finish' }) : panel.getByRole('button', { name: 'Next' })
      await expect(main).toBeFocused()
      if (i === 2) {
        // The scanned page's text was recognized: Find in document finds a word on it.
        const word = await page.evaluate(() => (window as any).__watchdogTour.useTour.getState().targets.scanned.find)
        await page.locator('.find-box input').fill(word)
        await expect(page.locator('.find-count')).not.toHaveText(/no match/i, { timeout: 10_000 })
        await main.focus()
      }
      if (i === 12) {
        // D307: the two waiting files are added for real, with prepared answers: the run needs no
        // key and no acknowledgement, says so, and leaves a third briefing.
        const modal = page.locator('.modal')
        await expect(modal.getByText('Add documents', { exact: true })).toBeVisible()
        await expect(modal.getByText('Prepared answers, no cost')).toBeVisible({ timeout: 30_000 })
        await modal.getByRole('button', { name: 'Read documents' }).click()
        await expect(modal.locator('.callout', { hasText: 'processed with prepared answers' })).toBeVisible({ timeout: 120_000 })
        await expect(modal.getByText('Acknowledge and add')).toHaveCount(0)
        await expect(modal.locator('.modal-title')).toContainText('Finished', { timeout: 120_000 })
        const sample = JSON.parse(readFileSync(join(home, '.watchdog', 'projects.json'), 'utf8'))['port-calder-waterfront-sample'].path
        const briefings = await page.evaluate(async (v) => (await (window as any).watchdog.rpc('sample.tour', { vault: v })).briefings, sample)
        expect(briefings).toBe(3)
        await main.focus()
      }
      await page.keyboard.press(i % 2 ? 'Enter' : 'Space')
    }
    await expect(panel).toHaveCount(0)
    await expect(page.locator('.tour-ring')).toHaveCount(0)
    expect(seen).toEqual(['overview', 'scanned', 'entity', 'verify', 'contradiction', 'duplicate', 'lead', 'search', 'timeline', 'network', 'usage', 'add', 'ask', 'next'])
    // The Add documents step closed its window on the way out, and nothing was sent anywhere.
    await expect(page.locator('.modal')).toHaveCount(0)

    // Back and Skip, and Help → Take the tour starts again from the first step with the sample
    // already there (no second build).
    await page.evaluate(() => (window as any).__watchdogTour.takeTour())
    await expect(panel.locator('.tour-count')).toHaveText(`Step 1 of ${STEPS}`, { timeout: 30_000 })
    await panel.getByRole('button', { name: 'Next' }).click()
    await expect(panel.locator('.tour-count')).toHaveText(`Step 2 of ${STEPS}`)
    await panel.getByRole('button', { name: 'Back' }).click()
    await expect(panel.locator('.tour-count')).toHaveText(`Step 1 of ${STEPS}`)
    await panel.getByRole('button', { name: 'Skip tour' }).click()
    await expect(panel).toHaveCount(0)

    // The investigations screen offers to open the existing sample or rebuild it.
    await page.evaluate(() => (window as any).__watchdogApp.getState().navigate({ view: 'projects' }))
    await page.getByRole('button', { name: 'Sample investigation', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Rebuild it fresh…' })).toBeVisible()
    await page.keyboard.press('Escape')

    expect(errors).toEqual([])
    await app.close()
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
