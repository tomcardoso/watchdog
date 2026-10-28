// Files pre-processing could not read, moved to the system trash (D360). The worker cannot do this:
// the trash is outside the investigation, where Python's folder-access guard refuses writes
// (src/watchdog/access.py), so the main process does it with the operating system's own trash.
// The folder names match vault_paths.py (`incoming/failed/`).

import { existsSync, lstatSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

export function unreadableDir(vault: string): string {
  return join(vault, 'incoming', 'failed')
}

/** The set-aside files, as the Documents screen lists them: visible, not sidecars, not links. */
export function unreadableFiles(vault: string): string[] {
  const dir = unreadableDir(vault)
  if (!existsSync(dir)) return []
  return readdirSync(dir)
    .filter((n) => !n.startsWith('.') && !n.endsWith('.yml'))
    .map((n) => join(dir, n))
    .filter((p) => lstatSync(p).isFile())
    .sort()
}

/** Move every unreadable file and its `<name>.yml` sidecar to the trash. Returns how many files
 * went; stops at the first the trash refuses, leaving the rest where they are. */
export async function trashUnreadable(vault: string, trash: (path: string) => Promise<void>): Promise<number> {
  let n = 0
  for (const f of unreadableFiles(vault)) {
    await trash(f)
    const sidecar = f + '.yml'
    if (existsSync(sidecar) && lstatSync(sidecar).isFile()) await trash(sidecar)
    n++
  }
  return n
}
