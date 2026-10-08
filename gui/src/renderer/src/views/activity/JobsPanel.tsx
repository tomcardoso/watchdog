// Jobs: running and recent `watchdog` subprocesses, and a live log for the selected one.

import { ArrowDownToLine, CheckCircle2, Clipboard, OctagonX, PauseCircle, RotateCw, Square, Terminal, XCircle } from 'lucide-react'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { Job, LogLine } from '@shared/api'
import { Badge, Button, Empty, Progress, Spinner, cx } from '@renderer/components/ui'
import { call } from '@renderer/lib/rpc'
import { startJob } from '@renderer/lib/jobs'
import { basename, fmtDateTime, fmtDuration, fmtRelative } from '@renderer/lib/format'
import { toast, useApp } from '@renderer/lib/store'
import { progressText } from '@renderer/shell/JobDock'
import { usePublicRecordsGate } from './PublicRecordsGate'

const SENDS_TO_MODEL = new Set(['add', 'dig', 'ingest'])

function useNow(active: boolean): number {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [active])
  return now
}

export function jobDuration(j: Job, now: number): number {
  const end = j.finished ? Date.parse(j.finished) : now
  return Math.max(0, (end - Date.parse(j.started)) / 1000)
}

function StateBadge({ job }: { job: Job }) {
  if (job.state === 'running')
    return (
      <Badge tone="accent">
        <span className="spinner" style={{ width: 10, height: 10 }} />
        Running
      </Badge>
    )
  if (job.state === 'done') return <Badge tone="success" icon={CheckCircle2}>Finished</Badge>
  if (job.state === 'cancelled') return <Badge icon={Square}>Stopped</Badge>
  if (job.exit_code === 2) return <Badge tone="warning" icon={PauseCircle} tip="Stopped partway in a way a re-run picks up">Paused</Badge>
  return <Badge tone="danger" icon={XCircle}>Failed</Badge>
}


function LogView({ job }: { job: Job & { log: LogLine[] } }) {
  const ref = useRef<HTMLDivElement>(null)
  const stuck = useRef(true)
  const [paused, setPaused] = useState(false)
  const lines = job.log

  useLayoutEffect(() => {
    const el = ref.current
    if (el && stuck.current) el.scrollTop = el.scrollHeight
  }, [lines.length, job.id])
  useEffect(() => {
    stuck.current = true
    setPaused(false)
  }, [job.id])

  const onScroll = () => {
    const el = ref.current
    if (!el) return
    const atEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 28
    stuck.current = atEnd
    setPaused(!atEnd)
  }
  const jump = () => {
    const el = ref.current
    if (el) el.scrollTop = el.scrollHeight
    stuck.current = true
    setPaused(false)
  }
  return (
    <div className="act-log-wrap">
      <div ref={ref} className="act-log selectable" onScroll={onScroll}>
        {lines.length === 0 ? (
          <div className="act-log-empty">{job.state === 'running' ? 'Waiting for output…' : 'This job printed nothing.'}</div>
        ) : (
          lines.map((l, i) => (
            <div key={i} className={cx('act-line', l.stream === 'err' && 'err')}>
              <span className="act-line-t">{l.t ? l.t.slice(11, 19) : ''}</span>
              <span className="act-line-x">{l.text || ' '}</span>
            </div>
          ))
        )}
      </div>
      {paused && (
        <button className="act-jump" onClick={jump}>
          <ArrowDownToLine /> Jump to latest
        </button>
      )}
    </div>
  )
}

function JobDetail({ job }: { job: Job & { log: LogLine[] } }) {
  const now = useNow(job.state === 'running')
  const project = useApp((s) => s.project)
  const gate = usePublicRecordsGate()
  const [stopCount, setStopCount] = useState(0)
  useEffect(() => setStopCount(0), [job.id])
  const prog = progressText(job)
  const running = job.state === 'running'
  const sameVault = !job.vault || job.vault === project?.path

  const stop = async () => {
    if (stopCount >= 1) {
      const ok = await window.watchdog.dialog.confirm({
        title: 'Stop immediately?',
        message: 'This ends the process without letting it save.',
        detail: 'Documents already finished are kept; the one in progress stays queued and starts again next time. A lock may be left behind; Activity → Maintenance → Release a stuck lock clears it.',
        confirm: 'Stop immediately',
        destructive: true
      })
      if (!ok) return
    }
    setStopCount((n) => n + 1)
    await call('jobs.cancel', { id: job.id }).catch((e) => toast({ kind: 'error', title: 'Could not stop the job', body: String(e.message) }))
  }

  const rerun = async () => {
    const args = job.args.filter((a) => a !== '--skip-warning')
    if (SENDS_TO_MODEL.has(job.args[0]) && !job.args.includes('--estimate') && !job.args.includes('--estimate-all')) {
      await gate.request({ args, label: job.label, kind: job.kind })
    } else {
      const j = await startJob(job.args, job.label, job.kind, job.vault)
      useApp.getState().navigate({ view: 'activity', job: j.id }, { replace: true })
    }
  }

  const copy = () => {
    void navigator.clipboard.writeText(job.log.map((l) => l.text).join('\n'))
    toast({ kind: 'success', title: 'Output copied' })
  }

  return (
    <div className="act-detail">
      <div className="act-detail-head">
        <div className="row" style={{ gap: 10 }}>
          <h2 className="act-detail-title grow truncate">{job.label}</h2>
          <StateBadge job={job} />
        </div>
        <div className="act-meta">
          <span>Started {fmtDateTime(job.started)}</span>
          <span>·</span>
          <span className="tnum">{running ? 'Running for' : 'Took'} {fmtDuration(jobDuration(job, now))}</span>
          {job.vault && (
            <>
              <span>·</span>
              <span className="truncate">{basename(job.vault)}</span>
            </>
          )}
          {!running && job.exit_code !== null && job.exit_code !== 0 && (
            <>
              <span>·</span>
              <span>exit code {job.exit_code}</span>
            </>
          )}
        </div>
        {running && (
          <div className="col" style={{ gap: 6, marginTop: 4 }}>
            <Progress value={prog.done} max={prog.total ?? 1} indeterminate={!prog.total} />
            <div className="faint" style={{ fontSize: 'var(--fs-sm)' }}>{prog.text}</div>
          </div>
        )}
        {job.state === 'failed' && job.exit_code === 2 && (
          <div className="act-note">This run stopped partway in a way that picks up on its own. Run it again to continue from where it left off.</div>
        )}
        <div className="row" style={{ gap: 8, marginTop: 6 }}>
          {running ? (
            <Button icon={stopCount ? OctagonX : Square} variant={stopCount ? 'danger' : 'default'} size="sm" onClick={() => void stop()}>
              {stopCount ? 'Stop immediately' : 'Stop'}
            </Button>
          ) : (
            <Button icon={RotateCw} size="sm" disabled={!sameVault} tip={sameVault ? undefined : 'Open that investigation to run this again'} loading={gate.busy} onClick={() => void rerun()}>
              Run again
            </Button>
          )}
          <Button icon={Clipboard} size="sm" variant="ghost" onClick={copy} disabled={!job.log.length}>
            Copy output
          </Button>
        </div>
        {running && stopCount > 0 && (
          <div className="act-note">
            Stop asks Watchdog to finish the document it is on, save its place and stop. Pressing Stop again ends it immediately; finished work is kept and the rest stays queued.
          </div>
        )}
      </div>
      <LogView job={job} />
      {gate.modal}
    </div>
  )
}

export default function JobsPanel({ selected, onSelect }: { selected?: string; onSelect: (id: string) => void }) {
  const jobsMap = useApp((s) => s.jobs)
  const project = useApp((s) => s.project)
  const [loaded, setLoaded] = useState(false)
  const now = useNow(Object.values(jobsMap).some((j) => j.state === 'running'))

  useEffect(() => {
    call('jobs.list', {})
      .then((js) => useApp.getState().setJobs(js))
      .catch(() => undefined)
      .finally(() => setLoaded(true))
  }, [])

  const jobs = useMemo(() => Object.values(jobsMap).sort((a, b) => Date.parse(b.started) - Date.parse(a.started)), [jobsMap])
  const current = jobs.find((j) => j.id === selected) ?? jobs.find((j) => j.state === 'running') ?? jobs[0]

  // A job known only from jobs.list has no output yet: fetch what the server kept.
  useEffect(() => {
    if (!current || current.log.length) return
    let live = true
    call('jobs.get', { id: current.id })
      .then((r) => {
        const j = useApp.getState().jobs[current.id]
        if (live && j && !j.log.length && r.log?.length) useApp.getState().appendLog(current.id, r.log)
      })
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [current?.id, current?.state]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!loaded && !jobs.length)
    return (
      <div className="row" style={{ padding: 40, justifyContent: 'center' }}>
        <Spinner size="lg" />
      </div>
    )
  if (!jobs.length)
    return (
      <Empty icon={Terminal} title="Nothing has run yet">
        Pre-processing, processing and the other long tasks appear here while they run, with their full output. Add documents to start one.
      </Empty>
    )
  return (
    <div className="act-split">
      <div className="act-list" role="listbox" aria-label="Jobs">
        {jobs.map((j) => {
          const p = progressText(j)
          return (
            <button key={j.id} role="option" aria-selected={j.id === current?.id} className="act-job" onClick={() => onSelect(j.id)}>
              <div className="row" style={{ gap: 8 }}>
                <span className="act-job-title grow truncate">{j.label}</span>
                <StateBadge job={j} />
              </div>
              <div className="act-job-sub">
                <span>{fmtRelative(j.started)}</span>
                <span>·</span>
                <span className="tnum">{fmtDuration(jobDuration(j, now))}</span>
                {j.vault && j.vault !== project?.path && (
                  <>
                    <span>·</span>
                    <span className="truncate">{basename(j.vault)}</span>
                  </>
                )}
              </div>
              {j.state === 'running' && (
                <>
                  <Progress value={p.done} max={p.total ?? 1} indeterminate={!p.total} style={{ marginTop: 6 }} />
                  <div className="act-job-sub truncate" style={{ marginTop: 4 }}>{p.text}</div>
                </>
              )}
            </button>
          )
        })}
      </div>
      {current && <JobDetail job={current} />}
    </div>
  )
}
