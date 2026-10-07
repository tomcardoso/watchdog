// `wdfile://` serves files from inside registered vaults to the renderer (PDF pages, images,
// thumbnails), so the renderer never needs blanket file:// access. A request for anything outside
// an allowed root is refused.

import { net, protocol } from 'electron'
import { realpathSync } from 'node:fs'
import { isAbsolute, relative, resolve, sep } from 'node:path'
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

export function handleProtocol(): void {
  protocol.handle(SCHEME, async (request) => {
    // wdfile://local/<encodeURIComponent(absolute path)> — see fileUrl() in the preload script.
    const url = new URL(request.url)
    const abs = resolve(decodeURIComponent(url.pathname.slice(1)))
    if (!allowed(abs)) return new Response('Forbidden', { status: 403 })
    return net.fetch(pathToFileURL(abs).toString())
  })
}
