// Add documents: `watchdog add`, the core workflow, as four steps. Choose → Read (local, no model)
// → Public-records gate → Run. Nothing is sent to a model before the gate has been acknowledged
// (or auto-approve, which the CLI itself honours, applies).

import {
  ArrowRight, Check, ChevronDown, ChevronRight, Circle, FilePlus2, FolderInput, RefreshCw, ShieldAlert, X, CircleCheck, CircleX, Calculator
} from 'lucide-react'
import { ReactNode, useEffect, useRef, useState } from 'react'
import { Badge, Button, Callout, Empty, Field, Modal, Progress, Skeleton, Spinner, Switch } from '@renderer/components/ui'
import { basename, fmtCost, plural } from '@renderer/lib/format'
import { flagsFor, startJob, stopJob, waitForJob } from '@renderer/lib/jobs'
import { useEngineGate } from '@renderer/lib/engine'
import { EngineWait } from '@renderer/components/EngineWait'
import { call, errorMessage, useRpc } from '@renderer/lib/rpc'
import { navigate, useApp, useVault } from '@renderer/lib/store'
import { progressText, STAGE_LABELS } from '@renderer/shell/JobDock'
import type { Effort, Estimate, Job, Preflight, RunOptions } from '@shared/api'

type Phase = 'choose' | 'reading' | 'gate' | 'running' | 'done'
const STEPS = ['Choose', 'Read', 'Confirm', 'Add']
const STEP_OF: Record<Phase, number> = { choose: 0, reading: 1, gate: 2, running: 3, done: 3 }

// Pipeline stage → step of the run stepper: Read, Extract, Write, Brief.
const STAGE_STEP: Record<string, number> = {
  model: 0, chew: 0, classify: 1, extract: 1, dig: 1, fold: 1, reconcile: 2, commit: 2, contradictions: 2, synthesis: 2, timeline: 2, briefing: 3, leads: 3, requests: 3, done: 3
}
const RUN_STEPS = ['Read', 'Extract', 'Write', 'Brief']

const NEW_PATHS_EVENT = 'wd:add'

export function AddDialog() {
  const vault = useVault()
  const addOpen = useApp((s) => s.addOpen)
  const jobs = useApp((s) => s.jobs)
  const hasProject = useApp((s) => s.project !== null)
  const visible = addOpen !== null && hasProject
  const engine = useEngineGate()

  const [phase, setPhase] = useState<Phase>('choose')
  const [paths, setPaths] = useState<string[]>([])
  const [retry, setRetry] = useState(false)
  const [options, setOptions] = useState<RunOptions>({})
  const [readState, setReadState] = useState<{ i: number; n: number; job: string | null; label: string } | null>(null)
  const [readIssues, setReadIssues] = useState<string[]>([])
  const [folders, setFolders] = useState<string[]>([])
  const [gate, setGate] = useState<{ pf: Preflight; blocked: { name: string; reason: string | null }[] } | null>(null)
  const [addJob, setAddJob] = useState<string | null>(null)
  const [autoNote, setAutoNote] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const phaseRef = useRef(phase)
  phaseRef.current = phase
  // The investigation this flow belongs to: every step runs there, even if the reporter switches
  // investigation while files are read or the gate waits.
  const flowVault = useRef(vault)

  // Opening: start fresh unless a run is under way (the dialog can be closed and reopened).
  useEffect(() => {
    if (!addOpen) return
    if (phaseRef.current === 'choose' || phaseRef.current === 'done') {
      setPhase('choose')
      setPaths((prev) => (phaseRef.current === 'choose' ? [...new Set([...prev, ...addOpen.paths])] : addOpen.paths))
      if (phaseRef.current === 'done') {
        setAddJob(null)
        setRetry(false)
        setError('')
        setGate(null)
      }
    }
  }, [addOpen])

  // `wd:add` from the Overview: open with failed documents to retry.
  useEffect(() => {
    const on = (e: Event) => {
      const d = (e as CustomEvent<{ retry?: boolean; paths?: string[] }>).detail ?? {}
      if (phaseRef.current === 'choose' || phaseRef.current === 'done') {
        setPhase('choose')
        setRetry(!!d.retry)
        setPaths(d.paths ?? [])
        setAddJob(null)
        setGate(null)
        setError('')
      }
      useApp.getState().openAdd(d.paths ?? [])
    }
    window.addEventListener(NEW_PATHS_EVENT, on)
    return () => window.removeEventListener(NEW_PATHS_EVENT, on)
  }, [])

  const close = () => useApp.getState().closeAdd()
  const job: Job | undefined = addJob ? jobs[addJob] : undefined
  const effective: Phase = phase === 'running' && job && job.state !== 'running' ? 'done' : phase
  // A finished run is 'done', so reopening the dialog (or dropping files) starts afresh.
  useEffect(() => {
    if (effective === 'done' && phase === 'running') setPhase('done')
  }, [effective, phase])

  // Another investigation opened: anything not under way belonged to the previous one.
  useEffect(() => {
    if (flowVault.current === vault) return
    if (phaseRef.current === 'reading' || phaseRef.current === 'running') return
    flowVault.current = vault
    setPhase('choose')
    setPaths(useApp.getState().addOpen?.paths ?? [])
    setRetry(false)
    setGate(null)
    setAddJob(null)
    setError('')
  }, [vault])

  // Whatever is already waiting in the investigation.
  const { data: pf0, isLoading: pfLoading, error: pfError } = useRpc('ingest.preflight', visible && phase === 'choose' ? { vault, options: cleanOptions(options) } : null, { staleTime: 0 })

  const gatherFlags = async (cmd: 'add' | 'chew', o: RunOptions) => flagsFor(cmd, cleanOptions(o))

  // ── Step 2 → 3: read locally, then gate ────────────────────────────────────
  const begin = async () => {
    setError('')
    setBusy(true)
    flowVault.current = vault
    const needIncoming = (pf0?.incoming ?? 0) > 0
    try {
      let chewFlags: string[] = []
      try {
        chewFlags = await gatherFlags('chew', { chew_workers: options.chew_workers, chunk_workers: options.chunk_workers })
      } catch {
        /* defaults */
      }
      const issues: string[] = []
      const dirs: string[] = []
      setPhase('reading')
      const work: (string | null)[] = [...paths, ...(needIncoming ? [null] : [])]
      for (let i = 0; i < work.length; i++) {
        const p = work[i]
        const label = p ? `Reading ${basename(p)}` : 'Reading documents'
        setReadState({ i, n: work.length, job: null, label })
        const j = await startJob(p ? ['chew', p, ...chewFlags] : ['chew', ...chewFlags], label, 'chew', vault)
        setReadState({ i, n: work.length, job: j.id, label })
        const fin = await waitForJob(j.id)
        if (fin.state === 'cancelled') {
          setPhase('choose')
          setBusy(false)
          return
        }
        if (fin.state !== 'done') {
          const log = useApp.getState().jobs[j.id]?.log ?? []
          const text = log.map((l) => l.text).join('\n')
          if (p && /is a folder/i.test(text)) dirs.push(p)
          else issues.push(`${p ? basename(p) : 'Documents in incoming'}: ${lastError(log) || 'could not be read'}`)
        }
      }
      setFolders(dirs)
      setReadIssues(issues)
      const [pf, pipe] = await Promise.all([
        call('ingest.preflight', { vault, options: cleanOptions(options) }),
        call('vault.pipeline', { vault })
      ])
      const blocked = [...pipe.chew_failed.map((f) => ({ name: f.name, reason: null as string | null })), ...pipe.skipped.map((f) => ({ name: f.name, reason: f.reason }))]
      setGate({ pf, blocked })
      setBusy(false)
      const auto = pf.auto_approve.enabled && pf.auto_approve.approve && pf.auth.ok
      const somethingToRun = countFor(pf, retry, dirs.length) > 0 || !!pf.pending_finalization || pf.staged > 0
      if (auto && somethingToRun) {
        setAutoNote(true)
        await run(pf, dirs)
      } else {
        setAutoNote(false)
        setPhase('gate')
        if (!useApp.getState().addOpen) useApp.getState().openAdd(paths) // the gate always needs a person
      }
    } catch (e) {
      setError(errorMessage(e))
      setPhase('choose')
      setBusy(false)
    }
  }

  // ── Step 3 → 4 ──────────────────────────────────────────────────────────────
  const run = async (pf: Preflight, dirs: string[]) => {
    setBusy(true)
    setError('')
    try {
      const { chew_workers: _a, chunk_workers: _b, ...addOpts } = options
      void _a
      void _b
      const flags = await gatherFlags('add', addOpts)
      const n = countFor(pf, retry, dirs.length)
      const label = n > 0 ? `Adding ${plural(n, 'document')}` : 'Finishing the batch'
      const j = await startJob(['add', '--skip-warning', ...flags, ...(retry ? ['--retry'] : []), ...dirs], label, 'add', flowVault.current)
      setAddJob(j.id)
      setPhase('running')
    } catch (e) {
      setError(errorMessage(e))
      setPhase('gate')
    } finally {
      setBusy(false)
    }
  }

  const nothingToDo = !pfLoading && pf0 && paths.length === 0 && pf0.incoming + pf0.queued + pf0.staged + (retry ? pf0.failed : 0) === 0 && !pf0.pending_finalization

  // ── render ──────────────────────────────────────────────────────────────────
  const step = STEP_OF[effective]
  let body: ReactNode
  let footer: ReactNode
  let title = 'Add documents'
  let sub: ReactNode = 'Copies files into this investigation, reads them on this computer, then extracts and writes up what they contain.'

  if (effective === 'choose') {
    body = (
      <>
      <EngineWait style={{ marginBottom: 16 }} />
      <ChooseStep
        paths={paths}
        setPaths={setPaths}
        pf={pf0 ?? null}
        loading={pfLoading}
        retry={retry}
        setRetry={setRetry}
        options={options}
        setOptions={setOptions}
        error={error || (pfError ? errorMessage(pfError) : '')}
      />
      </>
    )
    footer = (
      <>
        <span className="faint grow" style={{ fontSize: 'var(--fs-sm)' }}>
          {engine.ready ? 'Reading files stays on this computer. You confirm before anything goes to a model.' : 'Files you choose stay listed here, ready to read when setup finishes.'}
        </span>
        <Button variant="ghost" onClick={close}>Cancel</Button>
        <Button variant="primary" iconRight={ArrowRight} disabled={!!nothingToDo || pfLoading || !!pfError || !engine.ready} loading={busy} onClick={() => void begin()}>
          Read documents
        </Button>
      </>
    )
  } else if (effective === 'reading') {
    title = 'Reading documents'
    sub = 'Extracting text and pages on this computer. Nothing is sent to a model in this step.'
    const rj = readState?.job ? jobs[readState.job] : undefined
    body = (
      <>
        {readState && readState.n > 1 && (
          <div className="add-readcount">
            File {Math.min(readState.i + 1, readState.n)} of {readState.n}
          </div>
        )}
        <JobPanel job={rj} idle={readState?.label ?? 'Starting'} />
      </>
    )
    footer = (
      <>
        <span className="faint grow" style={{ fontSize: 'var(--fs-sm)' }}>You can close this window. Reading continues, and the window reopens when it needs you.</span>
        <Button variant="ghost" onClick={close}>Hide</Button>
        {rj && <Button onClick={() => stopJob(rj.id)}>Stop</Button>}
      </>
    )
  } else if (effective === 'gate' && gate) {
    title = 'Before anything is sent'
    sub = undefined
    body = <GateStep gate={gate} retry={retry} folders={folders} issues={readIssues} error={error} />
    const n = countFor(gate.pf, retry, folders.length)
    const finishing = n === 0 && !!(gate.pf.pending_finalization || gate.pf.staged > 0)
    const nothing = n === 0 && !finishing
    footer = (
      <>
        <Button variant="default" onClick={close}>Cancel</Button>
        {nothing ? (
          <Button variant="primary" onClick={close}>Close</Button>
        ) : (
          <Button variant="primary" autoFocus icon={Check} loading={busy} disabled={!gate.pf.auth.ok || !engine.ready} onClick={() => void run(gate.pf, folders)}>
            Acknowledge and add
          </Button>
        )}
      </>
    )
  } else if (effective === 'running' || effective === 'done') {
    title = effective !== 'done' ? job?.label ?? 'Adding documents' : job?.state === 'done' ? 'Finished' : job?.state === 'cancelled' ? 'Stopped' : job?.exit_code === 2 ? 'Paused' : 'Did not finish'
    sub = undefined
    body = <RunStep job={job} done={effective === 'done'} autoNote={autoNote} startAtWrite={!!gate && countFor(gate.pf, retry, folders.length) === 0} />
    footer =
      effective === 'running' ? (
        <>
          <span className="faint grow" style={{ fontSize: 'var(--fs-sm)' }}>Closing this window does not stop the run. Progress stays in the corner.</span>
          <Button variant="ghost" onClick={close}>Hide</Button>
          {job && <Button onClick={() => stopJob(job.id)}>Stop</Button>}
        </>
      ) : (
        <DoneFooter job={job} onAgain={() => { setPhase('choose'); setAddJob(null); setGate(null); setPaths([]); setRetry(false) }} />
      )
  } else {
    body = <Skeleton h={120} />
    footer = <Button onClick={close}>Close</Button>
  }

  return (
    <Modal open={visible} onClose={close} title={title} sub={sub} width="wide" footer={footer} dismissable={effective !== 'gate'}>
      <Stepper current={step} />
      {body}
    </Modal>
  )
}

// ── helpers ──────────────────────────────────────────────────────────────────
function cleanOptions(o: RunOptions): RunOptions {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(o)) {
    if (v === undefined || v === null || v === '' || v === false || (typeof v === 'number' && Number.isNaN(v))) continue
    out[k] = v
  }
  return out as RunOptions
}

function countFor(pf: Preflight, retry: boolean, folderCount: number): number {
  return pf.documents_to_send + (retry ? pf.failed : 0) + folderCount * 0
}

function lastError(log: { stream: string; text: string }[]): string {
  const lines = log.filter((l) => l.text.trim())
  const err = [...lines].reverse().find((l) => /^\s*Error/i.test(l.text)) ?? [...lines].reverse().find((l) => l.stream === 'err')
  return (err?.text ?? '').replace(/^\s*Error:\s*/i, '').trim()
}

function Stepper({ current }: { current: number }) {
  return (
    <ol className="add-steps">
      {STEPS.map((s, i) => (
        <li key={s} className={i < current ? 'done' : i === current ? 'now' : ''}>
          <span className="dot">{i < current ? <Check /> : i + 1}</span>
          {s}
        </li>
      ))}
    </ol>
  )
}

// ── Step 1 ───────────────────────────────────────────────────────────────────
function ChooseStep({ paths, setPaths, pf, loading, retry, setRetry, options, setOptions, error }: {
  paths: string[]; setPaths: (p: string[]) => void; pf: Preflight | null; loading: boolean; retry: boolean; setRetry: (b: boolean) => void
  options: RunOptions; setOptions: (o: RunOptions) => void; error: string
}) {
  const vault = useVault()
  const [showOpts, setShowOpts] = useState(false)
  const [est, setEst] = useState<{ data?: Estimate; error?: string; loading?: boolean; all?: boolean } | null>(null)
  const pick = async (folders: boolean) => {
    const got = await window.watchdog.dialog.openFiles({ title: folders ? 'Choose a folder of documents' : 'Choose documents', folders, multi: true })
    if (got.length) setPaths([...new Set([...paths, ...got])])
  }
  const estimate = async (all: boolean) => {
    setEst({ loading: true, all })
    try {
      const data = await call('ingest.estimate', { vault, stage: 'dig', all_models: all, options: cleanOptions(options) })
      setEst({ data, all })
    } catch (e) {
      setEst({ error: errorMessage(e), all })
    }
  }
  const waiting = pf ? [
    pf.incoming > 0 && `${plural(pf.incoming, 'file')} in incoming`,
    pf.queued - pf.staged > 0 && `${plural(pf.queued - pf.staged, 'document')} read, waiting for extraction`,
    pf.staged > 0 && `${plural(pf.staged, 'document')} extracted, waiting to be finished`,
    pf.pending_finalization && 'a batch waiting to be finished'
  ].filter(Boolean) as string[] : []

  return (
    <div className="col gap-16">
      <section>
        <div className="add-label">Files to add</div>
        {paths.length > 0 ? (
          <ul className="add-files">
            {paths.map((p) => (
              <li key={p}>
                <span className="n truncate">{basename(p)}</span>
                <span className="d truncate" title={p}>{p.slice(0, p.length - basename(p).length)}</span>
                <Button variant="ghost" size="sm" icon={X} tip="Remove" onClick={() => setPaths(paths.filter((x) => x !== p))} />
              </li>
            ))}
          </ul>
        ) : (
          <div className="add-hint">Drop files anywhere on the window, or choose them here. The originals stay where they are.</div>
        )}
        <div className="row" style={{ marginTop: 10 }}>
          <Button icon={FilePlus2} onClick={() => void pick(false)}>Choose files…</Button>
          <Button icon={FolderInput} onClick={() => void pick(true)}>Choose folder…</Button>
        </div>
      </section>

      <section>
        <div className="add-label">Already waiting</div>
        {loading ? (
          <Skeleton h={18} w="60%" />
        ) : !pf ? (
          <div className="faint">Watchdog could not check this investigation. The reason is below.</div>
        ) : waiting.length ? (
          <ul className="add-waiting">{waiting.map((w) => <li key={w}>{w}</li>)}</ul>
        ) : (
          <div className="faint">Nothing else is waiting.</div>
        )}
        {pf && pf.failed > 0 && (
          <label className="checkbox" style={{ marginTop: 10 }}>
            <input type="checkbox" checked={retry} onChange={(e) => setRetry(e.target.checked)} />
            <span>Retry {plural(pf.failed, 'document')} that failed before</span>
          </label>
        )}
      </section>

      {pf && !pf.auth.ok && (
        <Callout tone="warning" title="No model sign-in is set up" action={<Button size="sm" onClick={() => { useApp.getState().closeAdd(); navigate({ view: 'settings', tab: 'auth' }) }}>Open Settings</Button>}>
          {pf.auth.reason ?? 'Reading works without it, but extraction needs a sign-in or API key.'}
        </Callout>
      )}

      <section>
        <button className="add-disclose" onClick={() => setShowOpts(!showOpts)} aria-expanded={showOpts}>
          {showOpts ? <ChevronDown /> : <ChevronRight />}
          Options
          <span className="faint">models, effort, checks, speed</span>
        </button>
        {showOpts && <OptionsForm options={options} setOptions={setOptions} />}
      </section>

      <section>
        <div className="row">
          <Button icon={Calculator} loading={est?.loading && !est.all} onClick={() => void estimate(false)}>Estimate cost</Button>
          <Button variant="ghost" loading={est?.loading && !!est.all} onClick={() => void estimate(true)}>Compare all models</Button>
        </div>
        {est?.error && <Callout tone="danger" style={{ marginTop: 10 }}>{est.error}</Callout>}
        {est?.data && (
          <div style={{ marginTop: 10 }}>
            <pre className="log add-estimate">{est.data.text.trim()}</pre>
            {est.data.all_models && est.data.all_models.length > 0 && (
              <table className="table add-compare">
                <thead><tr><th>Model</th><th>Provider</th><th style={{ textAlign: 'right' }}>Estimated cost</th></tr></thead>
                <tbody>
                  {est.data.all_models.map((m) => (
                    <tr key={m.label + m.provider}>
                      <td>{m.label}{m.note && <span className="faint"> · {m.note}</span>}</td>
                      <td>{m.provider}</td>
                      <td className="tnum" style={{ textAlign: 'right' }}>{fmtCost(m.cost_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {(paths.length > 0 || (pf?.incoming ?? 0) > 0) && <div className="faint" style={{ fontSize: 'var(--fs-sm)', marginTop: 6 }}>The estimate covers documents already read. New files are counted after the read step.</div>}
          </div>
        )}
      </section>

      {error && <Callout tone="danger" title="That did not work">{error}</Callout>}
    </div>
  )
}

const EFFORT_LABEL: Record<string, string> = { low: 'Low', medium: 'Medium', high: 'High', xhigh: 'Extra high', max: 'Maximum' }

function OptionsForm({ options, setOptions }: { options: RunOptions; setOptions: (o: RunOptions) => void }) {
  const { data: models } = useRpc('settings.models', {})
  const { data: skills } = useRpc('skills.list', {})
  const set = <K extends keyof RunOptions>(k: K, v: RunOptions[K] | undefined) => setOptions({ ...options, [k]: v })
  const groups = new Map<string, NonNullable<typeof models>['models']>()
  for (const m of models?.models ?? []) groups.set(m.provider, [...(groups.get(m.provider) ?? []), m])
  const efforts = models?.efforts ?? ['low', 'medium', 'high', 'xhigh', 'max']

  const modelSelect = (k: keyof RunOptions, label: string, hint?: string) => (
    <Field label={label} hint={hint}>
      <select className="select" value={(options[k] as string) ?? ''} onChange={(e) => set(k, (e.target.value || undefined) as never)}>
        <option value="">Use the configured model</option>
        {[...groups.entries()].map(([prov, ms]) => (
          <optgroup key={prov} label={prov}>
            {ms.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
          </optgroup>
        ))}
      </select>
    </Field>
  )
  const effortSelect = (k: 'extractor_effort' | 'classifier_effort' | 'finalizer_effort', label: string) => (
    <Field label={label}>
      <select className="select" value={options[k] ?? ''} onChange={(e) => set(k, (e.target.value || undefined) as Effort | undefined)}>
        <option value="">Use the configured effort</option>
        {efforts.map((e) => <option key={e} value={e}>{EFFORT_LABEL[e] ?? e}</option>)}
      </select>
    </Field>
  )
  const num = (k: 'concurrency' | 'classify_pages' | 'limit' | 'chew_workers' | 'chunk_workers', label: string, hint: string) => (
    <Field label={label} hint={hint}>
      <input className="input" type="number" min={1} placeholder="Default" value={options[k] ?? ''} onChange={(e) => set(k, e.target.value ? Math.max(1, Math.floor(Number(e.target.value))) : undefined)} />
    </Field>
  )

  return (
    <div className="add-opts">
      <div className="add-opt-group">
        <h4>Models</h4>
        <div className="add-grid">
          {modelSelect('extractor_model', 'Extraction', 'Reads each document and lists the facts.')}
          {modelSelect('classifier_model', 'Classification', 'Decides what kind of record each document is.')}
          {modelSelect('finalizer_model', 'Finishing step', 'Entity matching, summaries, timeline and briefing.')}
        </div>
        <details className="add-more">
          <summary>Choose a model for each finishing stage</summary>
          <div className="add-grid" style={{ marginTop: 10 }}>
            {modelSelect('finalizer_reconciliation_model', 'Entity matching')}
            {modelSelect('finalizer_synthesis_model', 'Entity summaries')}
            {modelSelect('finalizer_timeline_model', 'Timeline')}
            {modelSelect('finalizer_briefing_model', 'Briefing')}
          </div>
        </details>
      </div>
      <div className="add-opt-group">
        <h4>Effort</h4>
        <div className="add-grid">
          {effortSelect('extractor_effort', 'Extraction')}
          {effortSelect('classifier_effort', 'Classification')}
          {effortSelect('finalizer_effort', 'Finishing step')}
        </div>
      </div>
      <div className="add-opt-group">
        <h4>Checks and scope</h4>
        <div className="add-grid">
          <Field label="Second-read check" hint="A cheap second pass that adds facts the first one missed. Costs roughly 15% more.">
            <select className="select" value={options.verify === true ? 'on' : options.verify === false ? 'off' : ''} onChange={(e) => set('verify', e.target.value === 'on' ? true : e.target.value === 'off' ? false : undefined)}>
              <option value="">Use the configured setting</option>
              <option value="on">On for this run</option>
              <option value="off">Off for this run</option>
            </select>
          </Field>
          <Field label="Record skill" hint="Pin one skill for every document instead of classifying each.">
            <select className="select" value={options.skill ?? ''} onChange={(e) => set('skill', e.target.value || undefined)}>
              <option value="">Detect for each document</option>
              {(skills?.skills ?? []).map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
            </select>
          </Field>
          {num('limit', 'Limit', 'Extract at most this many documents now.')}
          {num('classify_pages', 'Pages to classify from', 'Pages shown to the classifier.')}
        </div>
      </div>
      <div className="add-opt-group">
        <h4>Speed and behaviour</h4>
        <div className="add-grid">
          {num('concurrency', 'Documents at once', 'Extracted in parallel.')}
          {num('chew_workers', 'Pre-processing: files at once', 'Parallel file workers while pre-processing.')}
          {num('chunk_workers', 'Pre-processing: parts at once', 'Parallel workers within one file.')}
        </div>
        <div className="col" style={{ marginTop: 10 }}>
          <label className="row add-switch">
            <Switch checked={!!options.wait} onChange={(v) => set('wait', v || undefined)} label="Wait out rate limits" />
            <span><b>Wait out rate limits</b><span className="faint"> Sleep until the limit resets and carry on, instead of stopping.</span></span>
          </label>
          <label className="row add-switch">
            <Switch checked={!!options.skip_briefing} onChange={(v) => set('skip_briefing', v || undefined)} label="Skip the briefing" />
            <span><b>Skip the briefing</b><span className="faint"> Still writes summaries and the timeline, without the final model call.</span></span>
          </label>
        </div>
      </div>
      <Button variant="ghost" size="sm" onClick={() => setOptions({})} disabled={!Object.keys(cleanOptions(options)).length}>Reset options</Button>
    </div>
  )
}

// ── Step 3 ───────────────────────────────────────────────────────────────────
function GateStep({ gate, retry, folders, issues, error }: { gate: { pf: Preflight; blocked: { name: string; reason: string | null }[] }; retry: boolean; folders: string[]; issues: string[]; error: string }) {
  const { pf, blocked } = gate
  const n = countFor(pf, retry, folders.length)
  const finishing = n === 0 && !!(pf.pending_finalization || pf.staged > 0)
  const auto = pf.auto_approve
  return (
    <div className="col gap-16">
      {(issues.length > 0 || blocked.length > 0) && (
        <Callout tone="warning" title="Some files could not be read">
          <ul className="add-issues">
            {issues.map((i) => <li key={i}>{i}</li>)}
            {blocked.map((b) => <li key={b.name}>{b.name}{b.reason ? `: ${b.reason}` : ' was set aside in incoming/failed'}</li>)}
          </ul>
        </Callout>
      )}
      {n === 0 && !finishing ? (
        <Empty icon={CircleCheck} title="Nothing to send">No new documents need a model. If you expected some, they may already be in this investigation, or could not be read.</Empty>
      ) : (
        <>
          <div className="add-count">
            <div className="big tnum">{n > 0 ? n : '—'}</div>
            <div>
              {n > 0 ? (
                <>
                  <b>{n === 1 ? 'document' : 'documents'}</b> will be sent to the model
                  {folders.length > 0 && <span className="muted">, plus the documents inside {plural(folders.length, 'folder')}, which are counted as they are read</span>}.
                </>
              ) : (
                <><b>No documents</b> need extraction. Only the finishing step runs: it sends what was already extracted to the model to write summaries, the timeline and the briefing.</>
              )}
              {retry && pf.failed > 0 && n > 0 && <div className="faint">Includes {plural(pf.failed, 'document')} being retried.</div>}
            </div>
          </div>

          {auto.enabled && !auto.approve && auto.blocker && <Callout tone="info" title="Auto-approve is on, but not for this run">{auto.blocker}</Callout>}

          <div className="add-warning" role="alert">
            <ShieldAlert />
            <pre>{pf.warning_text.trim()}</pre>
          </div>

          <div>
            <div className="add-label">Where the text goes</div>
            <ul className="add-models">
              {pf.models.map((m) => (
                <li key={m.stage + m.model}>
                  <span className="s">{stageName(m.stage)}</span>
                  <span className="grow truncate">{m.label}</span>
                  {m.backend && <Badge>{m.backend}</Badge>}
                  {m.effort && <span className="faint">{m.effort}</span>}
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
      {!pf.auth.ok && (
        <Callout tone="danger" title="Sign-in needed before this can run" action={<Button size="sm" onClick={() => { useApp.getState().closeAdd(); navigate({ view: 'settings', tab: 'auth' }) }}>Open Settings</Button>}>
          {pf.auth.reason ?? 'No usable sign-in or API key was found for the models above.'}
        </Callout>
      )}
      {error && <Callout tone="danger">{error}</Callout>}
    </div>
  )
}

const stageName = (s: string) => ({ classifier: 'Classify', extractor: 'Extract', finalizer: 'Finish' } as Record<string, string>)[s] ?? s.replace(/^finalizer:/, 'Finish: ')

// ── Step 4 ───────────────────────────────────────────────────────────────────
function JobPanel({ job, idle }: { job?: Job; idle: string }) {
  const p = job ? progressText(job) : { text: idle, done: null, total: null }
  const docs = job ? Object.entries(job.progress.docs ?? {}) : []
  const log = useApp((s) => (job ? s.jobs[job.id]?.log : undefined)) ?? []
  const logRef = useRef<HTMLDivElement>(null)
  const [showLog, setShowLog] = useState(false)
  useEffect(() => {
    if (showLog) logRef.current?.scrollTo({ top: logRef.current.scrollHeight })
  }, [log.length, showLog])
  return (
    <div className="col gap-12">
      <div>
        <div className="row" style={{ marginBottom: 8 }}>
          {(!job || job.state === 'running') && <Spinner />}
          <span className="grow">{p.text}</span>
        </div>
        <Progress value={p.done} max={p.total ?? 1} indeterminate={!p.total} />
      </div>
      {docs.length > 0 && (
        <ul className="add-docs">
          {docs.map(([sha, d]) => (
            <li key={sha}>
              <DocState state={d.state} />
              <span className="grow truncate">{d.filename}</span>
              <span className="faint truncate" style={{ maxWidth: 220 }}>{d.detail ?? stateLabel(d.state)}</span>
            </li>
          ))}
        </ul>
      )}
      <div>
        <button className="add-disclose" onClick={() => setShowLog(!showLog)}>
          {showLog ? <ChevronDown /> : <ChevronRight />}Live output
        </button>
        {showLog && (
          <div className="log add-log" ref={logRef}>
            {log.length === 0 ? <span className="faint">No output yet.</span> : log.slice(-400).map((l, i) => <div key={i} className={l.stream === 'err' ? 'err' : undefined}>{l.text}</div>)}
          </div>
        )}
      </div>
    </div>
  )
}

const stateLabel = (s: string) => ({ queued: 'Waiting', done: 'Done', failed: 'Failed' } as Record<string, string>)[s] ?? s

function DocState({ state }: { state: string }) {
  if (state === 'done') return <CircleCheck className="ds ok" />
  if (state === 'failed') return <CircleX className="ds bad" />
  if (state === 'queued' || state === 'waiting') return <Circle className="ds idle" />
  return <span className="spinner ds" style={{ width: 14, height: 14 }} />
}

function RunStep({ job, done, autoNote, startAtWrite }: { job?: Job; done: boolean; autoNote: boolean; startAtWrite: boolean }) {
  const vault = useVault()
  const stage = job?.progress.stage
  const ok = job?.state === 'done'
  // A run that stopped short keeps its steps where it stopped: only a finished run is all done.
  // Extraction also reports 'done' when it ends, so a failed run there stops at Extract.
  const at = done && !ok && stage === 'done' ? 'dig' : stage
  const active = done && ok ? 4 : at && at in STAGE_STEP ? Math.max(1, STAGE_STEP[at]) : startAtWrite ? 2 : 1
  const { data: summary } = useRpc('vault.summary', done && ok ? { vault } : null, { staleTime: 0 })
  const tail = (useApp((s) => (job ? s.jobs[job.id]?.log : undefined)) ?? []).slice(-40)
  return (
    <div className="col gap-16">
      {autoNote && <Callout tone="info">Auto-approve is on for this investigation, so the public-records notice was not repeated. Change it in Settings.</Callout>}
      <ol className="add-run">
        {RUN_STEPS.map((s, i) => (
          <li key={s} className={i < active ? 'done' : i === active ? 'now' : ''}>
            <span className="bar" />
            <span className="t">{s}</span>
            <span className="d">{i === active && at ? (done && !ok ? 'Stopped here' : STAGE_LABELS[at] ?? at) : i < active ? 'Done' : ''}</span>
          </li>
        ))}
      </ol>
      {!done && <JobPanel job={job} idle="Starting" />}
      {done && job && (
        <>
          {ok && (
            <Callout tone="success" title="Documents added">
              {summary ? `This investigation now has ${plural(summary.totals.documents, 'document')} and ${plural(summary.totals.entities, 'entity', 'entities')}.` : 'The investigation has been updated.'}
            </Callout>
          )}
          {!ok && job.state === 'cancelled' && <Callout tone="info" title="Stopped">The run was stopped. Anything already extracted is kept. Run Add again to continue.</Callout>}
          {!ok && job.exit_code === 2 && (
            <Callout tone="warning" title="Paused, and safe to resume">
              The run stopped partway, for example at a rate limit or while a batch is being processed. Nothing is lost. Run Add again to continue.
            </Callout>
          )}
          {!ok && job.state === 'failed' && job.exit_code !== 2 && (
            <>
              <Callout tone="danger" title="The run did not finish">
                The output below says why. Documents already extracted are kept; fix the cause, then run Add again, ticking the box to retry any documents that failed.
              </Callout>
              <div className="log add-log" ref={(el) => el?.scrollTo({ top: el.scrollHeight })}>{tail.map((l, i) => <div key={i} className={l.stream === 'err' ? 'err' : undefined}>{l.text}</div>)}</div>
            </>
          )}
        </>
      )}
    </div>
  )
}

function DoneFooter({ job, onAgain }: { job?: Job; onAgain: () => void }) {
  const vault = useVault()
  const ok = job?.state === 'done'
  const { data: summary } = useRpc('vault.summary', ok ? { vault } : null)
  const go = (r: Parameters<typeof navigate>[0]) => {
    useApp.getState().closeAdd()
    navigate(r)
  }
  if (ok)
    return (
      <>
        <Button variant="ghost" onClick={() => useApp.getState().closeAdd()}>Close</Button>
        <Button onClick={() => go({ view: 'review' })}>Review what was found</Button>
        {summary?.briefing ? <Button variant="primary" iconRight={ArrowRight} onClick={() => go({ view: 'note', path: summary.briefing!.path })}>Read the briefing</Button> : <Button variant="primary" onClick={() => go({ view: 'home' })}>Go to Overview</Button>}
      </>
    )
  return (
    <>
      <Button variant="ghost" onClick={() => useApp.getState().closeAdd()}>Close</Button>
      {job && <Button onClick={() => go({ view: 'activity', job: job.id })}>Open full output</Button>}
      <Button variant="primary" icon={RefreshCw} onClick={onAgain}>{job?.exit_code === 2 ? 'Add again to continue' : 'Add again'}</Button>
    </>
  )
}

