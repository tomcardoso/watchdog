// Maintenance: every maintenance step as a card — what it does, its options,
// and a Run button. Anything that sends text to a model goes through the public-records gate.

import {
  BarChart3,
  BookOpen,
  ChevronRight,
  Cpu,
  Download,
  Eye,
  FolderOpen,
  Gauge,
  Hammer,
  Link2,
  Network,
  Play,
  RefreshCw,
  Coins,
  ScanSearch,
  ScanText,
  Sparkles,
  Telescope,
  Undo2
} from 'lucide-react'
import { LucideIcon } from 'lucide-react'
import { ReactNode, useState } from 'react'
import type { CitationReport, Effort, Estimate, RunOptions } from '@shared/api'
import { Badge, Button, Field, Segmented, Switch } from '@renderer/components/ui'
import { ModelPicker } from '@renderer/components/ModelPicker'
import { call, errorMessage, invalidate, useRpc } from '@renderer/lib/rpc'
import { flagsFor, runAction, startJob } from '@renderer/lib/jobs'
import { useEngineGate } from '@renderer/lib/engine'
import { EngineWait } from '@renderer/components/EngineWait'
import { fmtCost, plural } from '@renderer/lib/format'
import { navigate, toast, useApp } from '@renderer/lib/store'
import { usePublicRecordsGate } from './PublicRecordsGate'
import RecheckModal from '../entities/RecheckModal'

const EFFORTS: Effort[] = ['low', 'medium', 'high', 'xhigh', 'max']

/** The server maps RunOptions to flags; whether it includes the command name is not pinned down,
 * so accept either. */
async function argsFor(command: 'chew' | 'dig' | 'bark', options: RunOptions): Promise<string[]> {
  const a = await flagsFor(command, options)
  return a[0] === command ? a : [command, ...a]
}

async function launch(args: string[], label: string, kind?: string) {
  try {
    const job = await startJob(args, label, kind)
    navigate({ view: 'activity', job: job.id })
  } catch (e) {
    toast({ kind: 'error', title: `Could not start ${label.toLowerCase()}`, body: errorMessage(e) })
  }
}

function MCard({ icon: Icon, title, note, children, options, footer }: { icon: LucideIcon; title: string; note?: ReactNode; children: ReactNode; options?: ReactNode; footer: ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="card act-mcard">
      <div className="act-mcard-head">
        <div className="act-mcard-icon">
          <Icon />
        </div>
        <div className="grow">
          <div className="card-title">{title}</div>
        </div>
        {note}
      </div>
      <p className="act-mcard-text">{children}</p>
      {options && (
        <>
          <button className="act-disclose" aria-expanded={open} onClick={() => setOpen(!open)}>
            <ChevronRight /> Options
          </button>
          {open && <div className="act-options">{options}</div>}
        </>
      )}
      <div className="act-mcard-foot">{footer}</div>
    </div>
  )
}

function NumberField({ label, hint, value, onChange, placeholder }: { label: string; hint?: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <Field label={label} hint={hint}>
      <input className="input" inputMode="numeric" value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value.replace(/[^0-9]/g, ''))} />
    </Field>
  )
}

function EffortSelect({ value, onChange }: { value: Effort | ''; onChange: (v: Effort | '') => void }) {
  return (
    <select className="select" value={value} onChange={(e) => onChange(e.target.value as Effort | '')}>
      <option value="">Use the setting</option>
      {EFFORTS.map((e) => (
        <option key={e} value={e}>
          {e}
        </option>
      ))}
    </select>
  )
}

function Row({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <div className="act-optrow">
      <div>
        <div className="field-label">{label}</div>
        {hint && <div className="field-hint">{hint}</div>}
      </div>
      <div>{children}</div>
    </div>
  )
}

function EstimateBox({ est, onClose }: { est: Estimate; onClose: () => void }) {
  return (
    <div className="act-estimate">
      <pre className="selectable">{est.text}</pre>
      {est.all_models && est.all_models.length > 0 && (
        <table className="table">
          <tbody>
            {est.all_models.map((m) => (
              <tr key={m.label}>
                <td>{m.label}</td>
                <td className="muted">{m.provider}</td>
                <td className="tnum" style={{ textAlign: 'right' }}>{fmtCost(m.cost_usd)}</td>
                <td className="faint">{m.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Button size="sm" variant="ghost" onClick={onClose}>Dismiss</Button>
    </div>
  )
}

function useEstimator(stage: 'dig' | 'bark', options: () => RunOptions) {
  const vault = useApp((s) => s.project!.path)
  const [est, setEst] = useState<Estimate | null>(null)
  const [busy, setBusy] = useState<'one' | 'all' | null>(null)
  const run = async (all: boolean) => {
    setBusy(all ? 'all' : 'one')
    try {
      setEst(await call('ingest.estimate', { vault, stage, all_models: all, options: options() }))
    } catch (e) {
      toast({ kind: 'error', title: 'Could not estimate', body: errorMessage(e) })
    } finally {
      setBusy(null)
    }
  }
  return { est, busy, run, clear: () => setEst(null) }
}

// ── cards ────────────────────────────────────────────────────────────────────

function WatchCard() {
  const project = useApp((s) => s.project!)
  const jobs = useApp((s) => s.jobs)
  const running = Object.values(jobs).find((j) => j.kind === 'watch' && j.state === 'running' && j.vault === project.path)
  const [busy, setBusy] = useState(false)
  const toggle = async (on: boolean) => {
    setBusy(true)
    try {
      if (on) await startJob(['watch'], 'Watching incoming', 'watch')
      else if (running) await call('jobs.cancel', { id: running.id })
    } catch (e) {
      toast({ kind: 'error', title: 'Could not change watching', body: errorMessage(e) })
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="card act-watch">
      <div className="act-mcard-icon">
        <Eye />
      </div>
      <div className="grow">
        <div className="card-title">Watch incoming for new files</div>
        <div className="act-mcard-text" style={{ margin: '2px 0 0' }}>
          While this is on, files that land in <span className="mono">incoming/</span> are pre-processed (read and OCR’d, locally) as they arrive, starting with any already waiting. Nothing is sent to a model; extraction still waits for you.
        </div>
      </div>
      {running && (
        <Button size="sm" variant="ghost" onClick={() => navigate({ view: 'activity', job: running.id })}>
          Show output
        </Button>
      )}
      <Switch checked={!!running} disabled={busy} onChange={(v) => void toggle(v)} label="Watch incoming" />
    </div>
  )
}

function ChewCard({ incoming }: { incoming: number | null }) {
  const [workers, setWorkers] = useState('')
  const [chunks, setChunks] = useState('')
  return (
    <MCard
      icon={ScanText}
      title="Pre-processing"
      note={incoming ? <Badge tone="accent">{plural(incoming, 'file')} waiting</Badge> : undefined}
      options={
        <>
          <NumberField label="Files at once" hint="Leave blank to let Watchdog adapt to the batch." value={workers} onChange={setWorkers} placeholder="Adaptive" />
          <NumberField label="Chunks per large PDF" hint="Parallel pieces for big PDFs. Blank is adaptive." value={chunks} onChange={setChunks} placeholder="Adaptive" />
        </>
      }
      footer={
        <Button
          icon={Play}
          size="sm"
          onClick={async () => {
            const o: RunOptions = {}
            if (workers) o.chew_workers = Number(workers)
            if (chunks) o.chunk_workers = Number(chunks)
            await launch(await argsFor('chew', o), 'Pre-processing', 'chew')
          }}
        >
          Run pre-processing
        </Button>
      }
    >
      Converts every file in <span className="mono">incoming/</span> to text, applying OCR to pages that need it, splitting large PDFs into parallel chunks and checking for duplicates. It happens entirely on this computer: nothing is sent to a model. Press Stop to cancel; unfinished files stay in <span className="mono">incoming/</span>.
    </MCard>
  )
}

function DigCard({ queued, models }: { queued: number | null; models: ReturnType<typeof useModels> }) {
  const [limit, setLimit] = useState('')
  const [force, setForce] = useState(false)
  const [verify, setVerify] = useState<'default' | 'on' | 'off'>('default')
  const [model, setModel] = useState('')
  const [effort, setEffort] = useState<Effort | ''>('')
  const gate = usePublicRecordsGate()
  const build = (): RunOptions => {
    const o: RunOptions = {}
    if (limit) o.limit = Number(limit)
    if (force) o.force = true
    if (verify !== 'default') o.verify = verify === 'on'
    if (model) o.extractor_model = model
    if (effort) o.extractor_effort = effort
    return o
  }
  const e = useEstimator('dig', build)
  return (
    <MCard
      icon={Hammer}
      title="Processing"
      note={queued ? <Badge tone="accent">{plural(queued, 'document')} queued</Badge> : undefined}
      options={
        <>
          <NumberField label="Limit" hint="Extract only the next N queued documents; the rest stay queued." value={limit} onChange={setLimit} placeholder="All" />
          <Row label="Extractor model" hint="For this run only.">
            <ModelPicker compact value={model} onChange={setModel} models={models} emptyLabel="Use the setting" />
          </Row>
          <Row label="Extractor effort">
            <EffortSelect value={effort} onChange={setEffort} />
          </Row>
          <Row label="Second-read check" hint="Re-reads each document for facts the first pass missed. Adds roughly 15% to the cost.">
            <Segmented
              value={verify}
              onChange={setVerify}
              options={[
                { value: 'default', label: 'Setting' },
                { value: 'on', label: 'On' },
                { value: 'off', label: 'Off' }
              ]}
            />
          </Row>
          <Row label="Re-extract (--force)" hint="Extract again even where a saved extraction exists. Costs full price for every queued document.">
            <Switch checked={force} onChange={setForce} label="Force" />
          </Row>
        </>
      }
      footer={
        <div className="col" style={{ gap: 10, width: '100%' }}>
          <div className="row wrap" style={{ gap: 8 }}>
            <Button icon={Play} variant="primary" size="sm" loading={gate.busy} onClick={() => void gate.request({ args: ['dig', ...(limit ? ['--limit', limit] : []), ...(force ? ['--force'] : [])], label: 'Processing', kind: 'dig', options: build() }).catch(() => undefined)}>
              Run processing…
            </Button>
            <Button icon={Gauge} size="sm" loading={e.busy === 'one'} onClick={() => void e.run(false)}>
              Estimate
            </Button>
            <Button icon={BarChart3} size="sm" variant="ghost" loading={e.busy === 'all'} onClick={() => void e.run(true)}>
              Compare all models
            </Button>
          </div>
          {e.est && <EstimateBox est={e.est} onClose={e.clear} />}
        </div>
      }
    >
      Reads the queue and extracts facts, entities and dates from each document with a model, then stages the result. It sends the extracted text of every queued document to a cloud model, so it asks you to confirm the documents are public records first. Nothing is written to the vault until post-processing finishes the batch.
      {gate.modal}
    </MCard>
  )
}

function useModels() {
  return useRpc('settings.models', {}).data?.models ?? []
}

const BARK_STAGES: [keyof RunOptions, string, string][] = [
  ['finalizer_reconciliation_model', 'Reconciliation', 'Merging duplicate entities and flagging contradictions.'],
  ['finalizer_synthesis_model', 'Synthesis', 'Writing prose for entities named in more than one document.'],
  ['finalizer_timeline_model', 'Timeline', 'Folding same-date collisions and coarse restatements.'],
  ['finalizer_briefing_model', 'Briefing', 'The briefing itself.']
]

function BarkCard({ pending, models }: { pending: { docs: number; entities: number } | null; models: ReturnType<typeof useModels> }) {
  const [model, setModel] = useState('')
  const [effort, setEffort] = useState<Effort | ''>('')
  const [stage, setStage] = useState<Record<string, string>>({})
  const [skip, setSkip] = useState(false)
  const build = (): RunOptions => {
    const o: Record<string, unknown> = {}
    if (model) o.finalizer_model = model
    if (effort) o.finalizer_effort = effort
    for (const [k, v] of Object.entries(stage)) if (v) o[k] = v
    if (skip) o.skip_briefing = true
    return o as RunOptions
  }
  const e = useEstimator('bark', build)
  return (
    <MCard
      icon={Sparkles}
      title="Post-processing"
      note={pending ? <Badge tone="warning">{plural(pending.docs, 'document')} to finish</Badge> : undefined}
      options={
        <>
          <Row label="Finalizer model" hint="Used for every finishing step unless overridden below.">
            <ModelPicker compact value={model} onChange={setModel} models={models} emptyLabel="Use the setting" />
          </Row>
          <Row label="Finalizer effort">
            <EffortSelect value={effort} onChange={setEffort} />
          </Row>
          <div className="act-sub">Different model for one step</div>
          {BARK_STAGES.map(([k, label, hint]) => (
            <Row key={k} label={label} hint={hint}>
              <ModelPicker compact value={stage[k] ?? ''} onChange={(v) => setStage({ ...stage, [k]: v })} models={models} emptyLabel="Same as finalizer" />
            </Row>
          ))}
          <Row label="Skip the briefing" hint="Finishes everything else but makes no briefing call. The log entry is skipped too.">
            <Switch checked={skip} onChange={setSkip} label="Skip briefing" />
          </Row>
        </>
      }
      footer={
        <div className="col" style={{ gap: 10, width: '100%' }}>
          <div className="row wrap" style={{ gap: 8 }}>
            <Button icon={Play} variant="primary" size="sm" onClick={async () => void launch(await argsFor('bark', build()), 'Post-processing', 'bark')}>
              Run post-processing
            </Button>
            <Button icon={Gauge} size="sm" loading={e.busy === 'one'} onClick={() => void e.run(false)}>
              Estimate
            </Button>
            <Button icon={BarChart3} size="sm" variant="ghost" loading={e.busy === 'all'} onClick={() => void e.run(true)}>
              Compare all models
            </Button>
          </div>
          {e.est && <EstimateBox est={e.est} onClose={e.clear} />}
        </div>
      }
    >
      Finishes a batch that processing staged, or one an interruption left half-done: merges duplicate entities, flags contradictions between documents, writes entity summaries, reconciles the timeline and writes the briefing. Documents land in the vault at the start of this step. Safe to run again if it stops partway.
    </MCard>
  )
}

function SimpleCard({ icon, title, text, label, onRun, tone }: { icon: LucideIcon; title: string; text: ReactNode; label: string; onRun: () => Promise<void>; tone?: ReactNode }) {
  const [busy, setBusy] = useState(false)
  return (
    <MCard
      icon={icon}
      title={title}
      note={tone}
      footer={
        <Button
          icon={Play}
          size="sm"
          loading={busy}
          onClick={async () => {
            setBusy(true)
            try {
              await onRun()
            } catch (e) {
              toast({ kind: 'error', title: `${title} did not finish`, body: errorMessage(e) })
            } finally {
              setBusy(false)
            }
          }}
        >
          {label}
        </Button>
      }
    >
      {text}
    </MCard>
  )
}

function ExportCard() {
  const project = useApp((s) => s.project!)
  const [format, setFormat] = useState<'csv' | 'cypher'>('csv')
  const [dir, setDir] = useState<string | null>(null)
  const go = async (output: string | null) => {
    const args = ['export', '--format', format, ...(output ? ['--output', output] : [])]
    await launch(args, 'Export graph', 'export')
    setDir(output ?? `${project.path}/${project.slug}-export`)
  }
  return (
    <MCard
      icon={Network}
      title="Export the graph"
      options={
        <Row label="Format" hint={format === 'csv' ? 'nodes.csv and relationships.csv, for Neo4j import or Gephi.' : 'One graph.cypher of MERGE statements. Needs Neo4j 4.4 or later.'}>
          <Segmented value={format} onChange={setFormat} options={[{ value: 'csv', label: 'CSV' }, { value: 'cypher', label: 'Cypher' }]} />
        </Row>
      }
      footer={
        <div className="row wrap" style={{ gap: 8 }}>
          <Button
            icon={FolderOpen}
            size="sm"
            onClick={async () => {
              const out = await window.watchdog.dialog.openFolder({ title: 'Export the graph to…', grant: 'export destination' })
              if (out) await go(out)
            }}
          >
            Choose folder and export
          </Button>
          <Button size="sm" variant="ghost" onClick={() => void go(null)}>
            Export inside the vault
          </Button>
          {dir && (
            <Button size="sm" variant="ghost" icon={Download} onClick={() => window.watchdog.shell.showItemInFolder(dir)}>
              Show in folder
            </Button>
          )}
        </div>
      }
    >
      Writes the entity and relationship graph for network-analysis tools. It reads Watchdog's records only, with no model call. Only relationships stated in the documents are exported; edges to entities that were never profiled are dropped so the import stays valid. Wordings grouped as one relationship share its label, with each document's own wording kept beside it.
    </MCard>
  )
}

/** The cards that need the full engine: every control inside is disabled until setup has
 * finished (D272). `display: contents` keeps the grid layout untouched. */
// Every fact citation in saved answers, threads, briefings and the current-state page, checked
// against the stored facts (D283). Read-only: pages are never changed.
function CitationsCard() {
  const vault = useApp((s) => s.project?.path) ?? ''
  const [report, setReport] = useState<CitationReport | null>(null)
  const [busy, setBusy] = useState(false)
  const run = async () => {
    setBusy(true)
    try {
      setReport(await call('vault.checkCitations', { vault }))
    } catch (e) {
      toast({ kind: 'error', title: 'Could not check citations', body: errorMessage(e) })
    } finally {
      setBusy(false)
    }
  }
  const flagged = (report?.pages ?? []).filter((p) => p.not_found || p.disputed)
  const open = (path: string) => navigate(path.startsWith('briefings/') ? { view: 'briefings', path } : { view: 'note', path })
  return (
    <MCard
      icon={Link2}
      title="Check citations"
      note={report ? <Badge tone={report.not_found ? 'danger' : undefined}>{report.not_found ? plural(report.not_found, 'source not found', 'sources not found') : 'All found'}</Badge> : undefined}
      footer={
        <div className="act-cite-foot">
          <Button size="sm" loading={busy} onClick={() => void run()}>Check now</Button>
          {report && (
            <div className="act-cite-result">
              <div>
                {plural(report.citations, 'citation')} in {plural(report.checked, 'page')}: {report.found} found, {report.not_found} not found, {report.disputed} on disputed facts.
              </div>
              {flagged.map((p) => (
                <button key={p.path} className="srch-link" onClick={() => open(p.path)}>
                  {p.path}
                  {p.not_found ? ` · ${p.not_found} not found` : ''}
                  {p.disputed ? ` · ${p.disputed} disputed` : ''}
                </button>
              ))}
            </div>
          )}
        </div>
      }
    >
      Checks that every fact a saved answer, thread or briefing cites still exists, and lists citations of facts you marked Disputed. Text written by Claude may also contain uncited narrative; that is not flagged. Nothing is changed, and no model is used.
    </MCard>
  )
}

function RecheckCard() {
  const [open, setOpen] = useState(false)
  return (
    <>
    <MCard
      icon={ScanSearch}
      title="Re-check contradictions"
      footer={
        <Button size="sm" icon={Play} onClick={() => setOpen(true)}>
          Estimate the cost…
        </Button>
      }
    >
      When documents are added, their facts are compared with the facts already recorded, but recorded facts are not compared with each other again, and a conflict within a single document is not looked for. This asks the AI model to compare every recorded fact about every entity with two or more facts, and adds what it finds to Review. You see the cost before anything is sent. To check one entity, use Re-check on its page.
    </MCard>
    <RecheckModal open={open} onClose={() => setOpen(false)} />
    </>
  )
}

function Gated({ children }: { children: ReactNode }) {
  const ready = useEngineGate().ready
  return (
    <fieldset className="act-gated" disabled={!ready}>
      {children}
    </fieldset>
  )
}

export default function MaintenancePanel() {
  const project = useApp((s) => s.project!)
  const models = useModels()
  const pipeline = useRpc('vault.pipeline', { vault: project.path })
  const p = pipeline.data
  return (
    <div className="col" style={{ gap: 16 }}>
      <div className="act-intro">
        These are the steps <span className="mono">Add documents</span> runs for you, plus repairs. Reach for them to run one step at a time, to check an extraction before it reaches the vault, or to fix something that stopped.
      </div>
      <EngineWait />
      <Gated>
        <WatchCard />
      </Gated>
      <div className="act-mgrid">
        <Gated>
          <ChewCard incoming={p?.incoming.length ?? null} />
          <DigCard queued={p?.queued.length ?? null} models={models} />
          <BarkCard pending={p?.pending_finalization ?? null} models={models} />
        </Gated>
        <Gated>
        <SimpleCard
          icon={Undo2}
          title="Requeue failed documents"
          tone={p ? <Badge tone={p.failed.length ? 'danger' : undefined}>{plural(p.failed.length, 'failed document')}</Badge> : undefined}
          text="Moves documents that failed extraction back into the queue without running them, so a later extraction run tries them again. A failure from a temporary cause, such as a rate limit, is worth retrying; one that fails repeatedly may need a different model."
          label="Requeue"
          onRun={async () => {
            const out = await runAction(['requeue'])
            const n = /Requeued\s+(\d+)/.exec(out)?.[1]
            toast(n ? { kind: 'success', title: 'Moved back into the queue', body: `${plural(Number(n), 'document')}. Run Add documents to extract them again.` } : { kind: 'info', title: 'No failed documents to requeue' })
            invalidate('vault.', 'projects.', 'ingest.')
          }}
        />
        </Gated>
        <SimpleCard
          icon={Telescope}
          title="Lead sweep"
          text="Runs the full lead sweep, a deterministic pass over the entity graph with no model call: entities named but never profiled, recurring entities with no relationships, entities carrying unresolved contradictions, and facts that still need verifying. Step through them in Review."
          label="Run lead sweep"
          onRun={() => launch(['leads'], 'Lead sweep', 'leads')}
        />
        <SimpleCard
          icon={RefreshCw}
          title="Rebuild the timeline"
          text="Regenerates timeline.md from the canonical event files. Deterministic, no model call. Useful if the note was deleted or edited by mistake: nothing is lost, because it is generated output."
          label="Rebuild timeline"
          onRun={() => launch(['timeline'], 'Rebuild timeline', 'timeline')}
        />
        <Gated>
        <SimpleCard
          icon={ScanText}
          title="Rebuild notes"
          text="Rewrites every entity and document note from what Watchdog keeps on disk: each document's facts, the entity summaries and the record of merges and contradictions. No model call, no tokens. Use it if a note was deleted or edited by mistake. Your own Notes sections are kept as they are."
          label="Rebuild notes"
          onRun={async () => {
            const v = useApp.getState().project?.path
            if (!v) return
            try {
              const job = await call('jobs.rebuildNotes', { vault: v })
              useApp.getState().upsertJob(job)
              navigate({ view: 'activity', job: job.id })
            } catch (e) {
              toast({ kind: 'error', title: 'Could not start rebuilding notes', body: errorMessage(e) })
            }
          }}
        />
        </Gated>
        <CitationsCard />
        <Gated>
          <RecheckCard />
        </Gated>
        <Gated>
        <SimpleCard
          icon={Cpu}
          title="Rebuild the search index"
          text="Rebuilds the semantic and full-text indexes from what is already on disk: no OCR, no model calls, no tokens. Run it after changing the embedding model in Settings, since vectors from two models cannot be mixed, or after merging entities."
          label="Reindex"
          onRun={() => launch(['reindex'], 'Reindex', 'reindex')}
        />
        </Gated>
        <MCard
          icon={Coins}
          title="Usage"
          footer={<Button size="sm" onClick={() => navigate({ view: 'activity', tab: 'usage' })}>Open Usage</Button>}
        >
          Tokens, cost and timing for each processing run, by stage. It only reads recorded files, so it is free to look.
        </MCard>
        <ExportCard />
        <SimpleCard
          icon={BookOpen}
          title="Refresh Claude setup"
          text={
            <>
              Updates this investigation’s shortcuts, Claude instructions and Claude settings after you upgrade Watchdog. Your own notes below the end marker in <span className="mono">.claude/CLAUDE.md</span> are kept. Record skills are global and never need this.
            </>
          }
          label="Refresh now"
          onRun={async () => {
            const out = await runAction(['settings', 'refresh-skills'])
            toast({ kind: 'success', title: 'Claude setup updated', body: out.trim().split('\n').pop() || undefined })
          }}
        />
      </div>
    </div>
  )
}
