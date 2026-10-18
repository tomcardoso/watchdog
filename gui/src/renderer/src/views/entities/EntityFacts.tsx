// An entity's facts (D280): every fact tagged to it in the stored extractions, in date order or
// grouped by document, each with its source page, warnings, the reporter's own check and, on
// demand, the source passage. Keyboard as in the document reader: J/K move, V/D/C mark, N notes.

import { AlertTriangle, ChevronDown, ChevronRight, FileText, Info, ListChecks, Quote } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { DisputedBadge, FactCheck, handleMarkKey, useMarks } from '@renderer/components/FactCheck'
import { Badge, Button, Empty, Kbd, Segmented, cx } from '@renderer/components/ui'
import { fmtDate, fmtNum } from '@renderer/lib/format'
import { navigate } from '@renderer/lib/store'
import type { EntityFact } from '@shared/api'
import { Passage } from '../documents/FactsTab'
import '../documents/documents.css'

type Mode = 'all' | 'unchecked' | 'disputed' | 'inferred' | 'figures'
type Order = 'date' | 'document'
const PAGE = 30

function docLabel(f: EntityFact) {
  return f.title || 'Untitled document'
}

export function EntityFacts({ facts }: { facts: EntityFact[] }) {
  const [mode, setMode] = useState<Mode>('all')
  const [order, setOrder] = useState<Order>('date')
  const [all, setAll] = useState(false)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [noteFor, setNoteFor] = useState<string | null>(null)
  const marks = useMarks()
  const list = useRef<HTMLDivElement>(null)
  const markOf = (f: EntityFact) => marks.get(f.id, f.mark)

  const counts = {
    unchecked: facts.filter((f) => !markOf(f)).length,
    disputed: facts.filter((f) => markOf(f)?.status === 'disputed').length,
    inferred: facts.filter((f) => f.basis === 'inferred').length,
    figures: facts.filter((f) => f.figure_note || f.quote_note).length
  }
  const shown = facts.filter((f) =>
    mode === 'all' ? true : mode === 'unchecked' ? !markOf(f) : mode === 'disputed' ? markOf(f)?.status === 'disputed' : mode === 'inferred' ? f.basis === 'inferred' : !!(f.figure_note || f.quote_note)
  )
  const groups = useMemo(() => {
    if (order === 'date') return null
    const g = new Map<string, EntityFact[]>()
    for (const f of shown) {
      if (!g.has(f.sha)) g.set(f.sha, [])
      g.get(f.sha)!.push(f)
    }
    return [...g.entries()]
  }, [order, shown])

  if (!facts.length)
    return (
      <Empty icon={ListChecks} title="No facts yet">
        No fact in this investigation's documents is tagged to this entity. It may only appear in relationships.
      </Empty>
    )

  const limited = all ? shown : shown.slice(0, PAGE)
  const move = (from: HTMLElement, d: number) => {
    const rows = Array.from(list.current?.querySelectorAll<HTMLElement>('[data-fact-id]') ?? [])
    const i = rows.indexOf(from)
    rows[Math.min(rows.length - 1, Math.max(0, i + d))]?.focus()
  }

  const row = (f: EntityFact, withSource: boolean) => {
    const mark = markOf(f)
    const jump = (t: { page: number }) => navigate({ view: 'document', sha: f.sha, page: t.page })
    const hasPassage = !!(f.quote || f.passage_method)
    return (
      <article
        className={cx('fact', 'ent-fact-row', f.figure_note && 'warn', mark && `is-marked-${mark.status}`)}
        key={f.id}
        data-fact-id={f.id}
        tabIndex={0}
        aria-label={`Fact from ${docLabel(f)}${f.page ? `, page ${f.page}` : ''}${mark ? `, marked ${mark.status}` : ''}`}
        onKeyDown={(e) => {
          if (e.target !== e.currentTarget) return
          const k = e.key.toLowerCase()
          if (k === 'j' || e.key === 'ArrowDown') {
            e.preventDefault()
            move(e.currentTarget, 1)
          } else if (k === 'k' || e.key === 'ArrowUp') {
            e.preventDefault()
            move(e.currentTarget, -1)
          } else if (handleMarkKey(e, f.id, mark, marks, () => setNoteFor(f.id))) {
            e.preventDefault()
          }
        }}
      >
        <div className="fact-top">
          {f.date && <span className="fact-date">{fmtDate(f.date)}</span>}
          <button className="fact-page ent-fact-src" onClick={() => navigate({ view: 'document', sha: f.sha, page: f.page ?? undefined })} data-tip={`Open ${docLabel(f)}${f.page ? ` at page ${f.page}` : ''}`}>
            <FileText />
            <span className="truncate">{withSource ? docLabel(f) : 'Open'}{f.page ? ` · p. ${f.page}` : ''}</span>
          </button>
          {f.basis === 'inferred' && <Badge tone="info" icon={Info} tip="Reasoned from the document, not stated in it. Verify before relying on it.">inferred</Badge>}
          {f.figure_note && <Badge tone="warning" icon={AlertTriangle} tip={f.figure_note}>check figure</Badge>}
          {mark?.status === 'disputed' && <DisputedBadge />}
        </div>
        <div className="fact-text selectable">{f.fact}</div>
        {hasPassage && (
          <button className="ent-fact-toggle" onClick={() => setOpen((o) => ({ ...o, [f.id]: !o[f.id] }))} aria-expanded={!!open[f.id]}>
            {open[f.id] ? <ChevronDown /> : <ChevronRight />}
            <Quote />
            {open[f.id] ? 'Hide the source passage' : f.quote ? 'Show the quotation' : f.passage_method === 'matched' ? 'Show the matched passage' : 'Source passage'}
          </button>
        )}
        {hasPassage && open[f.id] && <Passage f={f} jump={jump} hasViewer />}
        <FactCheck id={f.id} mark={mark} marks={marks} noteOpen={noteFor === f.id} setNoteOpen={(v) => setNoteFor(v ? f.id : null)} />
      </article>
    )
  }

  return (
    <>
      <div className="facts-bar">
        <Segmented
          value={mode}
          onChange={(v) => {
            setMode(v)
            setAll(false)
          }}
          options={[
            { value: 'all', label: `All ${fmtNum(facts.length)}` },
            { value: 'unchecked', label: `Not checked ${fmtNum(counts.unchecked)}`, tip: 'Facts you have not yet marked Verified, Disputed or Can’t verify' },
            ...(counts.disputed ? [{ value: 'disputed' as Mode, label: `Disputed ${fmtNum(counts.disputed)}` }] : []),
            ...(counts.inferred ? [{ value: 'inferred' as Mode, label: `Inferred ${fmtNum(counts.inferred)}` }] : []),
            ...(counts.figures ? [{ value: 'figures' as Mode, label: `Figures ${fmtNum(counts.figures)}`, tip: 'Facts with a figure or quotation Watchdog could not confirm on the page' }] : [])
          ]}
        />
        <Segmented
          value={order}
          onChange={setOrder}
          options={[
            { value: 'date', label: 'By date', tip: 'In the order things happened: by the fact’s date, else its document’s date' },
            { value: 'document', label: 'By document' }
          ]}
        />
        <span className="faint" style={{ fontSize: 'var(--fs-xs)' }} data-tip="Select a fact, then press V, D or C to mark it, N for a note, J and K to move">
          <Kbd>V</Kbd> <Kbd>D</Kbd> <Kbd>C</Kbd>
        </span>
      </div>
      {shown.length === 0 && <div className="ent-empty-line">No facts in this group.</div>}
      <div ref={list}>
        {groups
          ? groups.map(([sha, fs]) => (
              <div key={sha} className="ent-fact-group">
                <div className="ent-fact-group-head">
                  <FileText />
                  <button className="ent-src" onClick={() => navigate({ view: 'document', sha })}>
                    <span className="truncate">{docLabel(fs[0])}</span>
                  </button>
                  {fs[0].doc_date && <span className="faint">{fmtDate(fs[0].doc_date)}</span>}
                  <span className="count">{fmtNum(fs.length)}</span>
                </div>
                {fs.map((f) => row(f, false))}
              </div>
            ))
          : limited.map((f) => row(f, true))}
      </div>
      {!groups && shown.length > PAGE && !all && (
        <Button size="sm" variant="ghost" onClick={() => setAll(true)}>
          Show all {fmtNum(shown.length)} facts
        </Button>
      )}
    </>
  )
}
