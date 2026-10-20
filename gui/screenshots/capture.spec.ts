// Screenshots of the sample investigation for the README, the docs and the website
// (`npm run screenshots`, after `npm run build`; needs WATCHDOG_PYTHON, and xvfb-run on headless
// Linux). Builds the Port Calder sample in a throwaway home, then captures each screen at twice
// the pixel density, in the light and the dark theme, into docs/images/ (`app-<name>.png` and
// `app-<name>-dark.png`). Nothing here is a test: it fails only if a screen cannot be reached.

import { _electron as electron, expect, test } from '@playwright/test'
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const OUT = resolve(__dirname, '..', '..', 'docs', 'images')
const python = process.env.WATCHDOG_PYTHON ?? 'python3'
const W = 1440
const H = 900

test('capture the sample investigation', async () => {
  test.setTimeout(900_000)
  mkdirSync(OUT, { recursive: true })
  const root = mkdtempSync(join(tmpdir(), 'wd-shots-'))
  const home = join(root, 'home')
  const inv = join(root, 'Investigations')
  mkdirSync(join(home, '.watchdog'), { recursive: true })
  mkdirSync(inv)
  writeFileSync(join(home, '.watchdog', 'config.json'), JSON.stringify({ projects_dir: inv }))
  writeFileSync(join(home, '.watchdog', 'access.json'), JSON.stringify({ version: 1, folders: [{ path: inv, label: 'new investigations', granted: new Date().toISOString() }] }))

  // The sidebar and About show the app's version, which Electron reads from package.json; a local
  // build is 0.1.0 (CI sets the real one from the tag). Show the release number, pyproject.toml's,
  // for the length of the capture.
  const pkgPath = resolve(__dirname, '..', 'package.json')
  const pkg = readFileSync(pkgPath, 'utf8')
  const release = /^version = "(.+)"$/m.exec(readFileSync(resolve(__dirname, '..', '..', 'pyproject.toml'), 'utf8'))![1]
  writeFileSync(pkgPath, pkg.replace(/"version": "[^"]+"/, `"version": "${release}"`))
  try {
    const app = await electron.launch({
      args: [resolve(__dirname, '..'), '--no-sandbox'],
      env: { ...process.env, HOME: home, WATCHDOG_PYTHON: python, WATCHDOG_SRC: resolve(__dirname, '..', '..', 'src') }
    })
    const page = await app.firstWindow()
    await page.setViewportSize({ width: W, height: H })
    const cdp = await page.context().newCDPSession(page)
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 2, mobile: false })
    await page.waitForSelector('.app', { timeout: 90_000 })

    await page.getByRole('button', { name: 'Explore a sample investigation' }).click()
    await page.getByRole('button', { name: 'Build the sample' }).click()
    const panel = page.locator('.tour-panel')
    await expect(panel).toBeVisible({ timeout: 120_000 })
    await panel.getByRole('button', { name: 'Skip tour' }).click()
    await expect(panel).toHaveCount(0)

    const store = (fn: string, arg?: unknown) =>
      page.evaluate(([f, a]) => (window as any).__watchdogApp.getState()[f as string](a), [fn, arg] as const)
    const go = async (route: object, settle = 3000) => { await store('navigate', route); await page.waitForTimeout(settle) }
    const toTop = () => page.evaluate(() => document.querySelectorAll('*').forEach((e) => { if ((e as HTMLElement).scrollTop > 0) (e as HTMLElement).scrollTop = 0 }))
    const t = await page.evaluate(() => (window as any).__watchdogTour.useTour.getState().targets)

    // Each shot: navigate, settle, then capture in both themes.
    const shots: { name: string; run: () => Promise<void>; keepScroll?: boolean }[] = [
      { name: 'overview', run: () => go({ view: 'home' }) },
      { name: 'document', run: () => go({ view: 'document', sha: t.scanned.sha, page: t.scanned.page }, 5000) },
      { name: 'entity', run: () => go({ view: 'entity', id: t.entity.id }) },
      { name: 'review', run: () => go({ view: 'review', kind: 'contradictions' }) },
      { name: 'timeline', run: () => go({ view: 'timeline' }) },
      { name: 'network', run: () => go({ view: 'graph' }, 9000) },
      { name: 'usage', run: () => go({ view: 'activity', tab: 'usage' }) },
      { name: 'projects', run: () => go({ view: 'projects' }) },
      {
        name: 'add',
        run: async () => {
          await go({ view: 'home' })
          await store('openAdd', [])
          await expect(page.locator('.modal').getByText('Prepared answers, no cost')).toBeVisible({ timeout: 30_000 })
          await page.waitForTimeout(1000)
        }
      },
      {
        name: 'ask',
        keepScroll: true,
        run: async () => {
          await store('closeAdd')
          await go({ view: 'ask' })
          await page.locator('.chat-sample-q').first().click()
          await page.waitForTimeout(25_000)
          await toTop()
        }
      }
    ]

    for (const s of shots) {
      await s.run()
      for (const theme of ['light', 'dark'] as const) {
        await store('setTheme', theme)
        await page.waitForTimeout(s.name === 'network' ? 2500 : 900)
        if (!s.keepScroll) await toTop()
        await page.mouse.move(W - 4, H - 4)
        await page.waitForTimeout(400)
        // Chrome's own capture, which honours the 2x device metrics (page.screenshot does not, in
        // Electron).
        const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' })
        writeFileSync(join(OUT, `app-${s.name}${theme === 'dark' ? '-dark' : ''}.png`), Buffer.from(data, 'base64'))
      }
      await store('setTheme', 'light')
    }
    await app.close()
  } finally {
    writeFileSync(pkgPath, pkg)
  }
})
