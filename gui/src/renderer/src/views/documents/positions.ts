// Saved OCR line positions (D289), fetched a few pages at a time as a page is shown or searched,
// never the whole document at once.

import { call } from '@renderer/lib/rpc'
import type { PagePositions } from '@shared/api'

const BATCH = 8

export interface PositionsSource {
  has: (page: number) => boolean
  get: (page: number) => Promise<PagePositions | null>
}

export function positionsSource(vault: string, sha: string, pages: number[]): PositionsSource {
  const available = [...new Set(pages)].sort((a, b) => a - b)
  const set = new Set(available)
  const cache = new Map<number, Promise<PagePositions | null>>()
  return {
    has: (page) => set.has(page),
    get(page) {
      if (!set.has(page)) return Promise.resolve(null)
      const hit = cache.get(page)
      if (hit) return hit
      // This page and the next few that have positions and haven't been asked for yet.
      const from = available.indexOf(page)
      const batch = available.slice(from).filter((n) => !cache.has(n)).slice(0, BATCH)
      const req = call('vault.textPositions', { vault, sha, pages: batch })
      for (const n of batch) {
        cache.set(
          n,
          req.then(
            (r) => r.boxes[String(n)] ?? null,
            () => {
              cache.delete(n) // a failed request is asked again next time
              return null
            }
          )
        )
      }
      return cache.get(page)!
    }
  }
}
