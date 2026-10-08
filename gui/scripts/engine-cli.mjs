// Runs the managed-engine installer without Electron, for checking it on a machine with no
// display (or in CI). Node 22.18+ runs the TypeScript directly.
//
//   node scripts/engine-cli.mjs --user-data /tmp/wd-ud [--fresh] [--verbose] [--home /tmp/h]
//        [--core-only] [--cancel-after <seconds>] [--simulate <value>]
//
// Installs into <user-data>/engine exactly as the app does and prints each step as it changes,
// with a line when phase 1 is in place (the moment the app would restart its backend). --home
// sets HOME for the model step, so the models download somewhere disposable. --core-only stops
// after phase 1 (cancelling at the switch, as quitting the app would), and --cancel-after stops
// the run that many seconds in; run the command again to check that it resumes. --simulate sets
// WATCHDOG_ENGINE_SIMULATE (e.g. "resume,slow-phase2") to exercise the stand-in.

import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const args = process.argv.slice(2)
const opt = (n) => (args.includes(`--${n}`) ? args[args.indexOf(`--${n}`) + 1] : undefined)
const userData = resolve(opt('user-data') ?? 'engine-test')
const { Engine } = await import('../src/main/engine.ts')

const env = { ...process.env }
if (opt('home')) env.HOME = resolve(opt('home'))
if (opt('simulate')) env.WATCHDOG_ENGINE_SIMULATE = opt('simulate')

const started = Date.now()
const secs = () => Math.round((Date.now() - started) / 1000)
let last = ''
const engine = new Engine({
  userData,
  resources: join(here, '..', 'resources'),
  isPackaged: false,
  repoRoot: resolve(here, '..', '..'),
  env,
  emit: (e) => {
    for (const l of e.log) if (args.includes('--verbose')) console.log('   |', l)
    const line = `phase ${e.phase} ` + e.steps.map((s) => `${s.id}:${s.state}`).join(' ')
    if (line !== last) console.log(`${secs()}s`, e.state, line)
    last = line
  }
})
const brief = (s) => JSON.stringify({ ...s, run: undefined, dir: undefined })
console.log('status before:', brief(engine.status()))
if (opt('cancel-after')) setTimeout(() => engine.cancel(), Number(opt('cancel-after')) * 1000)
const state = await engine.install({
  fresh: args.includes('--fresh'),
  onCore: () => {
    console.log(`phase 1 in place after ${secs()}s: engine=${engine.engineState()} complete=${engine.complete()}`)
    if (args.includes('--core-only')) engine.cancel()
  }
})
console.log(`install ${state} in ${secs()}s`)
const s = engine.status()
console.log('status after:', brief(s))
if (state !== 'done') {
  console.error(s.run.error)
  process.exit(args.includes('--core-only') || opt('cancel-after') ? 0 : 1)
}
