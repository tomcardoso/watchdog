// API keys encrypted with Electron's safeStorage (D295). The main process is the only place a key
// is encrypted or decrypted: it seals each key the reader pastes before the backend stores it in
// ~/.watchdog/credentials.json, and hands the Python backend the keys it decrypted over the
// backend's stdin. Python never decrypts.
//
// This module has no Electron import: the crypto is passed in, so `npm run test:unit` tests it
// with a stand-in. `secrets.ts` binds it to Electron's safeStorage.

import { existsSync, readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

export const ENC_TAG = 'safeStorage:v1'

/** An encrypted key as stored: `data` is safeStorage's ciphertext in base64; `masked` is the
 * masked form Settings shows, so a display needs no decryption. */
export interface SealedKey {
  enc: string
  data: string
  masked?: string
}

/** The part of Electron's safeStorage this module uses. */
export interface Crypto {
  isEncryptionAvailable(): boolean
  getSelectedStorageBackend?: () => string
  encryptString(plainText: string): Buffer
  decryptString(encrypted: Buffer): string
}

/** `encrypted`: every key is encrypted with the operating system's secure storage. `plaintext`:
 * no usable secure storage, so keys stay in a file only the user's account can read. */
export interface StoreStatus {
  store: 'encrypted' | 'plaintext'
  backend: string
  reason: 'unavailable' | 'basic_text' | null
}

/** What `secrets.provide` sends the backend. `keys` maps each blob's `data` to its key. */
export interface Provision {
  store: StoreStatus['store']
  backend: string
  reason: StoreStatus['reason']
  keys: Record<string, string>
  unreadable: number
}

// The methods that carry a key the reader pasted, and the one only this process may call.
export const KEY_METHODS = new Set(['auth.setAnthropicMode', 'auth.setKey', 'auth.addKey'])
export const MAIN_ONLY_METHODS = new Set(['secrets.provide'])

export function credentialsFile(): string {
  return join(homedir(), '.watchdog', 'credentials.json')
}

/** Which storage this computer offers. On Linux, Chromium falls back to `basic_text` (a fixed
 * password compiled into the browser) when no keyring is running: that is obfuscation, not
 * encryption, so it counts as none. */
export function storeStatus(c: Crypto, platform: string): StoreStatus {
  let available = false
  try {
    available = c.isEncryptionAvailable()
  } catch {
    available = false
  }
  let backend = platform === 'darwin' ? 'keychain' : platform === 'win32' ? 'dpapi' : 'unknown'
  if (platform !== 'darwin' && platform !== 'win32') {
    try {
      backend = c.getSelectedStorageBackend?.() ?? 'unknown'
    } catch {
      backend = 'unknown'
    }
  }
  if (!available) return { store: 'plaintext', backend, reason: 'unavailable' }
  if (platform !== 'darwin' && platform !== 'win32' && (backend === 'basic_text' || backend === 'unknown')) {
    return { store: 'plaintext', backend, reason: 'basic_text' }
  }
  return { store: 'encrypted', backend, reason: null }
}

/** The masked form Python's `auth._mask` gives: the first ten characters and the last four. */
export function mask(key: string): string {
  if (key.length <= 8) return key.length > 2 ? '…' + key.slice(-2) : '(set)'
  return `${key.slice(0, 10)}…${key.slice(-4)}`
}

export function isSealed(v: unknown): v is SealedKey {
  const o = v as SealedKey | null
  return !!o && typeof o === 'object' && o.enc === ENC_TAG && typeof o.data === 'string' && o.data.length > 0
}

export function seal(c: Crypto, key: string): SealedKey {
  return { enc: ENC_TAG, data: c.encryptString(key).toString('base64') }
}

function unseal(c: Crypto, b: SealedKey): string | null {
  try {
    return c.decryptString(Buffer.from(b.data, 'base64'))
  } catch {
    return null
  }
}

/** Every secret in a credentials file: each labelled item's `key` (D290), under
 * `keys[provider].items`. Labels, ids and the default are left alone. */
function secrets(state: Record<string, unknown>): unknown[] {
  const keys = state?.keys
  if (!keys || typeof keys !== 'object') return []
  const out: unknown[] = []
  for (const v of Object.values(keys as Record<string, unknown>)) {
    const items = (v as { items?: unknown } | null)?.items
    if (!Array.isArray(items)) continue
    for (const item of items as Record<string, unknown>[]) {
      if (item && typeof item === 'object' && 'key' in item) out.push(item.key)
    }
  }
  return out
}

/** Decrypt every blob: `{blob data: key}`, and how many could not be decrypted (a keyring that
 * was reset, or a file copied from another computer or account). */
export function revealAll(state: Record<string, unknown>, c: Crypto | null): { keys: Record<string, string>; unreadable: number } {
  const keys: Record<string, string> = {}
  let unreadable = 0
  for (const v of secrets(state)) {
    if (!isSealed(v)) continue
    const key = c ? unseal(c, v) : null
    if (key) keys[v.data] = key
    else unreadable++
  }
  return { keys, unreadable }
}

export function readState(file: string): Record<string, unknown> | null {
  if (!existsSync(file)) return null
  try {
    const data = JSON.parse(readFileSync(file, 'utf8'))
    return data && typeof data === 'object' ? data : null
  } catch {
    return null
  }
}

/** At each backend start: decrypt every stored key for the backend. A key stored in plain text
 * (on a computer with no usable secure storage) is read by the backend itself. */
export function prepare(c: Crypto, platform: string, file: string): { status: StoreStatus; provision: Provision } {
  const status = storeStatus(c, platform)
  const state = readState(file)
  let available = false
  try {
    available = c.isEncryptionAvailable()
  } catch {
    available = false
  }
  const { keys, unreadable } = state ? revealAll(state, available ? c : null) : { keys: {}, unreadable: 0 }
  return { status, provision: { ...status, keys, unreadable } }
}

/** The params a key-carrying method is sent to the backend with: the key as the reader typed it
 * (for its checks and its masked display) plus `key_enc`, its encrypted form, which is all the
 * backend writes. A `key_enc` from the window is never trusted. */
export function sealParams(method: string, params: unknown, c: Crypto, status: StoreStatus | null): unknown {
  if (!KEY_METHODS.has(method) || !params || typeof params !== 'object') return params
  const { key_enc: _ignored, ...rest } = params as Record<string, unknown>
  const key = rest.key
  if (status?.store === 'encrypted' && typeof key === 'string' && key.trim()) {
    return { ...rest, key_enc: seal(c, key.trim()) }
  }
  return rest
}
