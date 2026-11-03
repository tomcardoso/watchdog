// Installed-app check: launches an installed copy of Watchdog under Playwright, starts first-run
// setup's engine install (what the Install step's button does), and reports whether the backend
// gets to ready, the point past the start-up gate. Used by .github/workflows/installer-check.yml.
//
//   node scripts/check-installed.mjs <path to the installed executable> <output folder> [minutes]
//
// Writes progress to stdout, and status.json, engine-run.log, window.txt and window.png to the
// output folder. Exits 0 when the backend is ready, 1 otherwise.

import { _electron as electron } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const [exe, out, minutes = '40'] = process.argv.slice(2)
mkdirSync(out, { recursive: true })
const say = (...a) => console.log(new Date().toISOString(), ...a)
const deadline = Date.now() + Number(minutes) * 60_000

const app = await electron.launch({ executablePath: exe, timeout: 120_000 })
const page = await app.firstWindow()
page.on('console', (m) => say('[renderer]', m.type(), m.text()))
page.on('pageerror', (e) => say('[pageerror]', e.message))
const status = () => page.evaluate(async () => ({ backend: await window.watchdog.backend.status(), engine: await window.watchdog.engine.status() }))
const brief = (s) => `backend=${s.backend.state}${s.backend.message ? ` (${s.backend.message.split('\n')[0]})` : ''} engine=${s.engine.state} complete=${s.engine.complete} phase=${s.engine.run.phase ?? '-'} steps=${s.engine.run.steps.map((x) => `${x.id}:${x.state}`).join(',')}`

let s = await status()
say('launched:', brief(s))
await page.evaluate(() => void window.watchdog.engine.install())
let ready = false
while (Date.now() < deadline) {
  await page.waitForTimeout(15_000)
  s = await status()
  say(brief(s))
  if (s.engine.state === 'failed' || s.engine.state === 'cancelled') break
  ready = s.backend.state === 'ready'
  // Ready to use, and the background phase (models) finished or failed.
  if (ready && s.engine.state !== 'installing') break
}

writeFileSync(join(out, 'status.json'), JSON.stringify(s, null, 2))
writeFileSync(join(out, 'engine-run.log'), (s.engine.run.log ?? []).join('\n'))
writeFileSync(join(out, 'window.txt'), await page.evaluate(() => document.body.innerText))
await page.screenshot({ path: join(out, 'window.png') })
say(ready ? 'RESULT: the backend is ready' : `RESULT: the backend is not ready: ${brief(s)}`)
await app.close()
process.exit(ready ? 0 : 1)
