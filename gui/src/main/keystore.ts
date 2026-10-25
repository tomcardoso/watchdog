// API keys encrypted with Electron's safeStorage (D295). The main process is the only place a key
// is encrypted or decrypted: it seals each key the reader pastes before the backend stores it in
// ~/.watchdog/credentials.json, and hands the Python backend the keys it decrypted over the
// backend's stdin. Python never decrypts.
//
// This module has no Electron import: the crypto is passed in, so `npm run test:unit` tests it
// with a stand-in. `secrets.ts` binds it to Electron's safeStorage.

import { existsSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'

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
// Settings that are secrets, not a provider's key (D341): `settings.set` for one of these carries
// its value sealed as `value_enc`, and the backend keeps it in credentials.json's `secrets`.
export const SECRET_SETTINGS = new Set(['wayback_access_key', 'wayback_secret_key'])
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
 * `keys[provider].items`, and each secret setting under `secrets` (D341). Labels, ids and the
 * default are left alone. */
function secrets(state: Record<string, unknown>): unknown[] {
  const out: unknown[] = []
  const settings = state?.secrets
  if (settings && typeof settings === 'object') out.push(...Object.values(settings as Record<string, unknown>))
  const keys = state?.keys
  if (!keys || typeof keys !== 'object') return out
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
  if (method === 'settings.set' && params && typeof params === 'object') {
    const { value_enc: _drop, ...rest } = params as Record<string, unknown>
    const value = rest.value
    if (SECRET_SETTINGS.has(String(rest.key)) && status?.store === 'encrypted' && typeof value === 'string' && value.trim()) {
      return { ...rest, value_enc: seal(c, value.trim()) }
    }
    return rest
  }
  if (!KEY_METHODS.has(method) || !params || typeof params !== 'object') return params
  const { key_enc: _ignored, ...rest } = params as Record<string, unknown>
  const key = rest.key
  if (status?.store === 'encrypted' && typeof key === 'string' && key.trim()) {
    return { ...rest, key_enc: seal(c, key.trim()) }
  }
  return rest
}

function writePrivate(file: string, data: unknown): void {
  const tmp = join(dirname(file), `.${Date.now()}.tmp`)
  writeFileSync(tmp, JSON.stringify(data, null, 2) + '\n', { mode: 0o600 })
  renameSync(tmp, file)
}

/** Move secret settings still in plain text in config.json (from before D341) into
 * credentials.json, sealed, then drop them from config.json. Only with real encryption: without
 * it they stay where they are, in a file only the user's account can read. Returns how many moved. */
export function moveSettingSecrets(c: Crypto, status: StoreStatus, credFile: string, configFile: string): number {
  if (status.store !== 'encrypted' || !existsSync(configFile)) return 0
  let cfg: Record<string, unknown>
  try {
    cfg = JSON.parse(readFileSync(configFile, 'utf8'))
  } catch {
    return 0
  }
  if (!cfg || typeof cfg !== 'object') return 0
  const found = [...SECRET_SETTINGS].filter((k) => typeof cfg[k] === 'string')
  if (!found.length) return 0
  const state = readState(credFile) ?? {}
  const box = (state.secrets && typeof state.secrets === 'object' ? state.secrets : {}) as Record<string, unknown>
  for (const k of found) {
    const v = (cfg[k] as string).trim()
    // A value already stored with the keys is newer than the leftover copy.
    if (v && !(k in box)) box[k] = { ...seal(c, v), masked: mask(v) }
    delete cfg[k]
  }
  if (Object.keys(box).length) state.secrets = box
  writePrivate(credFile, state)
  writePrivate(configFile, cfg)
  return found.length
}
