// `wdfile://` serves files from inside registered vaults to the renderer (PDF pages, images,
// thumbnails), so the renderer never needs blanket file:// access. A request for anything outside
// an allowed root is refused.

import { net, protocol } from 'electron'
import { createReadStream, realpathSync, statSync } from 'node:fs'
import { extname, isAbsolute, relative, resolve, sep } from 'node:path'
import { Readable } from 'node:stream'
import { pathToFileURL } from 'node:url'

export const SCHEME = 'wdfile'

let roots: string[] = []

export function setAllowedRoots(paths: string[]): void {
  roots = paths
    .map((p) => {
      try {
        return realpathSync(p)
      } catch {
        return null
      }
    })
    .filter((p): p is string => !!p)
}

export function registerSchemePrivileges(): void {
  protocol.registerSchemesAsPrivileged([
    { scheme: SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true, corsEnabled: true } }
  ])
}

function allowed(abs: string): boolean {
  let real: string
  try {
    real = realpathSync(abs)
  } catch {
    return false
  }
  return roots.some((root) => {
    const rel = relative(root, real)
    return rel === '' || (!rel.startsWith('..') && !isAbsolute(rel) && !rel.startsWith(sep))
  })
}

// Media types for the audio and video the reader plays (D273). Everything else is typed by
// net.fetch's own file handling.
const MEDIA_TYPES: Record<string, string> = {
  mp3: 'audio/mpeg', m4a: 'audio/mp4', aac: 'audio/aac', wav: 'audio/wav', flac: 'audio/flac',
  ogg: 'audio/ogg', oga: 'audio/ogg', opus: 'audio/ogg', mp4: 'video/mp4', m4v: 'video/mp4',
  mov: 'video/quicktime', webm: 'video/webm', mkv: 'video/x-matroska', avi: 'video/x-msvideo'
}

/** Parse a single `bytes=a-b` range against a file of `size` bytes; null when absent or unusable. */
export function parseRange(header: string | null, size: number): { start: number; end: number } | null {
  const m = /^bytes=(\d*)-(\d*)$/.exec((header ?? '').trim())
  if (!m || (m[1] === '' && m[2] === '')) return null
  let start: number
  let end: number
  if (m[1] === '') {
    // A suffix range: the last N bytes.
    start = Math.max(0, size - Number(m[2]))
    end = size - 1
  } else {
    start = Number(m[1])
    end = m[2] === '' ? size - 1 : Math.min(Number(m[2]), size - 1)
  }
  if (start > end || start >= size) return null
  return { start, end }
}

/**
 * Serve a file with byte-range support, which an <audio> or <video> element needs to seek: it
 * asks for `Range: bytes=…` and expects 206 Partial Content back. Every response says
 * `Accept-Ranges: bytes`, so the element knows it can ask.
 */
function serveFile(abs: string, request: Request): Response {
  const size = statSync(abs).size
  const ext = extname(abs).slice(1).toLowerCase()
  const type = MEDIA_TYPES[ext] ?? 'application/octet-stream'
  const header = request.headers.get('range')
  const range = parseRange(header, size)
  if (header && !range) {
    return new Response(null, { status: 416, headers: { 'Content-Range': `bytes */${size}`, 'Accept-Ranges': 'bytes' } })
  }
  const { start, end } = range ?? { start: 0, end: size - 1 }
  const body = size === 0 ? null : (Readable.toWeb(createReadStream(abs, { start, end })) as ReadableStream)
  const headers: Record<string, string> = { 'Content-Type': type, 'Content-Length': String(size === 0 ? 0 : end - start + 1), 'Accept-Ranges': 'bytes' }
  if (range) headers['Content-Range'] = `bytes ${start}-${end}/${size}`
  return new Response(body, { status: range ? 206 : 200, headers })
}

export function handleProtocol(): void {
  protocol.handle(SCHEME, async (request) => {
    // wdfile://local/<encodeURIComponent(absolute path)> — see fileUrl() in the preload script.
    const url = new URL(request.url)
    const abs = resolve(decodeURIComponent(url.pathname.slice(1)))
    if (!allowed(abs)) return new Response('Forbidden', { status: 403 })
    // Recordings, and any request that names a byte range, are served here so seeking works;
    // everything else keeps net.fetch's file handling (types, caching).
    if (request.headers.has('range') || MEDIA_TYPES[extname(abs).slice(1).toLowerCase()]) {
      try {
        return serveFile(abs, request)
      } catch {
        return new Response('Not found', { status: 404 })
      }
    }
    return net.fetch(pathToFileURL(abs).toString())
  })
}
