// The engine's background setup (D272), as the rest of the app sees it: whether every library and
// model is in place yet, the run that is finishing it, and the gate every way of adding documents
// checks. The main process reports progress as 'engine.progress' events (main/engine.ts).

import { useEffect } from 'react'
import { create } from 'zustand'
import type { EngineProgress, EngineStatus, EngineStep } from '@shared/api'
import { invalidate, useEvent } from './rpc'

/** Shown wherever adding documents is unavailable because setup has not finished. */
export const ENGINE_WAIT = 'Watchdog is still setting up. You can add documents when it finishes, in a few minutes.'
/** The same, for the other actions that wait (rebuilding the search index, merging entities). */
export const ENGINE_WAIT_OTHER = 'Watchdog is still setting up. This will be available when it finishes, in a few minutes.'

interface EngineSetup {
  status: EngineStatus | null
  run: EngineProgress | null
  set: (patch: Partial<Pick<EngineSetup, 'status' | 'run'>>) => void
}

export const useEngineStore = create<EngineSetup>((set) => ({ status: null, run: null, set: (patch) => set(patch) }))

/** Keeps the store current. Mounted once, by the app shell. */
export function useEngineSync(): void {
  const set = useEngineStore((s) => s.set)
  useEffect(() => {
    void window.watchdog.engine.status().then((status) => set({ status, run: { ...status.run, log: [] } }))
  }, [set])
  useEvent('engine.progress', (run) => {
    set({ run })
    if (run.state !== 'running') {
      void window.watchdog.engine.status().then((status) => {
        set({ status })
        // The backend now accepts what it refused: let the screens ask again.
        if (status.complete) invalidate('app.', 'search.', 'ingest.')
      })
    }
  })
}

/** Phase 2 steps' share of the background work; the model downloads are the slow part. */
const WEIGHT: Record<string, number> = { libraries: 3, docling: 2, gliner: 6, embedding: 1, reranker: 3, ocr: 1 }

export function backgroundFraction(steps: EngineStep[]): number {
  const bg = steps.filter((s) => s.phase === 2)
  const total = bg.reduce((n, s) => n + (WEIGHT[s.id] ?? 1), 0)
  // A step under way counts for half, so the bar moves during the long single downloads.
  const share = (s: EngineStep) => (['done', 'warning', 'skipped', 'failed'].includes(s.state) ? 1 : s.state === 'running' ? 0.5 : 0)
  const done = bg.reduce((n, s) => n + share(s) * (WEIGHT[s.id] ?? 1), 0)
  return total ? done / total : 0
}

export interface EngineGate {
  /** Documents can be added: setup has finished (or the status is not known yet, so nothing blocks on a slow start). */
  ready: boolean
  /** The background phase is running now. */
  finishing: boolean
  /** The background phase stopped (failed or cancelled) before finishing. */
  stalled: boolean
  /** 0–1 through the background phase, while it runs. */
  fraction: number
  /** ENGINE_WAIT while not ready, else undefined: pass straight to a button's `tip`. */
  reason: string | undefined
}

export function useEngineGate(): EngineGate {
  const status = useEngineStore((s) => s.status)
  const run = useEngineStore((s) => s.run)
  const ready = !status || status.complete
  const finishing = !ready && run?.state === 'running' && run.phase === 2
  return {
    ready,
    finishing,
    stalled: !ready && (run?.state === 'failed' || run?.state === 'cancelled'),
    fraction: run ? backgroundFraction(run.steps) : 0,
    reason: ready ? undefined : ENGINE_WAIT
  }
}

/** Start (or retry) the background phase. */
export function finishSetup(): void {
  void window.watchdog.engine.install()
}
