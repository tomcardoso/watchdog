// Small JSON preferences file in the app's userData folder (window size, chosen Python, theme…).
// Investigation data never goes here: it lives in the vaults and ~/.watchdog, as for the CLI.

import { app } from 'electron'
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

let cache: Record<string, unknown> | null = null

function file(): string {
  return join(app.getPath('userData'), 'preferences.json')
}

function load(): Record<string, unknown> {
  if (cache) return cache
  try {
    cache = existsSync(file()) ? JSON.parse(readFileSync(file(), 'utf8')) : {}
  } catch {
    cache = {}
  }
  return cache!
}

export async function getPref<T = unknown>(key: string): Promise<T | null> {
  return (load()[key] as T) ?? null
}

export async function setPref(key: string, value: unknown): Promise<void> {
  const data = load()
  if (value === null || value === undefined) delete data[key]
  else data[key] = value
  mkdirSync(app.getPath('userData'), { recursive: true })
  const tmp = file() + '.tmp'
  writeFileSync(tmp, JSON.stringify(data, null, 2))
  renameSync(tmp, file())
}
