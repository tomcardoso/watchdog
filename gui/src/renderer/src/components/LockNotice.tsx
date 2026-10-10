// A held processing lock, explained (Home's Finish adding banner, the Add documents dialog). A lock
// holds `pid: cli`, never a process id, so the app cannot tell whether the run that took it is
// alive: it says what it knows (the lock's last stamp, whether a run from the app is going)
// and offers the forced release behind its confirmation when nothing from here is running.

import { LockOpen, SquareArrowOutUpRight } from 'lucide-react'
import type { LockInfo, PipelineState } from '@shared/api'
import { Button, Callout } from '@renderer/components/ui'
import { fmtDateTime, fmtRelative } from '@renderer/lib/format'
import { forceReleaseLock } from '@renderer/lib/jobs'
import { useApp } from '@renderer/lib/store'

/** The lock that would stop a run starting now: held and not old enough for a run to take it
 * over. The processing lock first, then pre-processing. */
export function blockingLock(pipe: PipelineState | null | undefined): LockInfo | null {
  const held = [pipe?.lock_info?.ingest, pipe?.lock_info?.chew].find((l) => l && !l.stale)
  if (held) return held
  // An older backend gave only booleans: treat a held lock as blocking, time unknown.
  if (!pipe?.lock_info && (pipe?.locks.ingest || pipe?.locks.chew))
    return { started_at: null, age_seconds: null, stale: false, stale_seconds: 1800, heartbeat_seconds: 300 }
  return null
}

export function LockNotice({ vault, lock }: { vault: string; lock: LockInfo }) {
  const jobs = useApp((s) => s.jobs)
  const navigate = useApp((s) => s.navigate)
  const own = Object.values(jobs).find((j) => j.state === 'running' && j.vault === vault)
  if (own)
    return (
      <Callout
        tone="info"
        title="A run is in progress"
        action={<Button size="sm" icon={SquareArrowOutUpRight} onClick={() => navigate({ view: 'activity', job: own.id })}>Show progress</Button>}
      >
        {own.label} is working on this investigation. You can add more documents when it finishes.
      </Callout>
    )
  const every = Math.round(lock.heartbeat_seconds / 60)
  const left = lock.age_seconds === null ? null : Math.max(1, Math.ceil((lock.stale_seconds - lock.age_seconds) / 60))
  return (
    <Callout
      tone="warning"
      title="This investigation is locked by a run"
      action={<Button size="sm" icon={LockOpen} onClick={() => void forceReleaseLock(vault)}>Release the lock…</Button>}
    >
      {lock.started_at ? (
        <>
          The lock was last renewed {fmtDateTime(lock.started_at)} ({fmtRelative(lock.started_at)}); a working run renews it every {every} minutes.{' '}
        </>
      ) : (
        <>The lock carries no time, so Watchdog cannot tell how old it is. </>
      )}
      No run started from the app is going on now, so one may have stopped without releasing the investigation, for example if Watchdog quit or the computer shut down. If nothing is running, release the lock.{' '}
      {left !== null ? `Otherwise the next run can take it over in about ${left} minute${left === 1 ? '' : 's'}.` : 'It does not expire on its own.'}
    </Callout>
  )
}
