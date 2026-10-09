// Floating cards for running jobs, bottom-left of the main column, with live progress.

import { Square, SquareArrowOutUpRight } from 'lucide-react'
import { Button, Progress } from '@renderer/components/ui'
import { call } from '@renderer/lib/rpc'
import { useApp } from '@renderer/lib/store'
import type { Job } from '@shared/api'

export const STAGE_LABELS: Record<string, string> = {
  chew: 'Pre-processing (on this computer)',
  model: 'Downloading a local model',
  extract: 'Processing (model extraction)',
  dig: 'Processing (model extraction)',
  fold: 'Merging exact duplicates',
  reconcile: 'Reconciling entities',
  commit: 'Writing to the vault',
  contradictions: 'Flagging contradictions',
  recheck: 'Re-checking contradictions',
  synthesis: 'Writing entity summaries',
  timeline: 'Reconciling the timeline',
  briefing: 'Writing the briefing',
  leads: 'Sweeping for leads',
  requests: 'Updating document requests',
  research: 'Downloading sources',
  done: 'Finishing up'
}

export function progressText(job: Job): { text: string; done: number | null; total: number | null } {
  const p = job.progress
  const docs = Object.values(p.docs ?? {})
  const stage = p.stage ? STAGE_LABELS[p.stage] ?? p.stage : null
  if (docs.length && (p.stage === 'extract' || p.stage === 'dig' || !p.stage)) {
    const finished = docs.filter((d) => d.state === 'done' || d.state === 'failed').length
    return { text: `${stage ?? 'Extracting'} · ${finished} of ${p.total ?? docs.length} documents`, done: finished, total: p.total ?? docs.length }
  }
  // A one-time model download names itself and its size (D273): "Downloading the transcription
  // model (486 MB)", with the megabytes as the bar.
  if (p.stage === 'model') return { text: `${p.current ?? 'Downloading a model'}…`, done: p.done, total: p.total }
  if (stage) {
    const of = p.total ? ` · ${p.done ?? 0} of ${p.total}` : ''
    return { text: stage + of + (p.note ? ` · ${p.note}` : ''), done: p.done, total: p.total }
  }
  return { text: p.current ?? 'Working…', done: null, total: null }
}

export function JobDock() {
  const jobs = useApp((s) => s.jobs)
  const route = useApp((s) => s.route)
  const navigate = useApp((s) => s.navigate)
  const running = Object.values(jobs).filter((j) => j.state === 'running')
  if (!running.length || route.view === 'activity') return null
  return (
    <div className="job-dock">
      {running.slice(0, 3).map((j) => {
        const p = progressText(j)
        return (
          <div className="job-card" key={j.id}>
            <div className="row">
              <span className="spinner" />
              <span className="title grow truncate">{j.label}</span>
              <Button variant="ghost" size="sm" icon={SquareArrowOutUpRight} tip="Show output" onClick={() => navigate({ view: 'activity', job: j.id })} />
              <Button variant="ghost" size="sm" icon={Square} tip="Stop" onClick={() => void call('jobs.cancel', { id: j.id })} />
            </div>
            <div className="detail truncate">{p.text}</div>
            <Progress value={p.done} max={p.total ?? 1} indeterminate={!p.total} />
          </div>
        )
      })}
    </div>
  )
}
