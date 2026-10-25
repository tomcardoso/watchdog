// Overview of the open investigation: the GUI version of bare `watchdog` — the latest briefing and
// its headline, what is waiting on the journalist, what is in progress, and ways in to explore.

import {
  Bell, CalendarRange, Copy, Download, ExternalLink, FilePlus2, FileSearch, Files, FolderOpen, Hourglass,
  Lightbulb, MessageSquareText, Pencil, RefreshCw, Scale, Search, Shapes, TriangleAlert, Upload, ArrowRight, Check, Inbox, Link2, FolderInput, X,
  ShieldAlert, ShieldCheck, GitMerge, ChevronDown, ChevronRight, Sparkles, FileQuestion, RefreshCcw
} from 'lucide-react'
import { ReactNode, useEffect, useRef, useState } from 'react'
import { Button, ErrorNote, Skeleton, Stat } from '@renderer/components/ui'
import { DocThumb } from '@renderer/components/DocThumb'
import { EntityAvatar } from '@renderer/components/EntityChip'
import { Markdown } from '@renderer/components/Markdown'
import { typeMeta } from '@renderer/lib/entityTypes'
import { fmtDate, fmtNum, fmtRelative, plural } from '@renderer/lib/format'
import { runAction, startJob, useJobRunningIn } from '@renderer/lib/jobs'
import { runLockState, type RunLockState } from '@renderer/lib/runlock'
import { useEngineGate } from '@renderer/lib/engine'
import { EngineWait } from '@renderer/components/EngineWait'
import { call, errorMessage, invalidate, useRpc } from '@renderer/lib/rpc'
import { navigate, toast, useApp, useVault } from '@renderer/lib/store'
import type { ReviewKind, Summary } from '@shared/api'
import { StartHereList, describeAdded } from '@renderer/components/StartHere'
import { BillingCard } from './BillingCard'
import { SpendingLimitCard } from './SpendingLimitCard'
import MigrateModal from '../activity/MigrateModal'
import './home.css'

/** A briefing's first screenful: whole paragraphs up to roughly `max` characters. */
function excerpt(body: string, max = 1500): string {
  const clean = body.replace(/<!--[\s\S]*?-->/g, '').replace(/^\s*# .*\n+/, '').trim()
  if (clean.length <= max) return clean
  const paras = clean.split(/\n{2,}/)
  let out = ''
  for (const p of paras) {
    if (out && out.length + p.length > max) break
    out += (out ? '\n\n' : '') + p
  }
  return out
}

export default function HomeView() {
  const vault = useVault()
  const { data: s, error, isLoading, refetch } = useRpc('vault.summary', { vault }, { refetchInterval: 20_000 })
  const running = useJobRunningIn(vault)
  const { data: pipe } = useRpc('vault.pipeline', { vault }, { refetchInterval: running ? 4_000 : 20_000 })
  const briefingPath = s?.briefing?.path
  const { data: note, isLoading: noteLoading } = useRpc('vault.note', briefingPath ? { vault, path: briefingPath } : null)

  if (error) {
    return (
      <div className="page">
        <div className="page-inner">
          <ErrorNote error={error} retry={() => void refetch()} />
        </div>
      </div>
    )
  }

  const empty = !!s && s.totals.documents === 0 && !s.has_work
  // Two groups (D327): what needs the reporter's judgement, shown, and tidy-up, folded to one line.
  const judgement: WaitRow[] = s
    ? ([
        { kind: 'contradictions', n: s.contradictions, one: 'contradiction', many: 'contradictions', hint: 'Facts that disagree across documents', icon: Scale },
        { kind: 'leads', n: s.leads, one: 'open lead', many: 'open leads', hint: 'Names and threads worth chasing', icon: Lightbulb },
        { kind: 'alerts', n: s.alerts, one: 'alert', many: 'alerts', hint: 'Matches for names you are watching', icon: Bell },
        { kind: 'verification', n: s.verification?.disputed ?? 0, one: 'disputed fact', many: 'disputed facts', hint: 'Facts you marked as not supported by the source', icon: ShieldAlert }
      ] as WaitRow[]).filter((w) => w.n > 0)
    : []
  const tidy: WaitRow[] = s
    ? ([
        { kind: 'duplicates', n: s.near_duplicates, one: 'possible duplicate document', many: 'possible duplicate documents', hint: 'Near-copies to confirm or dismiss', icon: Copy },
        { kind: 'merges', n: s.possible_same ?? 0, one: 'possible same entity', many: 'possible same entities', hint: 'Two records that may be one person or company', icon: GitMerge },
        { kind: 'requests', n: s.requests ?? 0, one: 'document request', many: 'document requests', hint: 'Records to ask for, grouped by who holds them', icon: FileQuestion }
      ] as WaitRow[]).filter((w) => w.n > 0)
    : []

  return (
    <div className="page">
      <div className="page-inner home">
        <Header />

        {isLoading || !s ? (
          <HomeSkeleton />
        ) : (
          <>
            {s.has_work && <WorkBanner s={s} run={runLockState(pipe?.locks)} stopped={pipe?.stopped_run ?? null} />}
            {!empty && <MigrationBanner />}
            {empty ? (
              <DropCard />
            ) : (
              <div className="home-stats">
                <Stat icon={Files} label="Documents" value={fmtNum(s.totals.documents)} sub={`${fmtNum(s.totals.pages)} pages`} onClick={() => navigate({ view: 'documents' })} />
                <Stat icon={Shapes} label="Entities" value={fmtNum(s.totals.entities)} onClick={() => navigate({ view: 'entities' })} />
                <Stat icon={ShieldCheck} label="Facts" value={fmtNum(s.verification?.facts ?? 0)} sub={s.verification?.disputed ? `${fmtNum(s.verification.disputed)} disputed` : undefined} onClick={() => navigate({ view: 'review', kind: 'verification' })} />
                <Stat icon={CalendarRange} label="Timeline events" value={fmtNum(s.totals.events)} onClick={() => navigate({ view: 'timeline' })} />
              </div>
            )}

            <div className="home-cols">
              <div className="home-main">
                {!empty && (
                  <>
                    <StartHere s={s} />
                    {s.headline && (
                      <blockquote className="home-pull">
                        <span className="eyebrow">Where things stand</span>
                        <Markdown text={s.headline} compact className="home-pull-text" />
                      </blockquote>
                    )}
                    <section className="card home-briefing">
                      <div className="home-briefing-head">
                        <div className="grow">
                          <div className="eyebrow">Latest briefing</div>
                          <div className="home-briefing-title">{s.briefing ? note?.title ?? s.briefing.name : 'No briefing yet'}</div>
                          {s.briefing?.date && <div className="faint" style={{ fontSize: 'var(--fs-sm)' }}>{fmtDate(s.briefing.date)}</div>}
                        </div>
                        {s.briefing && (
                          <Button iconRight={ArrowRight} onClick={() => navigate({ view: 'note', path: s.briefing!.path })}>
                            Read briefing
                          </Button>
                        )}
                      </div>
                      {s.briefing ? (
                        noteLoading ? (
                          <div className="col" style={{ padding: '4px 22px 22px' }}>
                            <Skeleton h={14} />
                            <Skeleton h={14} w="92%" />
                            <Skeleton h={14} w="70%" />
                          </div>
                        ) : (
                          <div className="home-briefing-body">
                            <Markdown text={excerpt(note?.body ?? '')} compact />
                          </div>
                        )
                      ) : (
                        <p className="muted" style={{ padding: '0 22px 22px', margin: 0 }}>
                          Add documents to get the first briefing. It summarizes what was found and what to check next.
                        </p>
                      )}
                    </section>

                    {s.recent_documents.length > 0 && (
                      <section>
                        <div className="home-section-head">
                          <h2>Recent documents</h2>
                          <Button variant="ghost" size="sm" iconRight={ArrowRight} onClick={() => navigate({ view: 'documents' })}>All documents</Button>
                        </div>
                        <div className="home-recent">
                          {s.recent_documents.slice(0, 8).map((d) => (
                            <button key={d.sha} className="home-doc" onClick={() => navigate({ view: 'document', sha: d.sha })}>
                              <DocThumb sha={d.sha} ext={d.ext} original={d.original} title={d.title ?? d.filename} summary={d.summary} width={180} />
                              <span className="t clamp-2">{d.title ?? d.filename}</span>
                              <span className="m">{d.date_of_document ? fmtDate(d.date_of_document) : d.ingested_at ? `Added ${fmtRelative(d.ingested_at)}` : d.ext.toUpperCase()}</span>
                            </button>
                          ))}
                        </div>
                      </section>
                    )}
                  </>
                )}
              </div>

              <aside className="home-side">
                <Waiting judgement={judgement} tidy={tidy} s={s} empty={empty} />
                <InProgress s={s} failedDocs={pipe?.failed ?? []} />
                <BillingCard vault={vault} />
                <SpendingLimitCard vault={vault} />
                {s.top_entities.length > 0 && (
                  <section className="card home-panel">
                    <h3>Most connected</h3>
                    <div className="home-ents">
                      {s.top_entities.slice(0, 7).map((e) => (
                        <button key={e.id} className="home-ent" onClick={() => navigate({ view: 'entity', id: e.id })}>
                          <EntityAvatar type={e.type} size={30} />
                          <span className="grow col" style={{ gap: 0, alignItems: 'flex-start' }}>
                            <span className="n truncate">{e.name}</span>
                            <span className="k">{typeMeta(e.type).label}</span>
                          </span>
                          <span className="c tnum">{plural(e.doc_count, 'doc')}</span>
                        </button>
                      ))}
                    </div>
                  </section>
                )}
              </aside>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ── Header ───────────────────────────────────────────────────────────────────
function Header() {
  const project = useApp((s) => s.project)!
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(project.description ?? '')
  const [saving, setSaving] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (editing) input.current?.select()
  }, [editing])

  const save = async () => {
    const next = text.trim()
    if (next === (project.description ?? '')) return setEditing(false)
    setSaving(true)
    try {
      await runAction('projects-describe', { slug: project.slug, description: next })
      const fresh = await call('projects.get', { slug: project.slug })
      useApp.setState({ project: fresh })
      invalidate('projects.')
      setEditing(false)
    } catch (e) {
      toast({ kind: 'error', title: 'Could not save the description', body: errorMessage(e) })
    } finally {
      setSaving(false)
    }
  }

  return (
    <header className="home-head">
      <div className="grow">
        <div className="eyebrow">Investigation</div>
        <h1 className="home-title">{project.name}</h1>
        {editing ? (
          <div className="row" style={{ marginTop: 6, maxWidth: 640 }}>
            <input
              ref={input}
              className="input"
              value={text}
              disabled={saving}
              placeholder="One line on what this investigation is about"
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void save()
                if (e.key === 'Escape') {
                  setText(project.description ?? '')
                  setEditing(false)
                }
              }}
            />
            <Button variant="primary" size="sm" icon={Check} loading={saving} tip="Save" onClick={() => void save()} />
            <Button variant="ghost" size="sm" icon={X} tip="Cancel" onClick={() => { setText(project.description ?? ''); setEditing(false) }} />
          </div>
        ) : (
          <button className="home-desc" onClick={() => { setText(project.description ?? ''); setEditing(true) }} title="Edit description">
            <span className={project.description ? '' : 'faint'}>{project.description || 'Add a one-line description'}</span>
            <Pencil />
          </button>
        )}
      </div>
      <div className="row home-quick">
        <Button icon={MessageSquareText} onClick={() => navigate({ view: 'ask' })}>Ask Claude</Button>
        <Button icon={Search} onClick={() => navigate({ view: 'search' })}>Search</Button>
        <Button icon={ExternalLink} variant="ghost" tip="Open in Obsidian" onClick={() => void window.watchdog.shell.openInObsidian(project.path)} />
        <Button icon={FolderOpen} variant="ghost" tip="Show folder" onClick={() => void window.watchdog.shell.openPath(project.path)} />
      </div>
    </header>
  )
}

// ── Finish adding ────────────────────────────────────────────────────────────
function WorkBanner({ s, run, stopped }: { s: Summary; run: RunLockState | null; stopped: string | null }) {
  const engine = useEngineGate()
  const n = s.incoming + s.awaiting_dig + s.awaiting_bark
  const text =
    n > 0
      ? `${plural(n, 'document')} ${n === 1 ? 'is' : 'are'} waiting to be added`
      : 'A batch is waiting to be finished'
  const sub =
    n > 0
      ? [s.incoming && `${plural(s.incoming, 'file')} in incoming`, s.awaiting_dig && `${s.awaiting_dig} to extract`, s.awaiting_bark && `${s.awaiting_bark} to finish`].filter(Boolean).join(' · ')
      : 'The documents are extracted. The finishing step writes the entity summaries, timeline and briefing.'
  return (
    <div className="home-banner">
      <div className="ico"><FilePlus2 /></div>
      <div className="grow">
        <div className="t">{run ? run.title : text}</div>
        <div className="s">{run ? run.detail : engine.reason ?? stopped ?? sub}</div>
      </div>
      <Button variant="primary" size="lg" disabled={!!run || !engine.ready} onClick={() => useApp.getState().openAdd()}>
        {n > 0 ? `Finish adding ${plural(n, 'document')}` : 'Finish the batch'}
      </Button>
    </div>
  )
}

// ── Bring up to date (D336) ──────────────────────────────────────────────────
// Shown once: when the investigation has pending migrations the reporter has not dismissed the
// banner for. Maintenance keeps the same step until it is done.
function MigrationBanner() {
  const vault = useVault()
  const { data } = useRpc('migrations.status', { vault })
  const [open, setOpen] = useState(false)
  if (!data?.banner) return null
  const n = data.pending.length
  const dismiss = async () => {
    try {
      await call('migrations.dismissBanner', { vault })
      invalidate('migrations.')
    } catch (e) {
      toast({ kind: 'error', title: 'Could not hide this', body: errorMessage(e) })
    }
  }
  return (
    <div className="home-banner">
      <div className="ico"><RefreshCcw /></div>
      <div className="grow">
        <div className="t">Watchdog has improved since these documents were processed</div>
        <div className="s">{plural(n, 'step')} can bring this investigation up to date without processing its documents again. You see the cost first. It stays in Activity → Maintenance if you hide this.</div>
      </div>
      <Button variant="ghost" onClick={() => void dismiss()}>Not now</Button>
      <Button variant="primary" onClick={() => setOpen(true)}>Review the steps</Button>
      <MigrateModal open={open} onClose={() => setOpen(false)} />
    </div>
  )
}

// ── Empty vault ──────────────────────────────────────────────────────────────
function DropCard() {
  const engine = useEngineGate()
  const pick = async (folders: boolean) => {
    const paths = await window.watchdog.dialog.openFiles({ title: folders ? 'Choose a folder of documents' : 'Choose documents to add', folders, multi: true })
    if (paths.length) useApp.getState().openAdd(paths)
  }
  return (
    <div className="home-drop">
      <div className="ico"><Upload /></div>
      <h2>Add your first documents</h2>
      <p>
        Drag PDFs, Word files, spreadsheets, emails or scans into this window. Watchdog copies them into the investigation, reads them on this computer, and only then asks
        before anything is sent to a model.
      </p>
      {!engine.ready && <EngineWait style={{ textAlign: 'left', margin: '0 auto 16px', maxWidth: 560 }} />}
      <div className="row" style={{ justifyContent: 'center' }}>
        <Button variant="primary" size="lg" icon={FilePlus2} disabled={!engine.ready} onClick={() => void pick(false)}>Choose files…</Button>
        <Button size="lg" icon={FolderInput} disabled={!engine.ready} onClick={() => void pick(true)}>Choose a folder…</Button>
        <Button size="lg" icon={Link2} onClick={() => window.dispatchEvent(new CustomEvent('wd:command', { detail: 'fetch-links' }))}>Fetch web links…</Button>
      </div>
    </div>
  )
}

// ── Start here ───────────────────────────────────────────────────────────────
function StartHere({ s }: { s: Summary }) {
  // One line on what is new (D328): since the last visit to Review once there has been one,
  // otherwise what the latest batch added.
  const line = s.summary_line
  const what = line ? describeAdded(line.added) : ''
  if (!s.start_here?.length && !what) return null
  return (
    <section className="card home-panel home-start">
      <div className="home-section-head" style={{ margin: '0 12px 6px' }}>
        <h2>Start here</h2>
        <Button variant="ghost" size="sm" iconRight={ArrowRight} onClick={() => navigate({ view: 'review' })}>Review</Button>
      </div>
      {what && line && (
        <div className="sh-new">
          <p>
            <Sparkles className="sh-new-ico" />{' '}
            {line.kind === 'since'
              ? `New since you last opened Review${line.at ? ` (${fmtRelative(line.at)})` : ''}, and still open: ${what}.`
              : `The latest batch${line.at ? ` (${fmtRelative(line.at)})` : ''} added ${what}.`}
          </p>
        </div>
      )}
      {s.start_here?.length ? (
        <StartHereList
          items={s.start_here}
          onPick={(it) => navigate(it.kind === 'alerts' && it.term ? { view: 'review', kind: 'alerts', filter: it.term, focus: it.rid } : { view: 'review', kind: it.kind, focus: it.rid })}
        />
      ) : (
        <p className="faint" style={{ margin: '0 12px 10px', fontSize: 'var(--fs-sm)' }}>Nothing needs your judgement right now.</p>
      )}
    </section>
  )
}

// ── Waiting on you ───────────────────────────────────────────────────────────
interface WaitRow { kind: string; n: number; one: string; many: string; hint: string; icon: typeof Scale }

function WaitButton({ w }: { w: WaitRow }) {
  return (
    <button className="home-wait" onClick={() => navigate({ view: 'review', kind: w.kind as ReviewKind })}>
      <span className="ico"><w.icon /></span>
      <span className="grow col" style={{ gap: 0, alignItems: 'flex-start' }}>
        <span className="n"><b className="tnum">{fmtNum(w.n)}</b> {w.n === 1 ? w.one : w.many}</span>
        <span className="k truncate">{w.hint}</span>
      </span>
      <ArrowRight className="go" />
    </button>
  )
}

function Waiting({ judgement, tidy, s, empty }: { judgement: WaitRow[]; tidy: WaitRow[]; s: Summary; empty: boolean }) {
  const [openTidy, setOpenTidy] = useState(false)
  if (empty) return null
  const tidyTotal = s.tidy_up ?? tidy.reduce((a, w) => a + w.n, 0)
  return (
    <section className="card home-panel">
      <h3>
        Needs your judgement{s.judgement ? <span className="home-count tnum">{fmtNum(s.judgement)}</span> : null}
      </h3>
      {judgement.length === 0 ? (
        <div className="home-clear">
          <Check />
          <span>Nothing needs your judgement right now.{s.totals.documents > 0 && !tidyTotal ? ' New documents may add items.' : ''}</span>
        </div>
      ) : (
        <div className="home-waiting">
          {judgement.map((w) => <WaitButton key={w.kind} w={w} />)}
        </div>
      )}
      {tidyTotal > 0 && (
        <>
          <button className="home-tidy" aria-expanded={openTidy} onClick={() => setOpenTidy(!openTidy)}>
            {openTidy ? <ChevronDown /> : <ChevronRight />}
            <span className="grow">Tidy-up</span>
            <b className="tnum">{fmtNum(tidyTotal)}</b>
          </button>
          {openTidy && <div className="home-waiting">{tidy.map((w) => <WaitButton key={w.kind} w={w} />)}</div>}
        </>
      )}
    </section>
  )
}

// ── In progress ──────────────────────────────────────────────────────────────
function InProgress({ s, failedDocs }: { s: Summary; failedDocs: { sha: string; filename: string; reason: string | null }[] }) {
  const [showFailed, setShowFailed] = useState(false)
  const rows: ReactNode[] = []
  const awaiting = s.awaiting_dig + s.awaiting_bark
  const add = () => useApp.getState().openAdd()
  const engine = useEngineGate()
  const row = (key: string, icon: ReactNode, text: ReactNode, action?: ReactNode, tone?: 'danger') => (
    <div className={'home-prog' + (tone ? ' ' + tone : '')} key={key}>
      <span className="ico">{icon}</span>
      <span className="grow">{text}</span>
      {action}
    </div>
  )
  if (s.incoming) rows.push(row('inc', <Inbox />, <><b className="tnum">{s.incoming}</b> {s.incoming === 1 ? 'file' : 'files'} in incoming</>, <Button size="sm" disabled={!engine.ready} onClick={add}>Add</Button>))
  if (awaiting) rows.push(row('await', <Hourglass />, <><b className="tnum">{awaiting}</b> {awaiting === 1 ? 'document' : 'documents'} not yet finished</>, <Button size="sm" disabled={!engine.ready} onClick={add}>Continue</Button>))
  if (s.pending_finalize && !awaiting) rows.push(row('batch', <Hourglass />, <>A batch is waiting to be finished</>, <Button size="sm" disabled={!engine.ready} onClick={add}>Finish</Button>))
  if (s.failed)
    rows.push(
      row(
        'fail',
        <TriangleAlert />,
        <>
          <b className="tnum">{s.failed}</b> {s.failed === 1 ? 'document' : 'documents'} failed{' '}
          {failedDocs.length > 0 && (
            <button className="linklike" onClick={() => setShowFailed(!showFailed)}>{showFailed ? 'hide' : 'details'}</button>
          )}
        </>,
        <Button size="sm" icon={RefreshCw} disabled={!engine.ready} onClick={() => window.dispatchEvent(new CustomEvent('wd:add', { detail: { retry: true } }))}>Retry</Button>,
        'danger'
      )
    )
  if (s.research_urls)
    rows.push(
      row(
        'res',
        <Link2 />,
        <><b className="tnum">{s.research_urls}</b> research {s.research_urls === 1 ? 'link' : 'links'} not downloaded</>,
        <Button
          size="sm"
          icon={Download}
          onClick={() =>
            void startJob('research-fetch', {}, 'Downloading research links').catch((e) => toast({ kind: 'error', title: 'Could not start the download', body: errorMessage(e) }))
          }
        >
          Download
        </Button>
      )
    )
  if (s.context_unseeded)
    rows.push(row('ctx', <FileSearch />, <>Background folder not yet read</>, <Button size="sm" onClick={() => navigate({ view: 'ask', mode: 'context' })}>Read it</Button>))

  if (!rows.length) return null
  return (
    <section className="card home-panel">
      <h3>In progress</h3>
      {engine.reason && <p className="faint" style={{ margin: "0 0 8px", fontSize: "var(--fs-sm)" }}>{engine.reason}</p>}
      <div className="home-progs">{rows}</div>
      {showFailed && (
        <ul className="home-failed">
          {failedDocs.slice(0, 8).map((f) => (
            <li key={f.sha}>
              <span className="truncate">{f.filename}</span>
              {f.reason && <span className="faint clamp-2">{f.reason}</span>}
            </li>
          ))}
          {failedDocs.length > 8 && <li className="faint">and {failedDocs.length - 8} more</li>}
        </ul>
      )}
    </section>
  )
}

function HomeSkeleton() {
  return (
    <>
      <div className="home-stats">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} h={82} style={{ borderRadius: 13 }} />
        ))}
      </div>
      <div className="home-cols">
        <div className="home-main">
          <Skeleton h={300} style={{ borderRadius: 13 }} />
          <Skeleton h={180} style={{ borderRadius: 13 }} />
        </div>
        <aside className="home-side">
          <Skeleton h={200} style={{ borderRadius: 13 }} />
          <Skeleton h={160} style={{ borderRadius: 13 }} />
        </aside>
      </div>
    </>
  )
}
