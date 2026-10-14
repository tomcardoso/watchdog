// The engine installer's progress view, shared by first-run setup and Settings → Setup. It shows
// the stages the main process reports ('engine.progress'), a log disclosure, retry and cancel.
// Setup waits only for phase 1; phase 2 (the document and search libraries and the local models)
// carries on in the background (D272), and first-run setup lists it as such.

import { AlertTriangle, CheckCircle2, ChevronRight, Circle, MinusCircle, XCircle } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { BackendStatus, EngineProgress, EngineStatus, EngineStep } from '@shared/api'
import { Button, Callout, Progress, Spinner, cx } from '@renderer/components/ui'
import { useEvent } from '@renderer/lib/rpc'

/** What the downloads cost, in words. Measured on a clean Linux install (see gui/README.md and
 * D272): phase 1 is Python and about 150 MB of libraries; phase 2 about 0.4 GB of libraries and
 * 4 GB of models. */
export const ENGINE_FIRST_TEXT = 'about 200 MB'
export const ENGINE_DOWNLOAD_TEXT = 'about 4.5 GB'
export const ENGINE_DISK_TEXT = 'about 7 GB'

// How much of the bar each stage is worth; the model downloads are the slow part.
const WEIGHT: Record<string, number> = { python: 1, packages: 2, libraries: 3, docling: 2, gliner: 6, embedding: 1, reranker: 3, ocr: 1 }

function StepIcon({ state }: { state: EngineStep['state'] }) {
  if (state === 'running') return <Spinner />
  if (state === 'done') return <CheckCircle2 className="onb-ic ok" />
  if (state === 'warning') return <AlertTriangle className="onb-ic warn" />
  if (state === 'failed') return <XCircle className="onb-ic bad" />
  if (state === 'skipped') return <MinusCircle className="onb-ic dim" />
  return <Circle className="onb-ic dim" />
}

function Row({ step, sub }: { step: EngineStep; sub?: boolean }) {
  return (
    <div className={cx('onb-step', sub && 'sub', step.state)}>
      <StepIcon state={step.state} />
      <div className="grow">
        <div className="onb-step-label">{step.label}</div>
        {step.detail && step.state !== 'pending' && <div className="onb-step-detail truncate">{step.detail}</div>}
      </div>
    </div>
  )
}

export function fraction(all: EngineStep[], phase?: 1 | 2): number {
  const steps = phase ? all.filter((s) => s.phase === phase) : all
  const total = steps.reduce((n, s) => n + (WEIGHT[s.id] ?? 1), 0)
  const done = steps.reduce((n, s) => n + (['done', 'warning', 'skipped', 'failed'].includes(s.state) ? WEIGHT[s.id] ?? 1 : 0), 0)
  return total ? done / total : 0
}

export interface EngineRun {
  status: EngineStatus | null
  run: EngineProgress | null
  log: string[]
  start: (fresh?: boolean) => Promise<void>
}

/** Subscribes to the installer and returns its current state. */
export function useEngine(): EngineRun {
  const [status, setStatus] = useState<EngineStatus | null>(null)
  const [run, setRun] = useState<EngineProgress | null>(null)
  const [log, setLog] = useState<string[]>([])
  useEffect(() => {
    void window.watchdog.engine.status().then((s) => {
      setStatus(s)
      setRun({ ...s.run, log: [] })
      setLog(s.run.log)
    })
  }, [])
  useEvent('engine.progress', (p) => {
    setRun((prev) => {
      if (prev && prev.id !== p.id) setLog(p.log)
      else setLog((l) => [...l, ...p.log].slice(-500))
      return p
    })
    if (p.state !== 'running') void window.watchdog.engine.status().then(setStatus)
  })
  const start = async (fresh = false) => {
    setLog([])
    setRun(null)
    const s = await (fresh ? window.watchdog.engine.reinstall() : window.watchdog.engine.install())
    setStatus(s)
  }
  return { status, run, log, start }
}

/**
 * `focus` 1 (first-run setup): the bar and the steps are phase 1's; phase 2's are listed below as
 * continuing in the background. Without it (Settings → Setup), every step counts.
 */
export function EngineProgressView({ eng, backend, focus }: { eng: EngineRun; backend?: BackendStatus | null; focus?: 1 }) {
  const { run, log } = eng
  const logRef = useRef<HTMLPreElement>(null)
  const [open, setOpen] = useState(false)
  useEffect(() => {
    if (open && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [log, open])
  if (!run || run.state === 'idle') return null
  const first = run.steps.filter((s) => s.phase === 1)
  const libraries = run.steps.find((s) => s.id === 'libraries')
  const models = run.steps.filter((s) => s.phase === 2 && s.id !== 'libraries')
  const warned = run.state === 'done' && run.steps.some((s) => s.state === 'warning')
  const coreDone = first.every((s) => ['done', 'skipped'].includes(s.state))
  const value = run.state === 'done' || (focus === 1 && coreDone) ? 1 : fraction(run.steps, focus)
  return (
    <div className="col gap-16">
      <Progress value={value} indeterminate={run.state === 'running' && value === 0} />
      <div className="onb-steps" aria-live="polite">
        {first.map((s) => (
          <Row key={s.id} step={s} />
        ))}
        <div className="onb-group">{focus === 1 ? 'Continues in the background' : 'Document and search tools'}</div>
        {libraries && <Row step={libraries} sub />}
        {models.map((s) => (
          <Row key={s.id} step={s} sub />
        ))}
      </div>
      {focus === 1 && (
        <p className="onb-note">
          You can carry on as soon as the first steps are done. The rest downloads while you finish setting up and while you work; adding documents waits for it.
        </p>
      )}
      {run.error && (
        <Callout tone={run.state === 'cancelled' ? 'info' : 'danger'} title={run.state === 'cancelled' ? (run.phase === 2 ? 'Setup paused' : 'Installation stopped') : 'The installation did not finish'}>
          <span className="selectable" style={{ whiteSpace: 'pre-wrap' }}>{run.error}</span>
        </Callout>
      )}
      {warned && (
        <Callout tone="warning" title="Some optional pieces could not be downloaded">
          Watchdog works without them and fetches each one the first time it is needed. To try again now, use Repair in Settings under Setup.
        </Callout>
      )}
      {backend?.state === 'error' && backend.message && run.state === 'done' && <Callout tone="danger">{backend.message}</Callout>}
      <div>
        <button className="onb-disclose" aria-expanded={open} onClick={() => setOpen(!open)}>
          <ChevronRight /> {open ? 'Hide details' : 'Show details'}
        </button>
        {open && (
          <pre ref={logRef} className="log onb-log" aria-label="Installer log">
            {log.join('\n') || 'Nothing yet.'}
          </pre>
        )}
      </div>
    </div>
  )
}

export function EngineActions({ eng, primary, onFresh }: { eng: EngineRun; primary: string; onFresh?: () => void }) {
  const running = eng.run?.state === 'running'
  const failed = eng.run?.state === 'failed' || eng.run?.state === 'cancelled'
  if (running) return <Button onClick={() => void window.watchdog.engine.cancel()}>Cancel</Button>
  if (failed) {
    return (
      <>
        <Button variant="primary" onClick={() => void eng.start(false)}>Try again</Button>
        {onFresh && <Button variant="ghost" onClick={onFresh}>Start over</Button>}
      </>
    )
  }
  if (eng.run?.state === 'done') return null
  return <Button variant="primary" disabled={!eng.status} onClick={() => void eng.start(false)}>{primary}</Button>
}
