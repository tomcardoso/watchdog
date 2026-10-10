// Starting and following operations, each run in a worker process (see gui/API.md § jobs, D298).

import type { Job, OpName, OpParams } from '@shared/api'
import { call, invalidate } from './rpc'
import { toast, useApp } from './store'
import { investigationName, isElsewhere, switchTo } from './investigation'

/** Start operation `op` in the open investigation (or `vault`). Resolves once the job is running. */
export async function startJob<O extends OpName>(op: O, params: OpParams[O], label: string, kind: string = op, vault?: string | null): Promise<Job> {
  const v = vault === undefined ? useApp.getState().project?.path ?? null : vault
  const job = await call('jobs.start', { vault: v, op, params, label, kind })
  useApp.getState().upsertJob(job)
  return job
}

/** A quick operation run to completion (rename, describe, requeue…). Resolves with its result;
 * throws with the reason it gives when it fails. */
export async function runAction<R = unknown, O extends OpName = OpName>(op: O, params: OpParams[O], vault?: string | null): Promise<R> {
  const v = vault === undefined ? useApp.getState().project?.path ?? null : vault
  const r = await call('action.run', { vault: v, op, params })
  if (r.code !== 0) throw new Error(r.error || r.log || 'That did not finish.')
  return r.result as R
}

/** Wait for a job to finish; resolves with its final state. */
export function waitForJob(id: string): Promise<Job> {
  return new Promise((resolve) => {
    const check = () => {
      const j = useApp.getState().jobs[id]
      if (j && j.state !== 'running') {
        unsub()
        resolve(j)
      }
    }
    const unsub = useApp.subscribe(check)
    check()
  })
}

export const isRunning = (j: Job) => j.state === 'running'

/** Called for every job.finished event: refresh what the job may have changed, and tell the user.
 * A job that ran in an investigation other than the open one says which, with a way back to it. */
export function onJobFinished(job: Job): void {
  invalidate('vault.', 'review.', 'projects.', 'usage.', 'research.', 'search.', 'ingest.')
  const ok = job.state === 'done'
  const elsewhere = isElsewhere(job.vault) ? investigationName(job.vault) : null
  const where = elsewhere ? ` in ${elsewhere}` : ''
  const action = elsewhere && job.vault ? { label: `Switch to ${elsewhere}`, run: () => void switchTo(job.vault!) } : undefined
  if (job.state === 'cancelled') {
    toast({ kind: 'info', title: `${job.label} stopped${where}`, action })
    return
  }
  // Exit code 2 = stopped partway in a way a re-run resumes (rate limit, pending batch).
  if (job.exit_code === 2) {
    toast({ kind: 'info', title: `${job.label} paused${where}`, body: 'Run it again to continue from where it stopped.', action })
  } else {
    toast({ kind: ok ? 'success' : 'error', title: ok ? `${job.label} finished${where}` : `${job.label} failed${where}`, body: ok ? undefined : 'Open Activity to see the output.', action })
  }
  const named = job.vault ? ` (${investigationName(job.vault)})` : ''
  window.watchdog.notify(ok ? `${job.label} finished${named}` : `${job.label} needs attention${named}`, 'Watchdog')
}

/** Stop a job, saying so when the stop could not be sent (the job may have ended already). */
export function stopJob(id: string): void {
  call('jobs.cancel', { id }).catch((e) => toast({ kind: 'error', title: 'Could not stop it', body: e instanceof Error ? e.message : String(e) }))
}
