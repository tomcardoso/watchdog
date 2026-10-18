// Everything the installer needs to ship the managed engine: the uv binary for each target and
// the watchdog wheel. Runs before `npm run dist*` (see package.json).
//
//   node scripts/prepare-engine.mjs [--target mac|linux|win] [--arch x64,arm64]

import { spawnSync } from 'node:child_process'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const args = process.argv.slice(2)
for (const script of ['fetch-uv.mjs', 'build-wheel.mjs']) {
  const r = spawnSync(process.execPath, [join(here, script), ...(script === 'fetch-uv.mjs' ? args : [])], { stdio: 'inherit' })
  if (r.status !== 0) process.exit(r.status ?? 1)
}
