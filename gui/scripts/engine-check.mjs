// Checks of the managed engine's logic (src/main/engine.ts) that need no network: how the record
// is read, when the engine counts as ready or complete, the simulated two-phase run, and the
// PyTorch flags. Node 22.18+ runs the TypeScript directly.
//
//   node scripts/engine-check.mjs        (npm run engine:check)
//
// The real installer is exercised by scripts/engine-cli.mjs, which downloads.

import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const { Engine, PHASES, RECORD_SCHEMA, parseSimulate, torchArgs } = await import('../src/main/engine.ts')

const roots = []
function engineAt({ record, simulate, resources } = {}) {
  const userData = mkdtempSync(join(tmpdir(), 'wd-engine-'))
  roots.push(userData)
  const res = resources ?? join(userData, 'resources')
  mkdirSync(res, { recursive: true })
  const env = { PATH: '' }
  if (simulate) env.WATCHDOG_ENGINE_SIMULATE = simulate
  const events = []
  const engine = new Engine({ userData, resources: res, isPackaged: true, repoRoot: null, env, platform: 'linux', emit: (e) => events.push(e) })
  if (record !== undefined) {
    mkdirSync(join(engine.venvDir(), 'bin'), { recursive: true })
    writeFileSync(engine.venvPython(), '')
    writeFileSync(join(engine.dir, 'engine.json'), JSON.stringify(record))
  }
  return { engine, events }
}
const rec = (over = {}) => ({ schema: RECORD_SCHEMA, wheel: 'source', wheelSha256: 'source', wheelVersion: '1.0.3', python: '3.12', installedAt: '', phases: [...PHASES], models: { ocr: 'ok' }, ...over })

let n = 0
async function check(name, fn) {
  await fn()
  n += 1
  console.log('ok', name)
}

await check('no record: missing, not complete', () => {
  const { engine } = engineAt()
  assert.equal(engine.engineState(), 'missing')
  assert.equal(engine.complete(), false)
})

await check('every phase and the models: ready and complete', () => {
  const { engine } = engineAt({ record: rec() })
  assert.equal(engine.engineState(), 'ready')
  assert.equal(engine.complete(), true)
  assert.equal(engine.status().complete, true)
})

await check('phase 1 only: ready to open, not complete', () => {
  const { engine } = engineAt({ record: rec({ phases: ['core'], models: null }) })
  assert.equal(engine.engineState(), 'ready')
  assert.equal(engine.complete(), false)
})

await check('libraries in but models not yet run: not complete', () => {
  const { engine } = engineAt({ record: rec({ models: null }) })
  assert.equal(engine.complete(), false)
})

await check('a phase this app defines but the record lacks: not complete', () => {
  const { engine } = engineAt({ record: rec({ phases: ['core', 'something-older'] }) })
  assert.equal(engine.engineState(), 'ready')
  assert.equal(engine.complete(), false)
})

await check('an older or newer record format is not read: missing', () => {
  for (const schema of [1, RECORD_SCHEMA + 1]) {
    const { engine } = engineAt({ record: rec({ schema }) })
    assert.equal(engine.engineState(), 'missing')
    assert.equal(engine.managedPython(), null)
    assert.equal(engine.complete(), false)
  }
  const { engine } = engineAt({ record: { schema: RECORD_SCHEMA, wheel: 'x' } }) // phases missing
  assert.equal(engine.engineState(), 'missing')
})

await check('a different bundled wheel: outdated', () => {
  const resources = mkdtempSync(join(tmpdir(), 'wd-res-'))
  roots.push(resources)
  mkdirSync(join(resources, 'python-wheel'))
  writeFileSync(join(resources, 'python-wheel', 'watchdog_intel-1.0.4-py3-none-any.whl'), 'new')
  const { engine } = engineAt({ record: rec({ wheelSha256: 'old' }), resources })
  assert.equal(engine.engineState(), 'outdated')
  assert.equal(engine.complete(), false)
  assert.equal(engine.bundledVersion(), '1.0.4')
})

await check('torch flags: CPU build except on macOS', () => {
  assert.deepEqual(torchArgs('linux'), ['--torch-backend', 'cpu'])
  assert.deepEqual(torchArgs('win32'), ['--torch-backend', 'cpu'])
  assert.deepEqual(torchArgs('darwin'), [])
})

await check('simulate values parse and combine', () => {
  assert.equal(parseSimulate(''), null)
  assert.deepEqual(parseSimulate('1'), { slow: false, slowPhase2: false, resume: false, failAt: null })
  assert.deepEqual(parseSimulate('resume,slow-phase2,fail:gliner'), { slow: false, slowPhase2: true, resume: true, failAt: 'gliner' })
})

await check('simulated run: phase 1, then onCore, then phase 2, then complete', async () => {
  const { engine, events } = engineAt({ simulate: '1' })
  assert.equal(engine.engineState(), 'missing')
  const order = []
  const state = await engine.install({
    onCore: () => {
      order.push(`core ready=${engine.engineState()} complete=${engine.complete()}`)
      const steps = events.at(-1).steps
      assert.ok(steps.filter((s) => s.phase === 1).every((s) => s.state === 'done'))
      assert.ok(steps.filter((s) => s.phase === 2).every((s) => s.state === 'pending'))
    }
  })
  assert.equal(state, 'done')
  assert.deepEqual(order, ['core ready=ready complete=false'])
  assert.ok(events.some((e) => e.phase === 2 && e.state === 'running'))
  assert.equal(engine.complete(), true)
  assert.equal(engine.needsBackground(), false)
})

await check('simulated resume: phase 1 already in place, only phase 2 runs', async () => {
  const { engine, events } = engineAt({ simulate: 'resume' })
  assert.equal(engine.engineState(), 'ready')
  assert.equal(engine.needsBackground(), true)
  await engine.install()
  const firstRunning = events.find((e) => e.steps.some((s) => s.state === 'running'))
  assert.equal(firstRunning.phase, 2)
  assert.equal(engine.complete(), true)
})

await check('a failed phase 2 leaves phase 1 usable and resumes', async () => {
  const { engine } = engineAt({ simulate: 'resume,fail:libraries' })
  assert.equal(await engine.install(), 'failed')
  assert.equal(engine.status().run.phase, 2)
  assert.equal(engine.engineState(), 'ready')
  assert.equal(engine.needsBackground(), true)
  assert.equal(await engine.install(), 'done')
  assert.equal(engine.complete(), true)
})

await check('cancelling phase 2 keeps phase 1 and says setup is paused', async () => {
  const { engine } = engineAt({ simulate: 'resume' })
  const run = engine.install()
  setTimeout(() => engine.cancel(), 300)
  assert.equal(await run, 'cancelled')
  assert.match(engine.status().run.error, /paused/)
  assert.equal(engine.engineState(), 'ready')
})

for (const r of roots) rmSync(r, { recursive: true, force: true })
console.log(`${n} checks passed`)
