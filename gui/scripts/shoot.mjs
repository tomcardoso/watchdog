// Screenshot harness: launches the built app (npm run build first) under Playwright and captures
// one or more screens. Used for visual QA, in CI-less environments too (wrap in xvfb-run on Linux).
//
//   node scripts/shoot.mjs --out shots --home /tmp/wd-demo-home --project port-calder \
//        --shot home='{"view":"home"}' --shot docs='{"view":"documents"}' [--theme dark] [--size 1440x900]
//
// --home sets HOME for the app and its Python backend, so it sees that sandbox's ~/.watchdog.
// Each --shot is name=<route JSON>; a route may carry "_wait": ms and "_eval": JS to run first.

import { _electron as electron } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { join, resolve } from 'node:path'

const args = process.argv.slice(2)
const opt = (name, def) => {
  const i = args.indexOf(`--${name}`)
  return i >= 0 ? args[i + 1] : def
}
const shots = []
args.forEach((a, i) => {
  if (a === '--shot') {
    const [name, ...rest] = args[i + 1].split('=')
    shots.push({ name, route: JSON.parse(rest.join('=')) })
  }
})
const out = resolve(opt('out', 'shots'))
mkdirSync(out, { recursive: true })
const [w, h] = opt('size', '1440x900').split('x').map(Number)
const env = { ...process.env, WATCHDOG_GUI_DEBUG: process.env.WATCHDOG_GUI_DEBUG ?? '' }
if (opt('home')) env.HOME = resolve(opt('home'))

const app = await electron.launch({ args: [resolve('.'), '--no-sandbox'], env })
const page = await app.firstWindow()
page.on('console', (m) => { if (m.type() === 'error') console.error('[renderer]', m.text()) })
page.on('pageerror', (e) => console.error('[pageerror]', e.message))
await page.setViewportSize({ width: w, height: h })
await page.waitForFunction(() => !!window.__watchdogApp, null, { timeout: 30000 })
const theme = opt('theme')
if (theme) await page.evaluate((t) => window.__watchdogApp.getState().setTheme(t), theme)
const project = opt('project')
if (project) {
  await page.waitForSelector('.app', { timeout: 60000 })
  await page.evaluate(async (slug) => {
    const p = await window.watchdog.rpc('projects.get', { slug })
    window.__watchdogApp.getState().setProject(p)
  }, project)
}
for (const s of shots) {
  const { _wait = 1500, _eval, ...route } = s.route
  if (route.view) await page.evaluate((r) => window.__watchdogApp.getState().navigate(r), route)
  if (_eval) await page.evaluate(_eval)
  await page.waitForTimeout(_wait)
  await page.screenshot({ path: join(out, `${s.name}.png`) })
  console.log('shot', join(out, `${s.name}.png`))
}
if (!shots.length) {
  await page.waitForTimeout(2500)
  await page.screenshot({ path: join(out, 'start.png') })
  console.log('shot', join(out, 'start.png'))
}
await app.close()
