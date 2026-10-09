// The document reader's Facts tab: each fact with its page, its source passage (D270) and the
// reporter's own check (D271). Keyboard: J/K or the arrow keys move between facts, V/D/C mark the
// focused fact Verified, Disputed or Can't verify (again to clear), N edits its note.

import { AlertTriangle, Clock, FileText, Info, SearchX } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { EntityChip } from '@renderer/components/EntityChip'
import { FactCheck, handleMarkKey, useMarks } from '@renderer/components/FactCheck'
import { Badge, Empty, Kbd, Segmented, cx } from '@renderer/components/ui'
import { fmtDate } from '@renderer/lib/format'
import { pageLabel } from '@renderer/lib/media'
import type { Fact, MediaInfo } from '@shared/api'
import type { JumpTarget } from './PdfViewer'

type Jump = (t: Omit<JumpTarget, 'nonce'>) => void
type Mode = 'all' | 'unchecked' | 'unlocated' | 'inferred' | 'figures'

const INFERRED_TIP = 'Reasoned from the document, not stated — verify before relying on it'

function figureExplain(note: string): string {
  if (/another page/i.test(note)) return 'The number is in the document, but not on the page this fact cites. The page link may be wrong.'
  if (/not found/i.test(note)) return 'The number appears nowhere in the source. It was probably calculated (a total, a difference) rather than read off the page. Check the arithmetic before using it.'
  return 'Watchdog could not confirm this figure on the page it cites. Check it against the source.'
}

/** The opening words of a passage, to find it on the rendered page. */
export function passageSnippet(q: string): string {
  const words = q.replace(/[“”"…]|\.\.\./g, ' ').replace(/\s+/g, ' ').trim().split(' ')
  return words.slice(0, 7).join(' ')
}

export function Passage({ f, jump, hasViewer, media }: { f: Fact; jump: Jump; hasViewer: boolean; media?: MediaInfo | null }) {
  const page = f.passage_page ?? f.page
  const findLabel = media ? 'play from here' : 'find on page'
  const on = (p: number) => (media ? `at ${pageLabel(media, p)}` : `on ${pageLabel(null, p)}`)
  if (f.quote) {
    return (
      <blockquote className="fact-quote">
        {f.quote}
        {page && hasViewer && (
          <button className="fact-quote-find" onClick={() => jump({ page, find: passageSnippet(f.quote!) })}>
            {findLabel}
          </button>
        )}
      </blockquote>
    )
  }
  if (f.passage_method === 'matched' && f.passage) {
    const elsewhere = f.passage_page && f.page && f.passage_page !== f.page
    return (
      <blockquote className="fact-passage">
        <span className="fact-passage-label" data-tip="Watchdog found this passage by matching the fact's names, figures and dates against the page. The model did not quote it, so read the page before relying on it.">
          Matched passage{f.passage_page ? `, ${pageLabel(media, f.passage_page)}` : ''}{elsewhere ? ` (the fact cites ${pageLabel(media, f.page!)})` : ''}
        </span>
        {f.passage}
        {page && hasViewer && (
          <button className="fact-quote-find" onClick={() => jump({ page, find: passageSnippet(f.passage!) })}>
            {findLabel}
          </button>
        )}
      </blockquote>
    )
  }
  if (f.passage_method === 'unlocated') {
    return (
      <div className="fact-unlocated" data-tip="Nothing on the page shares enough of this fact's names, figures and dates. It may be reasoned from several passages, or the page citation may be off. Check the source.">
        <SearchX />
        {f.page ? `No matching passage found ${on(f.page)}` : 'No matching passage found in this document'}
      </div>
    )
  }
  return null
}

export function FactsTab({ facts, jump, hasViewer, media, focusId }: { facts: Fact[]; jump: Jump; hasViewer: boolean; media?: MediaInfo | null; focusId?: string }) {
  const [mode, setMode] = useState<Mode>('all')
  // A cited fact (D283): scroll it into view and select it, once its row is on screen.
  useEffect(() => {
    if (!focusId) return
    const t = window.setTimeout(() => {
      const row = list.current?.querySelector<HTMLElement>(`[data-fact-id="${CSS.escape(focusId)}"]`)
      row?.scrollIntoView({ block: 'center' })
      row?.focus({ preventScroll: true })
    }, 60)
    return () => window.clearTimeout(t)
  }, [focusId, facts])
  const [noteFor, setNoteFor] = useState<string | null>(null)
  const marks = useMarks()
  const list = useRef<HTMLDivElement>(null)
  const markOf = (f: Fact) => marks.get(f.id, f.mark)
  const inferred = facts.filter((f) => f.basis === 'inferred').length
  const figures = facts.filter((f) => f.figure_note || f.quote_note).length
  const unchecked = facts.filter((f) => !markOf(f)).length
  const unlocated = facts.filter((f) => f.passage_method === 'unlocated').length
  const checked = facts.length - unchecked
  const shown = facts.filter((f) =>
    mode === 'all' ? true : mode === 'inferred' ? f.basis === 'inferred' : mode === 'figures' ? !!(f.figure_note || f.quote_note) : mode === 'unlocated' ? f.passage_method === 'unlocated' : !markOf(f)
  )
  if (!facts.length) return <Empty icon={FileText} title="No facts recorded">Nothing was extracted as a discrete fact from this document. The summary and full text are still available.</Empty>

  const move = (from: HTMLElement, d: number) => {
    const rows = Array.from(list.current?.querySelectorAll<HTMLElement>('[data-fact-id]') ?? [])
    const i = rows.indexOf(from)
    rows[Math.min(rows.length - 1, Math.max(0, i + d))]?.focus()
  }

  return (
    <>
      <div className="facts-bar">
        <Segmented
          value={mode}
          onChange={setMode}
          options={[
            { value: 'all', label: `All ${facts.length}` },
            { value: 'unchecked', label: `Not checked ${unchecked}`, tip: 'Facts you have not yet marked Verified, Disputed or Can’t verify' },
            ...(unlocated ? [{ value: 'unlocated' as Mode, label: `No passage ${unlocated}`, tip: 'Facts with no matching passage on the page they cite' }] : []),
            { value: 'inferred', label: `Inferred ${inferred}` },
            { value: 'figures', label: `Figures ${figures}`, tip: 'Facts with a figure or quote Watchdog could not confirm on the page' }
          ]}
        />
        <span className="faint" style={{ fontSize: 'var(--fs-xs)' }} data-tip="Select a fact, then press V, D or C to mark it, N for a note, J and K to move">
          {checked} of {facts.length} checked · <Kbd>V</Kbd> <Kbd>D</Kbd> <Kbd>C</Kbd>
        </span>
      </div>
      {shown.length === 0 && <div className="faint" style={{ padding: '12px 2px' }}>No facts in this group.</div>}
      <div ref={list}>
        {shown.map((f) => {
          const mark = markOf(f)
          return (
            <article
              className={cx('fact', f.figure_note && 'warn', mark && `is-marked-${mark.status}`, f.id === focusId && 'is-cited')}
              key={f.id}
              data-fact-id={f.id}
              tabIndex={0}
              aria-label={`Fact${f.page ? `, page ${f.page}` : ''}${mark ? `, marked ${mark.status}` : ''}`}
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
                {f.page ? (
                  <button
                    className="fact-page"
                    onClick={() => jump({ page: f.page!, find: media && (f.quote || f.passage) ? passageSnippet((f.quote || f.passage)!) : undefined })}
                    data-tip={media ? `Play page ${f.page} of the recording` : hasViewer ? `Show page ${f.page}` : `Page ${f.page}`}
                  >
                    {media ? <Clock /> : <FileText />}{pageLabel(media, f.page)}
                  </button>
                ) : null}
                {f.basis === 'inferred' && <Badge tone="info" icon={Info} tip={INFERRED_TIP}>inferred</Badge>}
                {f.figure_note && <Badge tone="warning" icon={AlertTriangle} tip="A figure in this fact was not found where it was cited">check figure</Badge>}
                {f.added_by && <span className="faint" style={{ fontSize: 'var(--fs-xs)' }}>added by {f.added_by}</span>}
              </div>
              <div className="fact-text selectable">{f.fact}</div>
              <Passage f={f} jump={jump} hasViewer={hasViewer} media={media} />
              {f.figure_note && (
                <div className="fact-warn">
                  <AlertTriangle />
                  <div>
                    <b>Figure check.</b> {figureExplain(f.figure_note)}
                    <div className="faint selectable" style={{ marginTop: 2 }}>{f.figure_note.replace(/^\(|\)$/g, '')}</div>
                  </div>
                </div>
              )}
              {f.quote_note && (
                <div className="fact-warn">
                  <AlertTriangle />
                  <div>
                    <b>Quote check.</b> The quoted wording could not be confirmed verbatim in the source. Check it against the page before quoting it.
                    <div className="faint selectable" style={{ marginTop: 2 }}>{f.quote_note.replace(/^\(|\)$/g, '')}</div>
                  </div>
                </div>
              )}
              {f.entities.length > 0 && (
                <div className="fact-ents">
                  {f.entities.map((e) => (
                    <EntityChip key={e.id} id={e.id} name={e.name} type={e.type} />
                  ))}
                </div>
              )}
              <FactCheck id={f.id} mark={mark} marks={marks} noteOpen={noteFor === f.id} setNoteOpen={(v) => setNoteFor(v ? f.id : null)} />
            </article>
          )
        })}
      </div>
    </>
  )
}
