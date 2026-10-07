// Runs the managed-engine installer without Electron, for checking it on a machine with no
// display (or in CI). Node 22.18+ runs the TypeScript directly.
//
//   node scripts/engine-cli.mjs --user-data /tmp/wd-ud [--fresh] [--dev]
//
// Installs into <user-data>/engine exactly as the app does and prints each step as it changes.

import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const args = process.argv.slice(2)
const opt = (n) => (args.includes(`--${n}`) ? args[args.indexOf(`--${n}`) + 1] : undefined)
const userData = resolve(opt('user-data') ?? 'engine-test')
const { Engine } = await import('../src/main/engine.ts')

let last = ''
const engine = new Engine({
  userData,
  resources: join(here, '..', 'resources'),
  isPackaged: false,
  repoRoot: resolve(here, '..', '..'),
  emit: (e) => {
    for (const l of e.log) if (args.includes('--verbose')) console.log('   |', l)
    const line = e.steps.map((s) => `${s.id}:${s.state}`).join(' ')
    if (line !== last) console.log(e.state, line)
    last = line
  }
})
console.log('status before:', JSON.stringify({ ...engine.status(), run: undefined }))
const started = Date.now()
const state = await engine.install({ fresh: args.includes('--fresh') })
console.log(`install ${state} in ${Math.round((Date.now() - started) / 1000)}s`)
const s = engine.status()
console.log('status after:', JSON.stringify({ ...s, run: undefined }))
if (state !== 'done') {
  console.error(s.run.error)
  process.exit(1)
}
