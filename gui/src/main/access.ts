// The list of folders the user has allowed Watchdog to change (~/.watchdog/access.json).
//
// The main process is the only writer. A folder is added when the user chooses it in a folder
// dialog for a purpose that needs it (an investigations folder, an existing investigation to add,
// a move destination), or when they approve a prompt this module shows. The renderer can ask but
// cannot grant silently. Python reads the list and enforces it (src/watchdog/access.py).

import { BrowserWindow, dialog } from 'electron'
import { existsSync, mkdirSync, readFileSync, realpathSync, renameSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, isAbsolute, join, relative, resolve } from 'node:path'

export interface Grant { path: string; label: string; granted: string }
interface AccessFile { version: 1; folders: Grant[] }

export function accessFile(): string {
  return process.env.WATCHDOG_ACCESS_FILE || join(homedir(), '.watchdog', 'access.json')
}

function real(p: string): string {
  try {
    return realpathSync(p)
  } catch {
    return resolve(p)
  }
}

function load(): AccessFile {
  try {
    const data = JSON.parse(readFileSync(accessFile(), 'utf8'))
    if (data && Array.isArray(data.folders)) return { version: 1, folders: data.folders.filter((f: Grant) => typeof f?.path === 'string') }
  } catch {
    /* missing or unreadable: nothing granted */
  }
  return { version: 1, folders: [] }
}

function save(data: AccessFile): void {
  const file = accessFile()
  mkdirSync(dirname(file), { recursive: true })
  const tmp = file + '.tmp'
  writeFileSync(tmp, JSON.stringify(data, null, 2) + '\n', { mode: 0o600 })
  renameSync(tmp, file)
}

export function listGrants(): Grant[] {
  return load().folders
}

function within(p: string, root: string): boolean {
  const rel = relative(root, p)
  return rel === '' || (!rel.startsWith('..') && !isAbsolute(rel))
}

export function isGranted(path: string): boolean {
  const p = real(path)
  return load().folders.some((g) => within(p, real(g.path)))
}

/** Record a grant the user has just made (a folder dialog choice or an approved prompt). A folder
 * already covered by a broader grant is not added twice; narrower grants it covers are folded in. */
export function addGrant(path: string, label: string): Grant[] {
  const p = real(path)
  const data = load()
  if (data.folders.some((g) => within(p, real(g.path)))) return data.folders
  data.folders = data.folders.filter((g) => !within(real(g.path), p))
  data.folders.push({ path: p, label, granted: new Date().toISOString() })
  save(data)
  return data.folders
}

export function revokeGrant(path: string): Grant[] {
  const p = real(path)
  const data = load()
  data.folders = data.folders.filter((g) => real(g.path) !== p)
  save(data)
  return data.folders
}

/** Ask the user, in a native dialog the renderer can't fake, to allow a folder. */
export async function requestGrant(win: BrowserWindow | null, path: string, label: string, why: string): Promise<boolean> {
  if (isGranted(path)) return true
  const opts = {
    type: 'question' as const,
    title: 'Allow access to this folder?',
    message: `Allow Watchdog to read and change files in “${label}”?`,
    detail: `${path}\n\n${why}\n\nWatchdog only changes files in folders you have allowed. You can review and remove access in Settings → Folder access.`,
    buttons: ['Allow', 'Don’t allow'],
    defaultId: 0,
    cancelId: 1
  }
  const r = win ? await dialog.showMessageBox(win, opts) : await dialog.showMessageBox(opts)
  if (r.response !== 0) return false
  // Allowing a folder that doesn't exist yet (the default investigations folder on first run)
  // creates it, so the grant has something to point at.
  if (!existsSync(path)) mkdirSync(path, { recursive: true })
  addGrant(path, label)
  return true
}
