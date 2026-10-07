// Disk cache for document thumbnails. The renderer draws a first page once (pdf.js) and hands the
// PNG here; later visits read it back instead of re-rendering the PDF.

import { app } from 'electron'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

function dir(): string {
  const d = join(app.getPath('userData'), 'thumbnails')
  mkdirSync(d, { recursive: true })
  return d
}

function safe(key: string): string {
  return key.replace(/[^a-zA-Z0-9_.-]/g, '_').slice(0, 180)
}

export function getThumb(key: string): string | null {
  const p = join(dir(), safe(key) + '.png')
  if (!existsSync(p)) return null
  return 'data:image/png;base64,' + readFileSync(p).toString('base64')
}

export function putThumb(key: string, dataUrl: string): void {
  const m = /^data:image\/png;base64,(.+)$/.exec(dataUrl)
  if (!m) return
  writeFileSync(join(dir(), safe(key) + '.png'), Buffer.from(m[1], 'base64'))
}
