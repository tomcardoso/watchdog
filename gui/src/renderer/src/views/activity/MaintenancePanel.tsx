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
  LifeBuoy,
  Link2,
  Network,
  Play,
  RefreshCcw,
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
import type { CitationReport, Effort, Estimate, OpName, OpParams, RunOptions } from '@shared/api'
import { Badge, Button, Callout, Field, Modal, Segmented, Skeleton, Switch } from '@renderer/components/ui'
import { ModelPicker } from '@renderer/components/ModelPicker'
import { call, errorMessage, invalidate, useRpc } from '@renderer/lib/rpc'
import { runAction, startJob } from '@renderer/lib/jobs'
import { lastExportFolder } from '@renderer/lib/exportFolder'
import { useEngineGate } from '@renderer/lib/engine'
import { EngineWait } from '@renderer/components/EngineWait'
import { fmtBytes, fmtCost, plural } from '@renderer/lib/format'
import { navigate, toast, useApp } from '@renderer/lib/store'
import { usePublicRecordsGate } from './PublicRecordsGate'
import RecheckModal from '../entities/RecheckModal'
import MigrateModal from './MigrateModal'

const EFFORTS: Effort[] = ['low', 'medium', 'high', 'xhigh', 'max']

async function launch<O extends OpName>(op: O, params: OpParams[O], label: string) {
  try {
    const job = await startJob(op, params, label)
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
      if (on) await startJob('watch', {}, 'Watching for new files')
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
        <div className="card-title">Watch for new files</div>
        <div className="act-mcard-text" style={{ margin: '2px 0 0' }}>
          While this is on, files you put in the investigation’s <span className="mono">incoming</span> folder (File → Show in Folder) are pre-processed (read and OCR’d, locally) as they arrive, starting with any already waiting. Nothing is sent to a model; extraction still waits for you.
        </div>
      </div>
      {running && (
        <Button size="sm" variant="ghost" onClick={() => navigate({ view: 'activity', job: running.id })}>
          Show output
        </Button>
      )}
      <Switch checked={!!running} disabled={busy} onChange={(v) => void toggle(v)} label="Watch for new files" />
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
            await launch('chew', o, 'Pre-processing')
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
            <Button icon={Play} variant="primary" size="sm" loading={gate.busy} onClick={() => void gate.request({ op: 'dig', params: build(), label: 'Processing', options: build() }).catch(() => undefined)}>
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
  ['finalizer_writing_model', 'Writing', 'Entity summaries and the briefing.'],
  ['finalizer_housekeeping_model', 'Housekeeping', 'Entity matching, contradictions, the timeline, dates and other tidying.']
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
            <Button icon={Play} variant="primary" size="sm" onClick={() => void launch('bark', build(), 'Post-processing')}>
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
  const jobs = useApp((s) => s.jobs)
  const [format, setFormat] = useState<'csv' | 'cypher'>('csv')
  const dir = lastExportFolder(jobs, project.path)
  const go = async (output: string | null) => {
    await launch('export', { format, output }, 'Export graph')
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

// A copy of the investigation for troubleshooting (D323), or a report of counts alone that is safe
// to post publicly (D331). The report is shown in full before anything is saved; the size of the
// full copy is read from the files before anything is written.
type CopyKind = 'diagnostics' | 'full'

function TroubleshootingModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const vault = useApp((s) => s.project!.path)
  const [kind, setKind] = useState<CopyKind>('diagnostics')
  const [originals, setOriginals] = useState(false)
  const q = useRpc('vault.troubleshootingSize', open && kind === 'full' ? { vault } : null)
  const report = useRpc('vault.diagnosticsReport', open && kind === 'diagnostics' ? { vault } : null)
  const s = q.data
  const bytes = s ? s.bytes + (originals ? s.originals_bytes : 0) : null
  const files = s ? s.files + (originals ? s.originals_files : 0) : null
  const ready = kind === 'diagnostics' ? !!report.data : !!s
  const choose = async () => {
    const out = await window.watchdog.dialog.openFolder({
      title: kind === 'diagnostics' ? 'Save the diagnostics report in…' : 'Save the copy for troubleshooting in…',
      grant: 'export destination'
    })
    if (!out) return
    onClose()
    if (kind === 'diagnostics') await launch('diagnostics-report', { output: out }, 'Diagnostics report')
    else await launch('troubleshooting-copy', { output: out, originals }, 'Copy for troubleshooting')
  }
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={
        <span className="row" style={{ gap: 8 }}>
          <LifeBuoy style={{ width: 18, height: 18, color: 'var(--accent)' }} />
          Export a copy for troubleshooting
        </span>
      }
      sub={kind === 'diagnostics' ? 'One text file of counts, safe to post with a report of a problem.' : 'One zip file of this investigation’s records, for your own use. Never attach it to an issue: issues are public.'}
      footer={
        <>
          <Button autoFocus onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" icon={FolderOpen} disabled={!ready} onClick={() => void choose()}>
            Choose folder and export
          </Button>
        </>
      }
    >
      <div className="col" style={{ gap: 14 }}>
        <Segmented
          value={kind}
          onChange={setKind}
          options={[
            { value: 'diagnostics', label: 'Diagnostics only (safe to share)' },
            { value: 'full', label: 'Full copy (private)' }
          ]}
        />
        {kind === 'diagnostics' ? (
          <>
            <p className="muted" style={{ margin: 0 }}>
              A report of counts and shapes: how many documents of each file type, how many facts, Review items and merges, which kinds of warning the processing log holds, and the models, calls, tokens and cost of each run. It never holds a file name, title, name, quote, fact, figure, identifier, date or summary, and no model is used.
            </p>
            <Callout tone="warning" title="Read it before you post it">
              Even counts can hint at things, such as the languages or kinds of documents you work with. This is the whole report; nothing else is saved.
            </Callout>
            {report.isError && <Callout tone="danger" title="Could not build the report">{errorMessage(report.error)}</Callout>}
            {!report.data && !report.isError && <Skeleton w="50%" />}
            {report.data && (
              <div className="act-estimate" style={{ maxHeight: 320, overflow: 'auto' }}>
                <pre>{report.data.text}</pre>
              </div>
            )}
          </>
        ) : (
          <>
            <p className="muted" style={{ margin: 0 }}>
              The copy holds what Watchdog recorded about this investigation: its records of documents and entities, each document’s extraction, the processing history and usage, the briefings, the entity and document notes, and a note of the Watchdog version, this computer’s operating system and your settings. It never holds your keys or sign-ins, your conversations with Claude, or earlier versions of files.
            </p>
            <Callout tone="warning" title="This is still investigation material">
              Even without the original documents, the notes and extractions quote them, name the people and organizations in them and record what you found. Send the copy only to someone you would show the documents to, and do not post it publicly.
            </Callout>
            <Row label="Include the original documents" hint="The files themselves and the text read from them. Needed to reproduce a problem with reading a document, but much larger and more sensitive.">
              <Switch checked={originals} onChange={setOriginals} label="Include originals" />
            </Row>
            {q.isError && <Callout tone="danger" title="Could not work out the size">{errorMessage(q.error)}</Callout>}
            {!s && !q.isError && <Skeleton w="50%" />}
            {s && bytes !== null && files !== null && (
              <div>
                About {fmtBytes(bytes)} in {plural(files, 'file')}, before compression.
                {!originals && s.originals_files > 0 && <span className="faint"> The originals would add {fmtBytes(s.originals_bytes)}.</span>}
              </div>
            )}
          </>
        )}
      </div>
    </Modal>
  )
}

function TroubleshootingCard() {
  const project = useApp((s) => s.project!)
  const jobs = useApp((s) => s.jobs)
  const [open, setOpen] = useState(false)
  const zip = lastExportFolder(jobs, project.path, ['troubleshooting-copy', 'diagnostics-report'])
  return (
    <>
      <MCard
        icon={LifeBuoy}
        title="Export a copy for troubleshooting"
        footer={
          <div className="row wrap" style={{ gap: 8 }}>
            <Button icon={Play} size="sm" onClick={() => setOpen(true)}>
              Export a copy…
            </Button>
            {zip && (
              <Button size="sm" variant="ghost" icon={Download} onClick={() => window.watchdog.shell.showItemInFolder(zip)}>
                Show in folder
              </Button>
            )}
          </div>
        }
      >
        Writes either a text report of counts alone, safe to post with a report of a problem, or one zip file of Watchdog’s records and notes for this investigation, for your own use. No model is used.
      </MCard>
      <TroubleshootingModal open={open} onClose={() => setOpen(false)} />
    </>
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

// Bring this investigation up to date (D336): the improvements made since its documents were
// processed, applied to what it holds. The badge says how many steps are waiting.
function MigrateCard() {
  const vault = useApp((s) => s.project!.path)
  const q = useRpc('migrations.status', { vault })
  const [open, setOpen] = useState(false)
  const n = q.data?.pending.length ?? 0
  return (
    <>
      <MCard
        icon={RefreshCcw}
        title="Bring this investigation up to date"
        note={q.data ? <Badge tone={n ? 'accent' : undefined}>{n ? plural(n, 'step') + ' waiting' : 'Up to date'}</Badge> : undefined}
        footer={
          <Button size="sm" icon={Play} disabled={!n} onClick={() => setOpen(true)}>
            Review the steps…
          </Button>
        }
      >
        Applies improvements made to Watchdog since this investigation’s documents were processed, without processing them again: dates the old date reader lost, records with the same name, fact tags and relationships that name no entity, and other fixes. You see each step and the cost before anything is sent. A step that stops waits for the next time.
      </MCard>
      <MigrateModal open={open} onClose={() => setOpen(false)} />
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
            const { requeued: n } = await runAction<{ requeued: number }>('requeue', {})
            toast(n ? { kind: 'success', title: 'Moved back into the queue', body: `${plural(n, 'document')}. Run Add documents to extract them again.` } : { kind: 'info', title: 'No failed documents to requeue' })
            invalidate('vault.', 'projects.', 'ingest.')
          }}
        />
        </Gated>
        <SimpleCard
          icon={Telescope}
          title="Lead sweep"
          text="Runs the full lead sweep, a deterministic pass over the entity graph with no model call: entities named but never profiled, recurring entities with no relationships, entities carrying unresolved contradictions, and relationships the model inferred. Step through them in Review."
          label="Run lead sweep"
          onRun={() => launch('lead-sweep', {}, 'Lead sweep')}
        />
        <SimpleCard
          icon={RefreshCw}
          title="Rebuild the timeline"
          text="Regenerates timeline.md from the canonical event files. Deterministic, no model call. Useful if the note was deleted or edited by mistake: nothing is lost, because it is generated output."
          label="Rebuild timeline"
          onRun={() => launch('rebuild-timeline', {}, 'Rebuild timeline')}
        />
        <Gated>
        <SimpleCard
          icon={ScanText}
          title="Rebuild notes"
          text="Rewrites every entity and document note from what Watchdog keeps on disk: each document's facts, the entity summaries and the record of merges and contradictions. No model call, no tokens. Use it if a note was deleted or edited by mistake. Your own Notes sections are kept as they are."
          label="Rebuild notes"
          onRun={async () => {
            await launch('rebuild-notes', {}, 'Rebuild notes')
          }}
        />
        </Gated>
        <CitationsCard />
        <Gated>
          <RecheckCard />
        </Gated>
        <Gated>
          <MigrateCard />
        </Gated>
        <Gated>
        <SimpleCard
          icon={Cpu}
          title="Rebuild the search index"
          text="Rebuilds the semantic and full-text indexes from what is already on disk: no OCR, no model calls, no tokens. Run it after changing the embedding model in Settings, since vectors from two models cannot be mixed, or after merging entities."
          label="Reindex"
          onRun={() => launch('reindex', {}, 'Reindex')}
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
        <TroubleshootingCard />
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
            await runAction('refresh-claude-setup', {})
            toast({ kind: 'success', title: 'Claude setup updated', body: 'The shortcuts, instructions and settings match this version of Watchdog.' })
          }}
        />
      </div>
    </div>
  )
}
