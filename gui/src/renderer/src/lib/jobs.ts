// Starting and following `watchdog` subprocess jobs (see gui/API.md § jobs).

import type { Job, RunOptions } from '@shared/api'
import { call, invalidate } from './rpc'
import { toast, useApp } from './store'

/** Start `watchdog <args…>` in the open vault. Resolves once the job is running. */
export async function startJob(args: string[], label: string, kind = args[0], vault?: string | null): Promise<Job> {
  const v = vault === undefined ? useApp.getState().project?.path ?? null : vault
  const job = await call('jobs.start', { vault: v, args, label, kind })
  useApp.getState().upsertJob(job)
  return job
}

/** CLI flags for a pipeline command from RunOptions — the server owns the mapping. */
export async function flagsFor(command: 'add' | 'dig' | 'bark' | 'chew', options: RunOptions): Promise<string[]> {
  return (await call('jobs.flags', { command, options })).args
}

/** A short command that finishes quickly (rename, archive, unlock…). Throws with the CLI's own
 * error text when it fails. */
export async function runAction(args: string[], vault?: string | null): Promise<string> {
  const v = vault === undefined ? useApp.getState().project?.path ?? null : vault
  const r = await call('action.run', { vault: v, args })
  if (r.code !== 0) {
    const msg = (r.stderr || r.stdout).trim().replace(/^Error:\s*/m, '')
    throw new Error(msg || `watchdog ${args[0]} exited with code ${r.code}`)
  }
  return r.stdout
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

/** Called for every job.finished event: refresh what the job may have changed, and tell the user. */
export function onJobFinished(job: Job): void {
  invalidate('vault.', 'review.', 'projects.', 'usage.', 'research.', 'search.', 'ingest.')
  const ok = job.state === 'done'
  if (job.state === 'cancelled') {
    toast({ kind: 'info', title: `${job.label} stopped` })
    return
  }
  // Exit code 2 = stopped partway in a way a re-run resumes (rate limit, pending batch).
  if (job.exit_code === 2) {
    toast({ kind: 'info', title: `${job.label} paused`, body: 'Run it again to continue from where it stopped.' })
  } else {
    toast({ kind: ok ? 'success' : 'error', title: ok ? `${job.label} finished` : `${job.label} failed`, body: ok ? undefined : 'Open Activity to see the output.' })
  }
  window.watchdog.notify(ok ? `${job.label} finished` : `${job.label} needs attention`, 'Watchdog')
}

/** Stop a job, saying so when the stop could not be sent (the job may have ended already). */
export function stopJob(id: string): void {
  call('jobs.cancel', { id }).catch((e) => toast({ kind: 'error', title: 'Could not stop it', body: e instanceof Error ? e.message : String(e) }))
}
