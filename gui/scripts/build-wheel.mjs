// Builds a wheel of the `watchdog-intel` package in this repository into resources/python-wheel/.
// electron-builder ships it as extraResources; the app installs it into the managed engine on a
// user's computer, so the engine is always the same version as the app (src/main/engine.ts).
//
//   node scripts/build-wheel.mjs
//
// Uses the uv in resources/bin for this computer (fetch-uv.mjs), else a uv on the PATH.

import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, readdirSync, rmSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const repo = resolve(here, '..', '..')
const out = resolve(here, '..', 'resources', 'python-wheel')

const os = { darwin: 'mac', linux: 'linux', win32: 'win' }[process.platform]
const bundled = join(resolve(here, '..', 'resources', 'bin', `${os}-${process.arch}`), process.platform === 'win32' ? 'uv.exe' : 'uv')
const uv = existsSync(bundled) ? bundled : 'uv'

rmSync(out, { recursive: true, force: true })
mkdirSync(out, { recursive: true })
const r = spawnSync(uv, ['build', '--wheel', '--out-dir', out, repo], { stdio: 'inherit' })
if (r.error) {
  console.error(`Could not run uv (${r.error.message}). Run fetch-uv.mjs first or install uv.`)
  process.exit(1)
}
if (r.status !== 0) process.exit(r.status ?? 1)
const wheels = readdirSync(out).filter((f) => f.endsWith('.whl'))
if (wheels.length !== 1) {
  console.error(`Expected one wheel in ${out}, found ${wheels.length}.`)
  process.exit(1)
}
console.log(`built ${join(out, wheels[0])}`)
