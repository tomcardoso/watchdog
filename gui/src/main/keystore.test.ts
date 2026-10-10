// Unit tests for encrypted API keys in the main process (D295). Run with `npm run test:unit`.
// safeStorage is replaced by a stand-in whose ciphertext is the key reversed and tagged, so a test
// can tell an encrypted value from the key; the real one is the operating system's encryption.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { ENC_TAG, mask, migrate, prepare, revealAll, sealParams, storeStatus, type Crypto } from './keystore.ts'

function fakeCrypto(opts: { available?: boolean; backend?: string; broken?: boolean } = {}): Crypto {
  return {
    isEncryptionAvailable: () => opts.available ?? true,
    getSelectedStorageBackend: () => opts.backend ?? 'gnome_libsecret',
    encryptString: (s: string) => Buffer.from('ENC:' + [...s].reverse().join('')),
    decryptString: (b: Buffer) => {
      const t = b.toString()
      if (opts.broken || !t.startsWith('ENC:')) throw new Error('Error while decrypting the ciphertext provided to safeStorage.decryptString.')
      return [...t.slice(4)].reverse().join('')
    }
  }
}

function tempFile(content: unknown): { file: string; dir: string } {
  const dir = mkdtempSync(join(tmpdir(), 'wd-keys-'))
  const file = join(dir, 'credentials.json')
  writeFileSync(file, JSON.stringify(content), { mode: 0o600 })
  return { file, dir }
}

const plainFile = () => ({
  mode: 'api-key',
  keys: {
    anthropic: 'sk-ant-api03-ANTHROPIC-1111',
    openai: { default: 'k-1', items: [{ id: 'k-1', label: 'Personal', key: 'sk-proj-PERSONAL-2222' }, { id: 'k-2', label: 'Work', key: 'sk-proj-WORK-3333' }] }
  }
})

test('the mask matches the one Python shows', () => {
  assert.equal(mask('sk-ant-api03-ANTHROPIC-1111'), 'sk-ant-api…1111')
  assert.equal(mask('short'), '…rt')
  assert.equal(mask('ab'), '(set)')
})

test('storage is encrypted on macOS and Windows, and on Linux only with a real keyring', () => {
  assert.deepEqual(storeStatus(fakeCrypto(), 'darwin'), { store: 'encrypted', backend: 'keychain', reason: null })
  assert.deepEqual(storeStatus(fakeCrypto(), 'win32'), { store: 'encrypted', backend: 'dpapi', reason: null })
  assert.equal(storeStatus(fakeCrypto({ backend: 'kwallet6' }), 'linux').store, 'encrypted')
  // basic_text is a fixed password built into Chromium: obfuscation, not encryption.
  assert.deepEqual(storeStatus(fakeCrypto({ backend: 'basic_text' }), 'linux'), { store: 'plaintext', backend: 'basic_text', reason: 'basic_text' })
  assert.deepEqual(storeStatus(fakeCrypto({ available: false }), 'darwin'), { store: 'plaintext', backend: 'keychain', reason: 'unavailable' })
})

test('migration encrypts every key in place and keeps the structure readable', () => {
  const { file, dir } = tempFile(plainFile())
  try {
    const r = prepare(fakeCrypto(), 'darwin', file)
    assert.equal(r.migrated, 3)
    const text = readFileSync(file, 'utf8')
    for (const k of ['ANTHROPIC-1111', 'PERSONAL-2222', 'WORK-3333']) assert.ok(!text.includes(k), `${k} left in plain text`)
    const data = JSON.parse(text)
    assert.equal(data.mode, 'api-key')
    assert.equal(data.keys.anthropic.enc, ENC_TAG)
    assert.equal(data.keys.anthropic.masked, 'sk-ant-api…1111')
    assert.deepEqual(data.keys.openai.items.map((i: { label: string }) => i.label), ['Personal', 'Work'])
    assert.equal(data.keys.openai.default, 'k-1')
    // 0600 (a Unix permission; Windows has no equivalent mode bits), atomically: no temporary
    // file is left beside it.
    if (process.platform !== 'win32') assert.equal(statSync(file).mode & 0o777, 0o600)
    assert.deepEqual(readdirSync(dir), ['credentials.json'])
    // Every key reaches the backend, decrypted, keyed by its blob.
    assert.deepEqual(Object.values(r.provision.keys).sort(), ['sk-ant-api03-ANTHROPIC-1111', 'sk-proj-PERSONAL-2222', 'sk-proj-WORK-3333'])
    assert.equal(r.provision.store, 'encrypted')
    // A second start finds nothing left to migrate.
    assert.equal(prepare(fakeCrypto(), 'darwin', file).migrated, 0)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('without secure storage the file is left as it is and keys still reach the backend', () => {
  const { file, dir } = tempFile(plainFile())
  try {
    const before = readFileSync(file, 'utf8')
    const r = prepare(fakeCrypto({ backend: 'basic_text' }), 'linux', file)
    assert.equal(r.migrated, 0)
    assert.equal(readFileSync(file, 'utf8'), before)
    assert.equal(r.provision.store, 'plaintext')
    assert.equal(r.provision.reason, 'basic_text')
    // Plain-text keys are read from the file by Python itself; nothing to decrypt.
    assert.deepEqual(r.provision.keys, {})
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('a key that cannot be decrypted is counted, not passed on', () => {
  const state = plainFile() as Record<string, unknown>
  migrate(state, fakeCrypto())
  assert.deepEqual(revealAll(state, fakeCrypto({ broken: true })), { keys: {}, unreadable: 3 })
  assert.equal(revealAll(state, null).unreadable, 3)
})

test('no file: nothing to migrate or hand over', () => {
  const r = prepare(fakeCrypto(), 'darwin', join(tmpdir(), 'wd-keys-missing', 'credentials.json'))
  assert.equal(r.migrated, 0)
  assert.deepEqual(r.provision.keys, {})
})

test('a pasted key is sealed before it reaches the backend, and only for key methods', () => {
  const c = fakeCrypto()
  const enc = storeStatus(c, 'darwin')
  const out = sealParams('auth.addKey', { provider: 'openai', label: 'Work', key: ' sk-proj-NEW-4444 ' }, c, enc) as Record<string, { enc: string; data: string }>
  assert.equal(out.key_enc.enc, ENC_TAG)
  assert.equal(c.decryptString(Buffer.from(out.key_enc.data, 'base64')), 'sk-proj-NEW-4444')
  // A key_enc from the window is never trusted.
  const forged = { provider: 'openai', key: 'sk-x-1', key_enc: { enc: ENC_TAG, data: 'Zm9v' } }
  const resealed = sealParams('auth.setKey', forged, c, enc) as Record<string, { data: string }>
  assert.equal(c.decryptString(Buffer.from(resealed.key_enc.data, 'base64')), 'sk-x-1')
  const basic = storeStatus(fakeCrypto({ backend: 'basic_text' }), 'linux')
  assert.ok(!('key_enc' in (sealParams('auth.setKey', forged, c, basic) as Record<string, unknown>)))
  // Without secure storage nothing is sealed; other methods pass through untouched.
  const plain = sealParams('auth.setKey', { provider: 'openai', key: 'sk-x-1' }, c, basic) as Record<string, unknown>
  assert.ok(!('key_enc' in plain))
  const other = { vault: '/v' }
  assert.equal(sealParams('vault.summary', other, c, enc), other)
})
