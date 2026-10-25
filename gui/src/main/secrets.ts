// Electron's side of encrypted API keys (D295): binds keystore.ts to safeStorage, encrypts and
// hands keys to the backend when it starts, and seals a pasted key before it reaches the backend.
//
// safeStorage's encryption key belongs to this app, not to the Python engine: on macOS it is the
// "Watchdog Safe Storage" item in the login Keychain, readable without a prompt by an app signed
// with the identity that created it. Engine updates never touch it.

import { safeStorage } from 'electron'
import log from 'electron-log/main'
import { join, dirname } from 'node:path'
import { MAIN_ONLY_METHODS, credentialsFile, moveSettingSecrets, prepare, sealParams, storeStatus, type StoreStatus } from './keystore'

let status: StoreStatus | null = null

/** Run when a backend has started, before the window can reach it: decrypt, hand over. */
export async function provideSecrets(request: (method: string, params: unknown) => Promise<unknown>): Promise<void> {
  let provision
  try {
    // Before decrypting, so a moved Wayback key is handed over in this same start (D341).
    const moved = moveSettingSecrets(safeStorage, storeStatus(safeStorage, process.platform), credentialsFile(), join(dirname(credentialsFile()), 'config.json'))
    if (moved) log.info(`keys: moved ${moved} Wayback Machine key(s) from config.json into encrypted storage`)
    const r = prepare(safeStorage, process.platform, credentialsFile())
    status = r.status
    provision = r.provision
    if (r.status.store === 'plaintext') log.warn(`keys: no usable secure storage (${r.status.backend}); keys stay in a private file`)
    if (r.provision.unreadable) log.warn(`keys: ${r.provision.unreadable} stored key(s) could not be decrypted on this computer`)
  } catch (e) {
    // The file could not be read or rewritten. Say what storage there is and hand over nothing:
    // a run then stops for want of its key rather than using another one.
    log.warn('keys: could not prepare stored keys:', (e as Error)?.message)
    if (!status) status = { store: 'plaintext', backend: 'unknown', reason: 'unavailable' }
    provision = { ...status, keys: {}, unreadable: 0 }
  }
  await request('secrets.provide', provision)
}

/** A window request on its way to the backend: refused when only this process may make it, and
 * with any pasted key sealed. */
export function prepareRequest(method: string, params: unknown): unknown {
  if (MAIN_ONLY_METHODS.has(method)) throw Object.assign(new Error('Not available.'), { code: 'forbidden' })
  return sealParams(method, params, safeStorage, status)
}
