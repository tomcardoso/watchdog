// Unit tests for the engine installer's uv commands. Run with `npm run test:unit`.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { coreInstallArgs } from './engine.ts'

function findUv(): string | null {
  const r = spawnSync('uv', ['--version'], { encoding: 'utf8' })
  return r.status === 0 ? 'uv' : null
}

test('phase 1 names its constraint and requirement files relative to the engine folder', () => {
  const args = coreInstallArgs('/x/venv/bin/python', 'darwin')
  assert.deepEqual(args.slice(args.indexOf('-c')), ['-c', 'requirements.lock', '-r', 'core.txt'])
})

// uv 0.11 splits a `-c` path at its spaces ("File not found: .../Application"), and the engine
// folder is under "Application Support" on a Mac. Run the real command from a folder with a space.
test('phase 1 installs from an engine folder whose path has a space', { skip: !findUv() && 'uv is not installed' }, () => {
  const root = mkdtempSync(join(tmpdir(), 'wd-engine-'))
  const dir = join(root, 'Application Support', 'engine')
  try {
    const env = { ...process.env, UV_NO_CONFIG: '1', UV_OFFLINE: '1', UV_NO_PROGRESS: '1' }
    const venv = spawnSync('uv', ['venv', '--quiet', join(dir, 'venv')], { env, encoding: 'utf8' })
    assert.equal(venv.status, 0, venv.stderr)
    writeFileSync(join(dir, 'requirements.lock'), '# nothing pinned\n')
    writeFileSync(join(dir, 'core.txt'), '# nothing to install\n')
    const python = join(dir, 'venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
    const r = spawnSync('uv', coreInstallArgs(python), { env, cwd: dir, encoding: 'utf8' })
    assert.doesNotMatch(r.stderr, /File not found/, r.stderr)
    assert.equal(r.status, 0, r.stderr)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
