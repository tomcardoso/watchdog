// Floating cards for running jobs, bottom-left of the main column, with live progress. A card can
// be minimised to a chip or hidden; the incoming-folder watcher is always a chip.

import { Minus, Square, SquareArrowOutUpRight, X } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Button, Progress } from '@renderer/components/ui'
import { stopJob } from '@renderer/lib/jobs'
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
  relationships: 'Grouping relationship wordings',
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

// Jobs that run until stopped (the incoming-folder watcher) are never shown as a full card: a small
// chip says they are on, so they never sit over a screen for hours.
const BACKGROUND_KINDS = new Set(['watch'])

export function JobDock() {
  const jobs = useApp((s) => s.jobs)
  const route = useApp((s) => s.route)
  const navigate = useApp((s) => s.navigate)
  // Per job: 'min' shows it as a chip, 'hidden' takes it off the dock (it still runs, and Activity
  // still lists it). Forgotten when the job ends, so the next run of anything starts as a card.
  const [shown, setShown] = useState<Record<string, 'min' | 'hidden'>>({})
  const ref = useRef<HTMLDivElement>(null)
  const running = Object.values(jobs).filter((j) => j.state === 'running')
  const runningIds = running.map((j) => j.id).join(' ')
  useEffect(() => {
    setShown((cur) => {
      const keep = Object.fromEntries(Object.entries(cur).filter(([id]) => runningIds.split(' ').includes(id)))
      return Object.keys(keep).length === Object.keys(cur).length ? cur : keep
    })
  }, [runningIds])

  const visible = route.view === 'activity' ? [] : running.filter((j) => shown[j.id] !== 'hidden')
  const cards = visible.filter((j) => !BACKGROUND_KINDS.has(j.kind) && shown[j.id] !== 'min').slice(0, 3)
  const chips = visible.filter((j) => !cards.includes(j))

  // Reserve room under every page for the dock, so the end of a page can always be scrolled clear
  // of it (.page-inner reads --dock-space).
  useLayoutEffect(() => {
    const host = ref.current?.parentElement ?? (document.querySelector('.main-scroll') as HTMLElement | null)
    const el = ref.current
    if (!host) return
    if (!el) {
      host.style.removeProperty('--dock-space')
      return
    }
    const apply = () => host.style.setProperty('--dock-space', `${Math.ceil(el.getBoundingClientRect().height) + 28}px`)
    apply()
    const ro = new ResizeObserver(apply)
    ro.observe(el)
    return () => {
      ro.disconnect()
      host.style.removeProperty('--dock-space')
    }
  }, [visible.length])

  if (!visible.length) return null
  const set = (id: string, v: 'min' | 'hidden' | null) =>
    setShown((cur) => {
      const next = { ...cur }
      if (v) next[id] = v
      else delete next[id]
      return next
    })
  return (
    <div className="job-dock" ref={ref} aria-label="Running jobs">
      {cards.map((j) => {
        const p = progressText(j)
        return (
          <div className="job-card" key={j.id}>
            <div className="row">
              <span className="spinner" />
              <span className="title grow truncate">{j.label}</span>
              <Button variant="ghost" size="sm" icon={SquareArrowOutUpRight} tip="Show output" onClick={() => navigate({ view: 'activity', job: j.id })} />
              <Button variant="ghost" size="sm" icon={Square} tip="Stop" onClick={() => stopJob(j.id)} />
              <Button variant="ghost" size="sm" icon={Minus} tip="Minimise" onClick={() => set(j.id, 'min')} />
            </div>
            <div className="detail truncate">{p.text}</div>
            <Progress value={p.done} max={p.total ?? 1} indeterminate={!p.total} />
          </div>
        )
      })}
      {chips.length > 0 && (
        <div className="job-chips">
          {chips.map((j) => {
            const background = BACKGROUND_KINDS.has(j.kind)
            const p = progressText(j)
            const pct = !background && p.total ? Math.round(((p.done ?? 0) / p.total) * 100) : null
            return (
              <div className="job-chip" key={j.id}>
                <button
                  type="button"
                  className="job-chip-main"
                  data-tip={background ? `${p.text}. Show output` : 'Show progress'}
                  data-tip-pos="top"
                  onClick={() => (background ? navigate({ view: 'activity', job: j.id }) : set(j.id, null))}
                >
                  {background ? <span className="job-chip-dot" aria-hidden="true" /> : <span className="spinner" />}
                  <span className="truncate">{j.label}</span>
                  {pct !== null && <span className="job-chip-pct tnum">{pct}%</span>}
                </button>
                {background && <Button variant="ghost" size="sm" icon={Square} tip="Stop" onClick={() => stopJob(j.id)} />}
                <Button variant="ghost" size="sm" icon={X} tip="Hide (it keeps running; Activity lists it)" onClick={() => set(j.id, 'hidden')} />
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
