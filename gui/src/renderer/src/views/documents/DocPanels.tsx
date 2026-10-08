// The right-hand tabs of the document reader: facts, summary, entities, text, details, notes.

import { AlertTriangle, Check, ChevronRight, Copy, FileText, Search } from 'lucide-react'
import { ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import { EntityChip } from '@renderer/components/EntityChip'
import { Markdown } from '@renderer/components/Markdown'
import { Button, Callout, Empty } from '@renderer/components/ui'
import { TYPE_META, typeMeta } from '@renderer/lib/entityTypes'
import { fmtDate, fmtDateTime, plural } from '@renderer/lib/format'
import { call, errorMessage, invalidate } from '@renderer/lib/rpc'
import { navigate, useVault } from '@renderer/lib/store'
import type { DocumentDetail } from '@shared/api'
import type { JumpTarget } from './PdfViewer'

type Jump = (t: Omit<JumpTarget, 'nonce'>) => void

// ── Summary ──────────────────────────────────────────────────────────────────
/** Split a note body at its `## Notes` section (the journalist's own). */
export function splitNotes(body: string): { rest: string; notes: string } {
  const lines = body.split('\n')
  let fence = false
  for (let i = 0; i < lines.length; i++) {
    if (/^\s*(```|~~~)/.test(lines[i])) fence = !fence
    if (!fence && /^##[ \t]+Notes[ \t]*$/.test(lines[i])) {
      const notes = lines.slice(i + 1).join('\n').replace(/<!--[\s\S]*?-->/g, '').trim()
      return { rest: lines.slice(0, i).join('\n').trim(), notes }
    }
  }
  return { rest: body.trim(), notes: '' }
}

export function SummaryTab({ d }: { d: DocumentDetail }) {
  const { rest } = useMemo(() => splitNotes(d.body ?? ''), [d.body])
  const body = rest.replace(/^#\s.*\n+/, '')
  return (
    <>
      {d.summary && <p className="docv-summary">{d.summary}</p>}
      {body ? <Markdown text={body} /> : !d.summary && <Empty icon={FileText} title="No summary yet">This document has no summary or note body.</Empty>}
    </>
  )
}

// ── Entities ─────────────────────────────────────────────────────────────────
export function EntitiesTab({ entities }: { entities: DocumentDetail['entities'] }) {
  const groups = useMemo(() => {
    const order = [...Object.keys(TYPE_META), '__other']
    const m = new Map<string, DocumentDetail['entities']>()
    for (const e of entities) {
      const k = TYPE_META[e.type] ? e.type : '__other'
      m.set(k, [...(m.get(k) ?? []), e])
    }
    return order.filter((k) => m.has(k)).map((k) => [k, m.get(k)!.sort((a, b) => a.name.localeCompare(b.name))] as const)
  }, [entities])
  if (!entities.length) return <Empty icon={FileText} title="No entities">No people, organizations or other entities are tied to this document.</Empty>
  return (
    <>
      {groups.map(([k, list]) => {
        const m = typeMeta(k)
        return (
          <section className="ent-group" key={k}>
            <div className="ent-group-head" style={{ color: m.color }}>
              <m.icon />
              {list.length > 1 ? m.plural : m.label} · {list.length}
            </div>
            {list.map((e) => (
              <div className="ent-row" key={e.id}>
                <EntityChip id={e.id} name={e.name} type={e.type} />
                {e.role && <span className="role">{e.role}</span>}
              </div>
            ))}
          </section>
        )
      })}
    </>
  )
}

// ── Text ─────────────────────────────────────────────────────────────────────
export function Highlight({ text, q }: { text: string; q: string }): ReactNode {
  if (!q) return text
  const lo = text.toLowerCase()
  const needle = q.toLowerCase()
  const out: ReactNode[] = []
  let pos = 0
  for (let at = lo.indexOf(needle); at !== -1; at = lo.indexOf(needle, pos)) {
    if (at > pos) out.push(text.slice(pos, at))
    out.push(<mark key={at}>{text.slice(at, at + needle.length)}</mark>)
    pos = at + needle.length
  }
  out.push(text.slice(pos))
  return out
}

export function TextTab({ pages, jump, hasViewer }: { pages: DocumentDetail['pages']; jump: Jump; hasViewer: boolean }) {
  const [q, setQ] = useState('')
  const needle = q.trim()
  const counts = useMemo(() => {
    if (!needle) return null
    const n = needle.toLowerCase()
    return pages.map((p) => p.text.toLowerCase().split(n).length - 1)
  }, [pages, needle])
  const total = counts?.reduce((a, b) => a + b, 0) ?? 0
  if (!pages.length) return <Empty icon={FileText} title="No extracted text">This document has no full-text file in the vault.</Empty>
  return (
    <>
      <div className="facts-bar">
        <div className="input-group" style={{ flex: '1 1 220px' }}>
          <Search />
          <input className="input" placeholder="Search the extracted text" value={q} onChange={(e) => setQ(e.target.value)} style={{ userSelect: 'text' }} />
        </div>
        {counts && <span className="faint" style={{ fontSize: 'var(--fs-sm)' }}>{total ? `${plural(total, 'match', 'matches')} on ${counts.filter(Boolean).length} pages` : 'No matches'}</span>}
      </div>
      {pages.map((p, i) =>
        counts && !counts[i] ? null : (
          <section className="text-tab-page" key={p.page}>
            <button className="text-tab-page-head" onClick={() => jump({ page: p.page })} disabled={!hasViewer} style={!hasViewer ? { cursor: 'default' } : undefined}>
              p. {p.page}
              {hasViewer && <ChevronRight style={{ width: 11, height: 11 }} />}
            </button>
            <div className="text-tab-page-body">
              <Highlight text={p.text} q={needle} />
            </div>
          </section>
        )
      )}
    </>
  )
}

// ── Details ──────────────────────────────────────────────────────────────────
function show(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—'
  if (Array.isArray(v)) return v.map(show).join(', ')
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

function KV({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="kv selectable">
      {rows.map(([k, v]) => (
        <div key={k} style={{ display: 'contents' }}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  )
}

export function DetailsTab({ d }: { d: DocumentDetail }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    void navigator.clipboard.writeText(d.sha).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1400)
    })
  }
  const side = Object.entries(d.sidecar ?? {})
  const meta = Object.entries(d.file_metadata ?? {})
  const extra = Object.entries(d.metadata ?? {})
  return (
    <>
      <section className="det-section">
        <h4>Document</h4>
        <KV
          rows={[
            ['Title', d.title || '—'],
            ['File name', d.filename],
            ['Document type', d.document_type || '—'],
            ['Document date', d.date_of_document ? fmtDate(d.date_of_document) : '—'],
            ['Pages', d.page_count ?? '—'],
            ['Source', d.source || '—'],
            ['Obtained', d.obtained ? fmtDate(d.obtained) : '—'],
            ['Ingested', d.ingested_at ? fmtDateTime(d.ingested_at) : '—']
          ]}
        />
      </section>
      <section className="det-section">
        <h4>Extraction</h4>
        <KV
          rows={[
            ['Record skill', d.record_skill || '—'],
            ['Skill hash', d.record_skill_hash ? <code>{d.record_skill_hash}</code> : '—'],
            ['Model', d.extract_model || '—'],
            ['Effort', d.extract_effort || '—'],
            [
              'SHA-256',
              <div className="det-sha" key="sha">
                <code>{d.sha}</code>
                <Button size="sm" variant="ghost" icon={copied ? Check : Copy} tip={copied ? 'Copied' : 'Copy SHA-256'} onClick={copy} />
              </div>
            ]
          ]}
        />
      </section>
      {d.near_duplicate_of || d.duplicates.length > 0 ? (
        <section className="det-section">
          <h4>Possible duplicates</h4>
          {d.near_duplicate_of && <p className="muted" style={{ margin: '0 0 8px', fontSize: 'var(--fs-sm)' }}>Matched: {d.near_duplicate_of}</p>}
          {d.duplicates.map((x) => (
            <button key={x.sha} className="det-dup" onClick={() => navigate({ view: 'document', sha: x.sha })}>
              <Copy />
              <span className="grow">
                <div style={{ fontWeight: 580 }} className="truncate">{x.filename}</div>
                {x.note && <div className="faint" style={{ fontSize: 'var(--fs-xs)' }}>{x.note}</div>}
              </span>
              <ChevronRight />
            </button>
          ))}
        </section>
      ) : null}
      {side.length > 0 && (
        <section className="det-section">
          <h4>Sidecar</h4>
          <KV rows={side.map(([k, v]) => [k, show(v)])} />
        </section>
      )}
      {extra.length > 0 && (
        <section className="det-section">
          <h4>Capture metadata</h4>
          <KV rows={extra.map(([k, v]) => [k, show(v)])} />
        </section>
      )}
      <section className="det-section">
        <h4>Embedded file metadata</h4>
        {meta.length ? <KV rows={meta.map(([k, v]) => [k, show(v)])} /> : <div className="faint">No embedded metadata was found in the file.</div>}
        <Callout tone="info" style={{ marginTop: 12 }}>
          Treat this as a lead, not a fact. It is easy to forge and often says nothing about who actually wrote the document: a scanner's software name is not the scan's author, and a template's creation date is inherited by every document built from it.
        </Callout>
      </section>
    </>
  )
}

// ── Notes ────────────────────────────────────────────────────────────────────
type Save = 'idle' | 'dirty' | 'saving' | 'saved' | 'error'

export function NotesTab({ d }: { d: DocumentDetail }) {
  const vault = useVault()
  const initial = useMemo(() => splitNotes(d.body ?? '').notes, [d.body])
  const [text, setText] = useState(initial)
  const [state, setState] = useState<Save>('idle')
  const [err, setErr] = useState('')
  const latest = useRef({ text: initial, dirty: false, saved: initial })
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const path = d.note

  const flush = async () => {
    clearTimeout(timer.current)
    const cur = latest.current
    if (!cur.dirty || !path) return
    setState('saving')
    try {
      await call('vault.saveNotes', { vault, path, text: cur.text })
      if (latest.current.text === cur.text) latest.current.dirty = false
      latest.current.saved = cur.text
      setState('saved')
      invalidate('vault.document', 'vault.note')
    } catch (e) {
      setErr(errorMessage(e))
      setState('error')
    }
  }
  const flushRef = useRef(flush)
  flushRef.current = flush

  useEffect(() => () => void flushRef.current(), [])

  if (!path) return <Empty icon={FileText} title="No note for this document">Notes are kept in the document's own note in the vault, which doesn't exist yet.</Empty>

  return (
    <div className="notes-editor">
      <Callout tone="info">
        These notes are yours. Watchdog never writes to this section, so what you put here survives every ingest. They are saved to the document's note in the vault, and show up in Obsidian too.
      </Callout>
      <textarea
        className="textarea"
        value={text}
        placeholder="Your own annotations, questions and follow-ups on this document."
        onChange={(e) => {
          setText(e.target.value)
          latest.current.text = e.target.value
          latest.current.dirty = e.target.value !== latest.current.saved
          setState(latest.current.dirty ? 'dirty' : 'idle')
          clearTimeout(timer.current)
          timer.current = setTimeout(() => void flushRef.current(), 1800)
        }}
        onBlur={() => void flush()}
      />
      <div className={'notes-status' + (state === 'saved' ? ' saved' : state === 'error' ? ' error' : '')} aria-live="polite">
        {state === 'saved' && (<><Check />Saved</>)}
        {state === 'saving' && 'Saving…'}
        {state === 'dirty' && 'Unsaved changes. Saves when you click away.'}
        {state === 'error' && (<><AlertTriangle />Could not save: {err}</>)}
      </div>
    </div>
  )
}
