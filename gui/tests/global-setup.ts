// Runs once before any Playwright worker starts. Right after `npm ci`, Electron's binary may not be
// on disk yet: `require('electron')` downloads and unpacks it on first use, and parallel workers
// each doing so race on the same file and fail with `spawn ETXTBSY` or `EACCES`. Resolving it here
// once, then running the binary (as Node, so no display is needed), leaves a finished binary for
// every worker.
import { spawnSync } from 'node:child_process'
import { createRequire } from 'node:module'

export default function globalSetup(): void {
  const require = createRequire(__filename)
  const binary = require('electron') as unknown as string
  const run = spawnSync(binary, ['--version'], { env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' }, encoding: 'utf8' })
  if (run.status !== 0) throw new Error(`Electron's binary (${binary}) did not start: ${run.error?.message ?? run.stderr}`)
}
