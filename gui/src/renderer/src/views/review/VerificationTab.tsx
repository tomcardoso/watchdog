// Review → Verification: every fact in the investigation and the reporter's check of it (D271).
// Progress at the top, a filter by status, and the same per-fact control as a document's Facts
// tab. Keyboard: J/K to move, V/D/C to mark the focused fact, N for a note, O to open its page.

import { ArrowUpRight, FileText, History, SearchX, ShieldCheck } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import type { LedgerFact } from '@shared/api'
import { FactCheck, MarkSummary, STATUS_META, handleMarkKey, useMarks } from '@renderer/components/FactCheck'
import { Callout, Empty, ErrorNote, Kbd, Progress, Segmented, Skeleton, cx } from '@renderer/components/ui'
import { fmtNum, plural } from '@renderer/lib/format'
import { useRpc } from '@renderer/lib/rpc'
import { navigate, useApp, useVault } from '@renderer/lib/store'

type Filter = 'unchecked' | 'disputed' | 'unverifiable' | 'verified' | 'unlocated' | 'changed'
const FILTERS: Filter[] = ['unchecked', 'disputed', 'unverifiable', 'verified', 'unlocated', 'changed']
const LIMIT = 300

export function VerificationTab() {
  const vault = useVault()
  const route = useApp((s) => s.route)
  const q = useRpc('verify.facts', vault ? { vault } : null, { staleTime: 5_000 })
  const base = useMarks()
  // A fact marked while a filter is showing stays in the list until the filter changes, so the
  // reporter can see what they did and undo it.
  const [stay, setStay] = useState<Set<string>>(new Set())
  const marks = useMemo(
    () => ({ ...base, set: (...a: Parameters<typeof base.set>) => { setStay((s) => new Set(s).add(a[0])); return base.set(...a) } }),
    [base]
  )
  const [noteFor, setNoteFor] = useState<string | null>(null)
  const list = useRef<HTMLDivElement>(null)
  const routeFilter = route.view === 'review' && FILTERS.includes(route.filter as Filter) ? (route.filter as Filter) : null
  const [picked, setPicked] = useState<Filter | null>(routeFilter)

  const facts = q.data?.facts ?? []
  const markOf = (f: LedgerFact) => marks.get(f.id, f.mark)
  const counts = useMemo(() => {
    const c: Record<Filter, number> = { unchecked: 0, disputed: 0, unverifiable: 0, verified: 0, unlocated: 0, changed: q.data?.orphaned.length ?? 0 }
    for (const f of facts) {
      const m = marks.get(f.id, f.mark)
      c[m ? m.status : 'unchecked']++
      if (f.passage_method === 'unlocated') c.unlocated++
    }
    return c
  }, [facts, marks, q.data])
  const filter: Filter = picked ?? (counts.disputed ? 'disputed' : 'unchecked')

  if (q.error) return <ErrorNote error={q.error} retry={() => void q.refetch()} />
  if (q.isLoading || !q.data)
    return (
      <div className="col" style={{ gap: 10 }}>
        <Skeleton h={54} />
        <Skeleton h={90} />
        <Skeleton h={90} />
      </div>
    )

  const total = facts.length
  const shown = facts.filter((f) => {
    if (stay.has(f.id)) return true
    const m = markOf(f)
    if (filter === 'unlocated') return f.passage_method === 'unlocated'
    if (filter === 'unchecked') return !m
    return m?.status === filter
  })
  const move = (from: HTMLElement, d: number) => {
    const rows = Array.from(list.current?.querySelectorAll<HTMLElement>('[data-fact-id]') ?? [])
    rows[Math.min(rows.length - 1, Math.max(0, rows.indexOf(from) + d))]?.focus()
  }
  const open = (f: LedgerFact) => navigate({ view: 'document', sha: f.sha, page: f.passage_page ?? f.page ?? undefined, tab: 'facts' })
  const label = (k: Filter) =>
    k === 'unchecked' ? 'Not checked' : k === 'unlocated' ? 'No passage' : k === 'changed' ? 'Changed since marked' : STATUS_META[k].label

  return (
    <div className="rv-queue">
      <div className="rv-blurb">
        <ShieldCheck />
        <p>
          Your own check of each fact against its source. Mark a fact Verified once you have read the page and it holds, Disputed when the source or another record does not support it, and Can’t verify when you could not settle it. Your name and the time are recorded with each mark, and the list is kept in the investigation as <span className="mono">verification.md</span>.
        </p>
      </div>

      {q.data.summary.read_only && (
        <Callout tone="warning" style={{ marginBottom: 12 }}>
          This investigation’s checks were saved by a newer version of Watchdog. They are shown here, but can’t be changed until you update Watchdog.
        </Callout>
      )}

      <div className="vf-progress card">
        <div className="vf-progress-head">
          <span className="vf-progress-n">
            <b className="tnum">{fmtNum(counts.verified)}</b> of <b className="tnum">{fmtNum(total)}</b> facts verified
          </span>
          <span className="faint">
            {fmtNum(counts.disputed)} disputed · {fmtNum(counts.unverifiable)} can’t verify · {fmtNum(counts.unchecked)} not checked
          </span>
        </div>
        <Progress value={total ? (counts.verified + counts.disputed + counts.unverifiable) / total : 0} />
        <div className="faint" style={{ fontSize: 'var(--fs-xs)' }}>
          {total ? `${Math.round(((counts.verified + counts.disputed + counts.unverifiable) / total) * 100)}% checked. ` : ''}Recorded as {q.data.reporter}. Change the name in Settings.
        </div>
      </div>

      <div className="rv-toolbar" style={{ flexWrap: 'wrap' }}>
        <Segmented<Filter>
          value={filter}
          onChange={(v) => {
            setPicked(v)
            setStay(new Set())
            navigate({ view: 'review', kind: 'verification', filter: v }, { replace: true })
          }}
          options={FILTERS.filter((k) => k !== 'changed' || counts.changed > 0).map((k) => ({ value: k, label: `${label(k)} ${fmtNum(counts[k])}` }))}
        />
        <span className="spacer" />
        <span className="faint" style={{ fontSize: 'var(--fs-xs)' }}>
          <Kbd>J</Kbd> <Kbd>K</Kbd> move · <Kbd>V</Kbd> <Kbd>D</Kbd> <Kbd>C</Kbd> mark · <Kbd>N</Kbd> note · <Kbd>O</Kbd> open
        </span>
      </div>

      {filter === 'changed' ? (
        <div className="rv-list">
          <div className="rv-note faint">
            These marks were made on facts whose wording or page has changed since, usually because the document was processed again. They are kept as a record and are never moved to the new wording. Check the new fact and mark it again.
          </div>
          {q.data.orphaned.map((o) => (
            <article className="fact vf-row" key={o.id}>
              <div className="fact-top">
                <History style={{ width: 13, height: 13, color: 'var(--text-3)' }} />
                <span className="faint" style={{ fontSize: 'var(--fs-xs)' }}>{o.filename}{o.page ? `, p. ${o.page}` : ''}</span>
                <MarkSummary mark={o} />
              </div>
              <div className="fact-text selectable">{o.fact}</div>
              {o.note && <div className="fcheck-note selectable">{o.note}</div>}
            </article>
          ))}
        </div>
      ) : shown.length === 0 ? (
        <Empty icon={ShieldCheck} title={filter === 'unchecked' ? (total ? 'Every fact has been checked' : 'No facts yet') : `No facts marked ${label(filter).toLowerCase()}`}>
          {filter === 'unchecked' && total ? 'Disputed and can’t-verify facts stay listed under their own filters.' : filter === 'unlocated' ? 'Every fact has a quote or a matching passage on its page.' : 'Marks you make in a document’s Facts tab, or here, are listed under these filters.'}
        </Empty>
      ) : (
        <div className="rv-list" ref={list}>
          {shown.length > LIMIT && <div className="rv-note faint">Showing the first {fmtNum(LIMIT)} of {plural(shown.length, 'fact')}. Open a document to work through its facts in order.</div>}
          {shown.slice(0, LIMIT).map((f) => {
            const mark = markOf(f)
            return (
              <article
                key={f.id}
                className={cx('fact vf-row', mark && `is-marked-${mark.status}`)}
                data-fact-id={f.id}
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.target !== e.currentTarget) return
                  const k = e.key.toLowerCase()
                  if (k === 'j' || e.key === 'ArrowDown') {
                    e.preventDefault()
                    move(e.currentTarget, 1)
                  } else if (k === 'k' || e.key === 'ArrowUp') {
                    e.preventDefault()
                    move(e.currentTarget, -1)
                  } else if (k === 'o' || e.key === 'Enter') {
                    e.preventDefault()
                    open(f)
                  } else if (handleMarkKey(e, f.id, mark, marks, () => setNoteFor(f.id))) {
                    e.preventDefault()
                    if (k !== 'n') move(e.currentTarget, 1)   // on to the next fact, as in triage
                  }
                }}
              >
                <div className="fact-top">
                  <button className="fact-page" onClick={() => open(f)} data-tip="Open the document at this fact's page">
                    <FileText />
                    <span className="truncate" style={{ maxWidth: 320 }}>{f.title}</span>
                    {f.page ? <span>· p. {f.page}</span> : null}
                    <ArrowUpRight />
                  </button>
                  {f.basis === 'inferred' && <span className="faint" style={{ fontSize: 'var(--fs-xs)' }}>inferred</span>}
                  {f.passage_method === 'unlocated' && (
                    <span className="fact-unlocated">
                      <SearchX />
                      No matching passage found{f.page ? ` on p. ${f.page}` : ''}
                    </span>
                  )}
                </div>
                <div className="fact-text selectable">{f.fact}</div>
                <FactCheck id={f.id} mark={mark} marks={marks} noteOpen={noteFor === f.id} setNoteOpen={(v) => setNoteFor(v ? f.id : null)} />
              </article>
            )
          })}
        </div>
      )}
    </div>
  )
}
