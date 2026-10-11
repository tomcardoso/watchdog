// Unit tests for encrypted API keys in the main process (D295). Run with `npm run test:unit`.
// safeStorage is replaced by a stand-in whose ciphertext is the key reversed and tagged, so a test
// can tell an encrypted value from the key; the real one is the operating system's encryption.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { ENC_TAG, mask, prepare, revealAll, seal, sealParams, storeStatus, type Crypto } from './keystore.ts'

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

const KEYS = { 'k-1': 'sk-ant-api03-ANTHROPIC-1111', 'k-2': 'sk-proj-PERSONAL-2222', 'k-3': 'sk-proj-WORK-3333' }

/** A credentials file as the app writes it: labelled items, each key sealed or (with no secure
 * storage) in plain text. */
function credentials(sealed: Crypto | null) {
  const item = (id: keyof typeof KEYS, label: string) => ({
    id, label, key: sealed ? { ...seal(sealed, KEYS[id]), masked: mask(KEYS[id]) } : KEYS[id]
  })
  return {
    mode: 'api-key',
    keys: {
      anthropic: { default: 'k-1', items: [item('k-1', 'Main')] },
      openai: { default: 'k-2', items: [item('k-2', 'Personal'), item('k-3', 'Work')] }
    }
  }
}

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

test('every sealed key reaches the backend, decrypted, keyed by its blob, and the file is untouched', () => {
  const { file, dir } = tempFile(credentials(fakeCrypto()))
  try {
    const before = readFileSync(file, 'utf8')
    for (const k of Object.values(KEYS)) assert.ok(!before.includes(k), `${k} stored in plain text`)
    const r = prepare(fakeCrypto(), 'darwin', file)
    assert.equal(readFileSync(file, 'utf8'), before)
    assert.deepEqual(Object.values(r.provision.keys).sort(), Object.values(KEYS).sort())
    assert.equal(r.provision.store, 'encrypted')
    assert.equal(r.provision.unreadable, 0)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('without secure storage the file is left as it is and keys still reach the backend', () => {
  const { file, dir } = tempFile(credentials(null))
  try {
    const before = readFileSync(file, 'utf8')
    const r = prepare(fakeCrypto({ backend: 'basic_text' }), 'linux', file)
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
  const state = credentials(fakeCrypto()) as Record<string, unknown>
  assert.deepEqual(revealAll(state, fakeCrypto({ broken: true })), { keys: {}, unreadable: 3 })
  assert.equal(revealAll(state, null).unreadable, 3)
})

test('no file: nothing to hand over', () => {
  const r = prepare(fakeCrypto(), 'darwin', join(tmpdir(), 'wd-keys-missing', 'credentials.json'))
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
