import { AlertTriangle, ArrowLeft, ArrowRight, BookOpen, Check, CalendarClock, Clock, ExternalLink, FileText, GitMerge, Link2, ListChecks, MessageSquare, MoreHorizontal, Network, NotebookPen, PencilLine, RefreshCw, ScanSearch, Sparkles, Undo2 } from 'lucide-react'
import { CSSProperties, useEffect, useMemo, useRef, useState } from 'react'
import { DocThumb } from '@renderer/components/DocThumb'
import { EntityAvatar, EntityChip } from '@renderer/components/EntityChip'
import { DisputedBadge } from '@renderer/components/FactCheck'
import { HistoryButton } from '@renderer/components/FileHistory'
import { Markdown } from '@renderer/components/Markdown'
import { Badge, Button, Callout, Dropdown, Empty, ErrorNote, Skeleton } from '@renderer/components/ui'
import type { EntityDetail, Relationship, TimelineEvent } from '@shared/api'
import { typeMeta } from '@renderer/lib/entityTypes'
import { fmtDate, fmtNum, plural } from '@renderer/lib/format'
import { call, errorMessage, invalidate, useRpc } from '@renderer/lib/rpc'
import { navigate, toast, useApp, useVault } from '@renderer/lib/store'
import ContradictionModal from './ContradictionModal'
import { EntityFacts } from './EntityFacts'
import MergeModal from './MergeModal'
import RecheckModal from './RecheckModal'
import './entities.css'

export default function EntityView() {
  const vault = useVault()
  const route = useApp((s) => s.route)
  const id = route.view === 'entity' ? route.id : ''
  const q = useRpc('vault.entity', id ? { vault, id } : null)
  const [mergeOpen, setMergeOpen] = useState(false)
  const [contraOpen, setContraOpen] = useState(false)
  const [recheckOpen, setRecheckOpen] = useState(false)

  if (q.isLoading) return <PageSkeleton />
  if (q.isError || !q.data)
    return (
      <div className="page">
        <div className="page-inner">
          <BackLink />
          <ErrorNote error={q.error ?? 'This entity could not be loaded.'} retry={() => void q.refetch()} />
        </div>
      </div>
    )
  const e = q.data
  const m = typeMeta(e.type)
  const mergedInto = typeof e.frontmatter?.merged_into === 'string' ? (e.frontmatter.merged_into as string) : null
  const openContra = e.contradictions.filter((c) => !c.resolved).length

  return (
    <div className="page">
      <div className="page-inner wide" style={{ '--chip-color': m.color } as CSSProperties}>
        <BackLink />

        {mergedInto && <StubNotice id={mergedInto} />}

        <header className="ent-hero">
          <EntityAvatar type={e.type} size={76} />
          <div style={{ minWidth: 0 }}>
            <div className="ent-hero-top">
              <span className="ent-type-tag">
                <m.icon size={13} />
                {m.label}
              </span>
              {openContra > 0 && (
                <Badge tone="danger" icon={AlertTriangle}>
                  {plural(openContra, 'open contradiction')}
                </Badge>
              )}
              {mergedInto && <Badge icon={GitMerge}>Merged</Badge>}
            </div>
            <h1 className="selectable">{e.name}</h1>
            {e.aliases.filter((a) => a.toLowerCase() !== e.name.toLowerCase()).length > 0 && (
              <div className="ent-aliases">
                <span className="lead">Also known as</span>
                {e.aliases
                  .filter((a) => a.toLowerCase() !== e.name.toLowerCase())
                  .map((a) => (
                    <span key={a} className="ent-alias">
                      {a}
                    </span>
                  ))}
              </div>
            )}
            <div className="ent-facts">
              <Fact v={fmtNum(e.doc_count)} l={e.doc_count === 1 ? 'document' : 'documents'} />
              <Fact v={fmtNum(e.facts.length)} l={e.facts.length === 1 ? 'fact' : 'facts'} />
              <Fact v={fmtNum(e.role_count)} l={e.role_count === 1 ? 'relationship' : 'relationships'} />
              <Fact v={fmtNum(e.timeline.length)} l={e.timeline.length === 1 ? 'timeline event' : 'timeline events'} />
              <Fact v={fmtDate(e.first_seen) || '—'} l="First seen" small />
              <Fact v={fmtDate(e.last_updated) || '—'} l="Last updated" small />
            </div>
            <div className="ent-actions">
              <Button variant="primary" icon={MessageSquare} onClick={() => navigate({ view: 'ask', prompt: `/watchdog-query Tell me about ${e.name}` })}>
                Ask Claude about this
              </Button>
              <Button icon={Network} onClick={() => navigate({ view: 'graph', focus: e.id })}>
                Show in network
              </Button>
              <Button icon={CalendarClock} onClick={() => navigate({ view: 'timeline', entity: e.id })}>
                Timeline
              </Button>
              <HistoryButton vault={vault} path={e.note} title={e.name} size="md" />
              <Dropdown
                align="left"
                trigger={(open) => <Button variant="ghost" icon={MoreHorizontal} tip="More actions" onClick={open} />}
                items={[
                  { label: 'Refresh summary from all sources', icon: RefreshCw, onClick: () => navigate({ view: 'ask', prompt: `/watchdog-entity ${e.id}` }) },
                  { label: 'Open in Obsidian', icon: ExternalLink, onClick: () => void openObsidian(vault, e.note) },
                  { separator: true, label: '' },
                  { label: 'Record a contradiction…', icon: AlertTriangle, onClick: () => setContraOpen(true) },
                  { label: 'Re-check contradictions…', icon: ScanSearch, onClick: () => setRecheckOpen(true), disabled: !!mergedInto },
                  { label: 'Merge into another entity…', icon: GitMerge, danger: true, onClick: () => setMergeOpen(true), disabled: !!mergedInto }
                ]}
              />
            </div>
          </div>
        </header>

        <div className="ent-cols">
          <div className="ent-main-col">
            <SummarySection e={e} />

            <section className="ent-sec">
              <SecTitle icon={ListChecks} title="Facts" count={e.facts.length} />
              <EntityFacts facts={e.facts} />
              {e.legacy_claims && (
                <div className="ent-legacy">
                  <div className="ent-legacy-head">Earlier claims</div>
                  <p className="faint">Recorded by an earlier version of Watchdog from documents whose extraction was not kept, so they cannot be shown as facts. Kept as written.</p>
                  <Markdown text={e.legacy_claims} compact />
                </div>
              )}
            </section>

            <Contradictions e={e} onAdd={() => setContraOpen(true)} onRecheck={mergedInto ? undefined : () => setRecheckOpen(true)} />

            <section className="ent-sec">
              <SecTitle icon={Clock} title="Timeline" count={e.timeline.length}>
                {e.timeline.length > 0 && (
                  <Button variant="ghost" size="sm" iconRight={ArrowRight} onClick={() => navigate({ view: 'timeline', entity: e.id })}>
                    Full timeline
                  </Button>
                )}
              </SecTitle>
              {e.timeline.length === 0 ? <div className="ent-empty-line">No dated events have been recorded for this entity.</div> : <MiniTimeline events={e.timeline} />}
            </section>

            <NotesSection e={e} />
          </div>

          <aside className="ent-side-col">
            <Relationships rels={e.relationships} />
            <section>
              <SecTitle icon={FileText} title="Appears in" count={e.documents.length} />
              {e.documents.length === 0 ? (
                <div className="ent-empty-line">No documents are linked to this entity.</div>
              ) : (
                <div className="ent-docs">
                  {e.documents.map((d) => (
                    <button key={d.sha} className="ent-doc" onClick={() => navigate({ view: 'document', sha: d.sha })} title={d.title || d.filename}>
                      <DocThumb sha={d.sha} ext={d.ext} original={d.original} title={d.title || d.filename} summary={d.summary} width={140} />
                      <span className="t">{d.title || d.filename}</span>
                    </button>
                  ))}
                </div>
              )}
            </section>
          </aside>
        </div>
      </div>

      <MergeModal open={mergeOpen} onClose={() => setMergeOpen(false)} initialMerge={e.id} />
      <RecheckModal open={recheckOpen} onClose={() => setRecheckOpen(false)} ids={[e.id]} name={e.name} />
      <ContradictionModal open={contraOpen} onClose={() => setContraOpen(false)} entityId={e.id} entityName={e.name} entityDocs={e.documents} />
    </div>
  )
}

async function openObsidian(vault: string, note: string | null) {
  const ok = await window.watchdog.shell.openInObsidian(vault, note ?? undefined)
  if (!ok) toast({ kind: 'info', title: 'Obsidian is not installed', body: 'Install Obsidian to open the vault as notes, or use the views here.' })
}

function BackLink() {
  return (
    <div style={{ marginBottom: 14 }}>
      <Button variant="ghost" size="sm" icon={ArrowLeft} onClick={() => navigate({ view: 'entities' })}>
        All entities
      </Button>
    </div>
  )
}

function Fact({ v, l, small }: { v: string; l: string; small?: boolean }) {
  return (
    <div className="ent-fact">
      <span className={small ? 'v sm' : 'v'}>{v}</span>
      <span className="l">{l}</span>
    </div>
  )
}

function SecTitle({ icon: Icon, title, count, children }: { icon: typeof FileText; title: string; count?: number; children?: React.ReactNode }) {
  return (
    <h2 className="ent-sec-title">
      <Icon />
      {title}
      {count !== undefined && count > 0 && <span className="count">{fmtNum(count)}</span>}
      <span className="grow" />
      {children}
    </h2>
  )
}

function StubNotice({ id }: { id: string }) {
  const vault = useVault()
  const target = useRpc('vault.entity', { vault, id })
  return (
    <Callout
      tone="warning"
      title="This entity was merged into another"
      style={{ marginBottom: 18 }}
      action={
        <Button size="sm" iconRight={ArrowRight} onClick={() => navigate({ view: 'entity', id })}>
          Open {target.data?.name ?? 'the survivor'}
        </Button>
      }
    >
      This note is now a redirect. Its aliases, documents, roles and timeline were folded into the surviving entity, which holds the full record.
    </Callout>
  )
}

// ── the summary ──────────────────────────────────────────────────────────────
// Shown as written, with its citations as links to the facts (D283). Who wrote it, when and from
// how many facts stays in the registry, not on the page (D284).
function SummarySection({ e }: { e: EntityDetail }) {
  const summary = e.synthesis?.summary_md ?? e.synthesis?.summary ?? e.sections.summary
  const analysis = e.synthesis ? e.synthesis.analysis_md ?? e.synthesis.analysis : null
  return (
    <section className="ent-sec">
      <SecTitle icon={BookOpen} title="Summary">
        <Button variant="ghost" size="sm" icon={Sparkles} onClick={() => navigate({ view: 'ask', prompt: `/watchdog-entity ${e.id}` })} tip="Re-write the summary from every source in a Claude session">
          Refresh from all sources
        </Button>
      </SecTitle>
      {summary?.trim() ? (
        <div className="ent-ai">
          {e.synthesis?.stale_notice && (
            <Callout tone="warning" title="Out of date" style={{ marginBottom: 12 }}>
              {e.synthesis.stale_notice}
            </Callout>
          )}
          <Markdown text={summary} />
          {analysis?.trim() && <Markdown text={analysis} />}
        </div>
      ) : (
        <Empty icon={BookOpen} title="No summary yet">
          A summary is added once the entity appears in two or more documents. Its facts below are the record either way.
        </Empty>
      )}
    </section>
  )
}

// ── contradictions ───────────────────────────────────────────────────────────
function parseCallout(text: string): { label: string; sides: { value: string; cite: string }[] } | null {
  const lines = text.split('\n').map((l) => l.replace(/^>\s?/, '').trim()).filter(Boolean)
  const head = /^\[!contradiction\]\s*(.*)$/i.exec(lines[0] ?? '')
  const sides = lines
    .slice(head ? 1 : 0)
    .map((l) => /^[-*]\s*\*\*(.+?)\*\*\s*(?:[—–-]\s*)?(.*)$/.exec(l))
    .filter((x): x is RegExpExecArray => !!x)
    .map((x) => ({ value: x[1], cite: x[2] }))
  if (sides.length < 2) return null
  return { label: head?.[1] ?? '', sides }
}

function Contradictions({ e, onAdd, onRecheck }: { e: EntityDetail; onAdd: () => void; onRecheck?: () => void }) {
  const vault = useVault()
  const [busy, setBusy] = useState<string | null>(null)
  const list = e.contradictions
  const open = list.filter((c) => !c.resolved).length
  const toggle = async (rid: string, resolved: boolean) => {
    setBusy(rid)
    try {
      await call(resolved ? 'review.unresolve' : 'review.resolve', { vault, rids: [rid] })
      invalidate('vault.', 'review.')
    } catch (err) {
      toast({ kind: 'error', title: 'Could not update', body: errorMessage(err) })
    } finally {
      setBusy(null)
    }
  }
  return (
    <section className="ent-sec">
      <SecTitle icon={AlertTriangle} title="Contradictions" count={list.length}>
        {onRecheck && (
          <Button variant="ghost" size="sm" icon={ScanSearch} onClick={onRecheck} tip="Ask the AI model to compare every recorded fact about this entity with every other">
            Re-check
          </Button>
        )}
        <Button variant="ghost" size="sm" icon={PencilLine} onClick={onAdd}>
          Record one
        </Button>
      </SecTitle>
      {list.length === 0 ? (
        <div className="ent-empty-line">No contradictions are flagged. If you find sources that disagree, record the conflict so it is tracked here.</div>
      ) : (
        <>
          {open === 0 && <div className="ent-empty-line" style={{ marginBottom: 10 }}>Every contradiction here is marked handled.</div>}
          {list.map((c) => {
            const p = parseCallout(c.text)
            return (
              <article key={c.rid} className="ent-contra" data-resolved={c.resolved}>
                <div className="ent-contra-head">
                  <div className="ent-contra-label selectable">{p?.label || c.summary}</div>
                  {c.resolved && <Badge icon={Check}>Handled</Badge>}
                  <Button size="sm" variant={c.resolved ? 'ghost' : 'default'} icon={c.resolved ? Undo2 : Check} loading={busy === c.rid} onClick={() => void toggle(c.rid, c.resolved)}>
                    {c.resolved ? 'Reopen' : 'Mark handled'}
                  </Button>
                </div>
                {p ? (
                  <div className="ent-contra-sides">
                    {p.sides.slice(0, 2).map((s, i) => (
                      <div key={i} className="ent-contra-side">
                        <div className="tag">{i === 0 ? 'One source says' : 'Another says'}</div>
                        <div className="val selectable">{s.value}</div>
                        {s.cite && (
                          <div className="cite">
                            <Markdown text={s.cite} compact />
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div style={{ marginTop: 8 }}>
                    <Markdown text={c.text.replace(/^>\s?/gm, '').replace(/^\[!contradiction\][^\n]*\n?/i, '')} compact />
                  </div>
                )}
              </article>
            )
          })}
        </>
      )}
    </section>
  )
}

// ── timeline ─────────────────────────────────────────────────────────────────
function MiniTimeline({ events }: { events: TimelineEvent[] }) {
  const [all, setAll] = useState(false)
  const shown = all ? events : events.slice(0, 12)
  return (
    <>
      <ol className="ent-tl">
        {shown.map((ev, i) => (
          <li key={i}>
            <div className="when">{fmtDate(ev.date)}</div>
            <div className="what selectable">{ev.text}{ev.disputed && <> <DisputedBadge /></>}</div>
            {ev.sha && ev.filename && (
              <div className="src">
                <button className="ent-src" onClick={() => navigate({ view: 'document', sha: ev.sha!, page: ev.page ?? undefined })}>
                  <FileText />
                  <span className="truncate">
                    {ev.filename}
                    {ev.page ? ` · p. ${ev.page}` : ''}
                  </span>
                </button>
              </div>
            )}
          </li>
        ))}
      </ol>
      {events.length > 12 && !all && (
        <Button size="sm" variant="ghost" onClick={() => setAll(true)}>
          Show all {events.length} events
        </Button>
      )}
    </>
  )
}

// ── notes ────────────────────────────────────────────────────────────────────
function NotesSection({ e }: { e: EntityDetail }) {
  const vault = useVault()
  const initial = useMemo(() => {
    const raw = e.sections.notes ?? ''
    return /^\s*(<!--[\s\S]*?-->\s*)*$/.test(raw) ? '' : raw.trim()
  }, [e.sections.notes])
  const [text, setText] = useState(initial)
  const [state, setState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const saved = useRef(initial)
  useEffect(() => {
    setText(initial)
    saved.current = initial
    setState('idle')
  }, [initial, e.id])

  const save = async () => {
    if (text === saved.current || !e.note) return
    setState('saving')
    try {
      await call('vault.saveNotes', { vault, path: e.note, text })
      saved.current = text
      setState('saved')
      invalidate('vault.entity')
    } catch (err) {
      setState('error')
      toast({ kind: 'error', title: 'Notes not saved', body: errorMessage(err) })
    }
  }
  return (
    <section className="ent-sec">
      <SecTitle icon={NotebookPen} title="Your notes" />
      <textarea className="ent-notes" value={text} placeholder="Add your own observations, questions and follow-ups here." onChange={(ev) => { setText(ev.target.value); setState('idle') }} onBlur={() => void save()} aria-label="Notes" />
      <div className="ent-notes-foot">
        {state === 'saving' ? <span>Saving…</span> : state === 'saved' ? <span style={{ color: 'var(--success)' }}>Saved</span> : state === 'error' ? <span style={{ color: 'var(--danger)' }}>Not saved</span> : null}
        <span>This section is yours. Watchdog never overwrites it, even when the entity is re-synthesized.</span>
      </div>
    </section>
  )
}

// ── relationships ────────────────────────────────────────────────────────────
function Relationships({ rels }: { rels: Relationship[] }) {
  const groups = useMemo(() => {
    const g = new Map<string, { role: string; dir: 'out' | 'in'; items: Map<string, Relationship> }>()
    for (const r of rels) {
      const key = `${r.direction}|${r.role}`
      if (!g.has(key)) g.set(key, { role: r.role || 'related to', dir: r.direction, items: new Map() })
      const items = g.get(key)!.items
      if (!items.has(r.target_id)) items.set(r.target_id, r)
    }
    return [...g.values()].sort((a, b) => (a.dir === b.dir ? b.items.size - a.items.size : a.dir === 'out' ? -1 : 1))
  }, [rels])
  return (
    <section>
      <SecTitle icon={Link2} title="Relationships" count={rels.length} />
      {groups.length === 0 ? (
        <div className="ent-empty-line">No relationships have been recorded for this entity.</div>
      ) : (
        <div className="card card-pad">
          {groups.map((g) => (
            <div key={`${g.dir}|${g.role}`} className="ent-rel-group">
              <div className="ent-rel-role">
                {g.dir === 'out' ? <ArrowRight /> : <ArrowLeft />}
                {g.role}
                {g.dir === 'in' && <span style={{ textTransform: 'none', letterSpacing: 0, fontWeight: 500 }}>(incoming)</span>}
              </div>
              <div className="ent-rel-chips">
                {[...g.items.values()].map((r) =>
                  r.target_name && r.target_type ? (
                    <EntityChip key={r.target_id} id={r.target_id} name={r.target_name} type={r.target_type} />
                  ) : (
                    <span key={r.target_id} className="ent-plain-chip" title="Mentioned but not profiled as its own entity">
                      {r.target_name || r.target_id}
                    </span>
                  )
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

function PageSkeleton() {
  return (
    <div className="page">
      <div className="page-inner wide">
        <Skeleton w={110} h={26} style={{ marginBottom: 14 }} />
        <div className="ent-hero" style={{ '--chip-color': 'var(--text-3)' } as CSSProperties}>
          <Skeleton w={76} h={76} style={{ borderRadius: 22 }} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Skeleton w={90} h={12} />
            <Skeleton w="45%" h={30} />
            <Skeleton w="30%" h={14} />
            <Skeleton w="60%" h={36} />
          </div>
        </div>
        <div className="ent-cols">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <Skeleton w={120} h={18} />
            {[100, 96, 92, 70].map((w, i) => (
              <Skeleton key={i} w={`${w}%`} h={14} />
            ))}
          </div>
          <Skeleton h={220} />
        </div>
      </div>
    </div>
  )
}

