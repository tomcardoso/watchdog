// Review's tidy-up queues as tables (D328): possible same entities, possible duplicate documents
// and document requests, one row each, ticked and applied in bulk. The focused row opens below
// itself with the full evidence. Keyboard as in the rest of Review: J/K move, X ticks, H applies
// the row's everyday answer (not the same, copies, handled), O opens, S keeps open, U undoes.
//
// "Merge ticked" runs merge-entity-pairs, which merges the pairs one at a time through the same
// merge as Merge… (each logged, each undoable); "Not the same", "These are copies", "Different
// documents" and "Mark handled" write the resolutions store, as every other queue does.

import { ArrowUpRight, Check, CheckCircle2, Copy, Files, GitCompare, GitMerge, ListTree, Undo2, X } from 'lucide-react'
import { Fragment, ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import type { DocumentRow, MergePairsResult, RequestGroup, SamePair } from '@shared/api'
import { Button, Callout, Empty, ErrorNote, Kbd, Segmented, Skeleton, cx } from '@renderer/components/ui'
import { DocThumb } from '@renderer/components/DocThumb'
import { useOpenWikilink } from '@renderer/components/Markdown'
import { useEngineGate, ENGINE_WAIT_OTHER } from '@renderer/lib/engine'
import { fmtDate, fmtNum, plural } from '@renderer/lib/format'
import { startJob, waitForJob } from '@renderer/lib/jobs'
import { errorMessage, invalidate, useRpc } from '@renderer/lib/rpc'
import { navigate, toast, useVault } from '@renderer/lib/store'
import { MergeLogSection, SamePairView, TierBadge } from './MergesTab'
import MergeModal from '../entities/MergeModal'
import type { QItem, Triage } from './ReviewView'

export type TidyKind = 'merges' | 'duplicates' | 'requests'
type Verdict = 'copies' | 'different'

const stripMd = (p: string) => p.replace(/\.md$/, '')

export function Key({ k, k2, label }: { k: string; k2?: string; label: string }) {
  return (
    <span className="rv-key">
      <Kbd>{k}</Kbd>
      {k2 && <Kbd>{k2}</Kbd>}
      <span>{label}</span>
    </span>
  )
}

// ── possible same entities ───────────────────────────────────────────────────────────────────

/** Which record a bulk merge keeps: the one in more documents, then the longer name (an initialled
 * or shortened form usually folds into the full one), then the first. */
export function survivor(pair: SamePair): { keep: SamePair['a']; merge: SamePair['b'] } {
  const { a, b } = pair
  const da = a.doc_count || a.documents.length
  const db = b.doc_count || b.documents.length
  if (db > da || (db === da && b.name.length > a.name.length)) return { keep: b, merge: a }
  return { keep: a, merge: b }
}

const SCHEME: Record<string, string> = { registration: 'registration number', licence: 'licence number', 'court-file': 'court file number', parcel: 'parcel identifier' }

/** The strongest evidence a pair shares, in one line: an identifier, then a shared relationship,
 * then what the model or the rule said. */
export function strongestEvidence(item: QItem): string {
  const ev = item.pair?.evidence ?? {}
  if (ev.identifier) return `Same ${SCHEME[ev.identifier.scheme] ?? ev.identifier.scheme}: ${ev.identifier.value}`
  if (ev.shared?.length) return `Both ${ev.shared[0].relationship} ${ev.shared[0].target_name}`
  if (item.pair?.model_declined) return 'The AI model compared their facts and was not sure'
  return item.detail[0] ?? ''
}

// ── duplicates ───────────────────────────────────────────────────────────────────────────────

function pairDocs(item: QItem, docs?: DocumentRow[]): { mine?: DocumentRow; other?: DocumentRow } {
  const prefix = item.rid.replace(/^duplicate:/, '')
  const mine = docs?.find((d) => d.sha.startsWith(prefix))
  const link = mine?.near_duplicate_of ?? null
  const target = link ? stripMd(link.replace(/^\[\[|\]\]$/g, '').split('|')[0]) : null
  const linkText = link?.replace(/^\[\[|\]\]$/g, '').split('|').pop() ?? null
  const stem = (d: DocumentRow) => stripMd(d.note ?? '').replace(/^.*?(documents\/)/, '$1')
  const other = docs?.find((d) => d.sha !== mine?.sha && ((target && stem(d) === target) || d.filename === linkText || d.filename === link))
  return { mine, other }
}

function matchName(item: QItem): string {
  return (item.detail[0] ?? '').replace(/^Closely matches /, '').replace(/ — confirm whether.*$/, '')
}

function DuplicatePair({ item, docs }: { item: QItem; docs?: DocumentRow[] }) {
  const { mine, other } = pairDocs(item, docs)
  return (
    <div className="rv-pair">
      <PairDoc label="This document" doc={mine} />
      <div className="rv-pair-vs">
        <GitCompare />
      </div>
      <PairDoc label="Closely matches" doc={other} fallback={item.detail[0]} />
    </div>
  )
}

function PairDoc({ label, doc, fallback }: { label: string; doc?: DocumentRow; fallback?: string }) {
  if (!doc) return <div className="rv-pairdoc ghost"><span className="eyebrow">{label}</span><span className="faint">{fallback ? 'Not found in the library' : 'Loading…'}</span></div>
  return (
    <button
      className="rv-pairdoc"
      onClick={(e) => {
        e.stopPropagation()
        navigate({ view: 'document', sha: doc.sha })
      }}
    >
      <DocThumb sha={doc.sha} ext={doc.ext} original={doc.original} title={doc.title ?? doc.filename} summary={doc.summary} width={140} className="rv-thumb" />
      <div className="rv-pairdoc-text">
        <span className="eyebrow">{label}</span>
        <span className="rv-pairdoc-title clamp-2">{doc.title ?? doc.filename}</span>
        <span className="faint rv-pairdoc-meta">{[doc.page_count ? plural(doc.page_count, 'page') : '', fmtDate(doc.date_of_document)].filter(Boolean).join(' · ')}</span>
      </div>
    </button>
  )
}

// ── requests ─────────────────────────────────────────────────────────────────────────────────

function readGrouped(): boolean {
  try {
    return localStorage.getItem('wd.review.requestsGrouped') !== 'flat'
  } catch {
    return true
  }
}

/** Requests as a table: HTML for an email or a spreadsheet, tab-separated text for anything else
 * (the same clipboard pair as Copy leads). */
async function copyRequests(items: QItem[], groups: Map<string, RequestGroup>) {
  const head = ['Request', 'Type', 'Why it matters', 'Likely source', 'Source group', 'Documents citing it', 'Cited in']
  const rows = items.map((it) => {
    const r = it.request!
    return [r.what, r.type ?? '', r.why ?? '', r.likely_source ?? '', groups.get(r.source_key)?.label || 'Source not given', String(r.cite_count), r.cited_in.map((c) => c.filename).join('; ')]
  })
  const esc = (t: string) => t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const html = `<table><tr>${head.map((h) => `<th>${h}</th>`).join('')}</tr>${rows.map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</table>`
  const text = [head, ...rows].map((r) => r.map((c) => c.replace(/[\t\n]/g, ' ')).join('\t')).join('\n')
  try {
    await navigator.clipboard.write([new ClipboardItem({ 'text/html': new Blob([html], { type: 'text/html' }), 'text/plain': new Blob([text], { type: 'text/plain' }) })])
    toast({ kind: 'success', title: `Copied ${plural(items.length, 'request')}`, body: 'Paste into an email or a spreadsheet.' })
  } catch (e) {
    toast({ kind: 'error', title: 'Could not copy', body: errorMessage(e) })
  }
}

// ── the table ────────────────────────────────────────────────────────────────────────────────

const PRIMARY: Record<TidyKind, { label: string; done: (n: number) => string }> = {
  merges: { label: 'Not the same', done: (n) => (n === 1 ? 'Marked not the same' : `${plural(n, 'pair')} marked not the same`) },
  duplicates: { label: 'These are copies', done: (n) => (n === 1 ? 'Marked as copies' : `${plural(n, 'pair')} marked as copies`) },
  requests: { label: 'Mark handled', done: (n) => (n === 1 ? 'Marked handled' : `${plural(n, 'request')} marked handled`) }
}

export function TidyTab({ kind, items, triage, loading, error, retry, focusRequest, groups, blurb, empty, onEmpty }: {
  kind: TidyKind
  items: QItem[]
  triage: Triage
  loading: boolean
  error: unknown
  retry: () => void
  focusRequest?: { rid: string; n: number } | null
  groups?: RequestGroup[]
  blurb: ReactNode
  empty: string
  onEmpty?: ReactNode
}) {
  const vault = useVault()
  const openLink = useOpenWikilink()
  const engine = useEngineGate()
  const docs = useRpc('vault.documents', kind === 'duplicates' && vault ? { vault } : null)
  const [grouped, setGrouped] = useState(readGrouped)
  const groupMap = useMemo(() => new Map((groups ?? []).map((g) => [g.key, g])), [groups])

  const visible = useMemo(() => {
    const live = items.filter((i) => !triage.hidden.has(i.rid))
    if (kind !== 'requests' || !grouped) return live
    // Grouped: the groups' order (the server's), each group's rows most-cited first, as given.
    const order = new Map((groups ?? []).map((g, i) => [g.key, i]))
    return [...live].sort((x, y) => (order.get(x.request!.source_key) ?? 999) - (order.get(y.request!.source_key) ?? 999))
  }, [items, triage.hidden, kind, grouped, groups])

  const [focus, setFocus] = useState<string | null>(focusRequest?.rid ?? null)
  useEffect(() => {
    if (focusRequest) setFocus(focusRequest.rid)
  }, [focusRequest])
  const [ticked, setTicked] = useState<Set<string>>(new Set())
  const [flash, setFlash] = useState<{ text: string; undo: boolean } | null>(null)
  const [handledHere, setHandledHere] = useState(0)
  const [merging, setMerging] = useState<Set<string>>(new Set())
  const [mergeReport, setMergeReport] = useState<MergePairsResult | null>(null)
  const [mergeFor, setMergeFor] = useState<{ keep: string; merge: string } | null>(null)
  const flashTimer = useRef<ReturnType<typeof setTimeout>>(undefined)

  const live = visible.filter((i) => !merging.has(i.rid))
  const focusId = focus && live.some((i) => i.rid === focus) ? focus : live[0]?.rid ?? null
  const focusIdx = live.findIndex((i) => i.rid === focusId)
  const tickedLive = live.filter((i) => ticked.has(i.rid))

  const say = (text: string, undo = false) => {
    setFlash({ text, undo })
    clearTimeout(flashTimer.current)
    flashTimer.current = setTimeout(() => setFlash(null), 6000)
  }
  useEffect(() => () => clearTimeout(flashTimer.current), [])

  const nextAfter = (rids: string[]) => {
    const gone = new Set(rids)
    const from = Math.max(0, live.findIndex((i) => gone.has(i.rid)))
    return live.slice(from).find((i) => !gone.has(i.rid))?.rid ?? [...live.slice(0, from)].reverse().find((i) => !gone.has(i.rid))?.rid ?? null
  }

  /** The ticked rows, or the focused one when none is ticked. */
  const targets = (): string[] => (tickedLive.length ? tickedLive.map((i) => i.rid) : focusId ? [focusId] : [])

  const resolve = (rids: string[], verdict?: Verdict) => {
    if (!rids.length) return
    setFocus(nextAfter(rids))
    setTicked(new Set())
    setHandledHere((n) => n + rids.length)
    void triage.resolve(rids, kind, verdict)
    say(verdict === 'different' ? (rids.length === 1 ? 'Marked as different documents' : `${plural(rids.length, 'pair')} marked as different documents`) : PRIMARY[kind].done(rids.length), true)
  }

  const mergeTicked = async () => {
    const rows = (tickedLive.length ? tickedLive : live.filter((i) => i.rid === focusId)).filter((i) => i.pair)
    if (!rows.length) return
    if (!engine.ready) return toast({ kind: 'info', title: 'Not yet', body: ENGINE_WAIT_OTHER })
    const plan = rows.map((i) => ({ rid: i.rid, ...survivor(i.pair!) }))
    const list = plan.slice(0, 6).map((p) => `${p.merge.name} into ${p.keep.name}`).join('; ')
    const ok = await window.watchdog.dialog.confirm({
      title: rows.length === 1 ? `Merge ${plan[0].merge.name} into ${plan[0].keep.name}?` : `Merge ${rows.length} pairs?`,
      message: `${list}${plan.length > 6 ? `; and ${plan.length - 6} more` : ''}. Each pair keeps the record named in more documents.`,
      detail: 'The pairs are merged one at a time. Each merge is listed under Recent merges, where it can be undone on its own. If one fails, the others still go ahead.',
      confirm: rows.length === 1 ? 'Merge' : `Merge ${rows.length} pairs`
    })
    if (!ok) return
    const rids = plan.map((p) => p.rid)
    setMerging((m) => new Set([...m, ...rids]))
    setTicked(new Set())
    setMergeReport(null)
    setFocus(nextAfter(rids))
    try {
      const job = await startJob('merge-entity-pairs', { keep: plan.map((p) => p.keep.id), merge: plan.map((p) => p.merge.id) }, rows.length === 1 ? 'Merging entities' : `Merging ${rows.length} pairs`)
      say(`Merging ${plural(rows.length, 'pair')}…`)
      const done = await waitForJob(job.id)
      const result = (done.result as MergePairsResult | null) ?? null
      invalidate('vault.', 'review.')
      if (result) {
        setMergeReport(result.failed.length ? result : null)
        say(`Merged ${plural(result.merged.length, 'pair')}${result.failed.length ? `; ${result.failed.length} could not be merged` : ''}. Each can be undone under Recent merges.`)
      } else {
        setMergeReport({ merged: [], failed: plan.map((p) => ({ keep: p.keep.id, merge: p.merge.id, keep_name: p.keep.name, merge_name: p.merge.name, error: done.state === 'cancelled' ? 'Stopped before it finished.' : 'The merge did not finish. Open Activity to see what it reported.' })), skipped: [] })
      }
    } catch (e) {
      toast({ kind: 'error', title: 'Could not start the merge', body: errorMessage(e) })
    } finally {
      setMerging((m) => {
        const n = new Set(m)
        rids.forEach((r) => n.delete(r))
        return n
      })
    }
  }

  const undo = async () => {
    const last = await triage.undo()
    if (last) {
      setFocus(last.rids[0])
      setHandledHere((n) => Math.max(0, n - last.rids.length))
      say('Brought back')
    }
  }

  const move = (d: number) => {
    if (!live.length) return
    const i = Math.max(0, focusIdx)
    setFocus(live[Math.min(live.length - 1, Math.max(0, i + d))].rid)
  }

  const toggle = (rid: string) =>
    setTicked((s) => {
      const n = new Set(s)
      n.has(rid) ? n.delete(rid) : n.add(rid)
      return n
    })

  const openItem = (it?: QItem) => {
    if (!it) return
    if (kind === 'requests') {
      const c = it.request?.cited_in[0]
      if (c?.sha) navigate({ view: 'document', sha: c.sha })
      return
    }
    if (kind === 'merges' && it.pair) return navigate({ view: 'entity', id: survivor(it.pair).keep.id })
    if (it.note) void openLink(stripMd(it.note))
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const t = e.target as HTMLElement
      if (t?.closest('input, textarea, select, [contenteditable="true"], [role="dialog"]')) return
      const cur = live.find((i) => i.rid === focusId)
      const k = e.key.toLowerCase()
      if (k === 'j' || e.key === 'ArrowDown') {
        e.preventDefault()
        move(1)
      } else if (k === 'k' || e.key === 'ArrowUp') {
        e.preventDefault()
        move(-1)
      } else if ((k === 'h' || k === 'e') && cur) {
        e.preventDefault()
        resolve(targets(), kind === 'duplicates' ? 'copies' : undefined)
      } else if (k === 'd' && kind === 'duplicates' && cur) {
        e.preventDefault()
        resolve(targets(), 'different')
      } else if (k === 'm' && kind === 'merges' && cur) {
        e.preventDefault()
        void mergeTicked()
      } else if ((k === 'o' || e.key === 'Enter') && cur) {
        e.preventDefault()
        openItem(cur)
      } else if (k === 'x' && cur) {
        e.preventDefault()
        toggle(cur.rid)
      } else if (k === 'u') {
        e.preventDefault()
        void undo()
      } else if (k === 's' && cur) {
        e.preventDefault()
        move(1)
        say('Kept open')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  useEffect(() => {
    if (focusId) document.querySelector(`[data-rid="${CSS.escape(focusId)}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [focusId])

  const allTicked = live.length > 0 && tickedLive.length === live.length
  const cols = kind === 'merges' ? 5 : kind === 'duplicates' ? 4 : 4
  const tickGroup = (key: string) => {
    const rids = live.filter((i) => i.request?.source_key === key).map((i) => i.rid)
    const all = rids.every((r) => ticked.has(r))
    setTicked((s) => {
      const n = new Set(s)
      rids.forEach((r) => (all ? n.delete(r) : n.add(r)))
      return n
    })
  }

  const head = (
    <tr>
      <th className="rv-tt-check">
        <input type="checkbox" aria-label="Tick every row" checked={allTicked} onChange={() => setTicked(allTicked ? new Set() : new Set(live.map((i) => i.rid)))} />
      </th>
      {kind === 'merges' && (
        <>
          <th>Records</th>
          <th className="num">Documents</th>
          <th>Strongest shared evidence</th>
          <th>Confidence</th>
        </>
      )}
      {kind === 'duplicates' && (
        <>
          <th>Document</th>
          <th>Closely matches</th>
          <th>Added</th>
        </>
      )}
      {kind === 'requests' && (
        <>
          <th>Request</th>
          <th className="num" data-tip="How many documents cite it">Documents</th>
          <th>Likely source</th>
        </>
      )}
    </tr>
  )

  const row = (it: QItem) => {
    const focused = it.rid === focusId
    const isTicked = ticked.has(it.rid)
    const busy = merging.has(it.rid)
    let cells: ReactNode = null
    if (kind === 'merges' && it.pair) {
      const { keep, merge } = survivor(it.pair)
      cells = (
        <>
          <td className="rv-tt-main">
            <span className="rv-tt-names">
              <b>{keep.name}</b>
              <span className="faint"> and </span>
              <b>{merge.name}</b>
            </span>
            {it.new && <span className="rv-new">New</span>}
          </td>
          <td className="num tnum">
            {fmtNum(keep.doc_count || keep.documents.length)} · {fmtNum(merge.doc_count || merge.documents.length)}
          </td>
          <td className="rv-tt-ev">{strongestEvidence(it)}</td>
          <td>
            <TierBadge tier={it.pair.tier} />
          </td>
        </>
      )
    } else if (kind === 'duplicates') {
      const { mine } = pairDocs(it, docs.data)
      cells = (
        <>
          <td className="rv-tt-main">
            <b>{it.title}</b>
            {it.new && <span className="rv-new">New</span>}
          </td>
          <td className="rv-tt-ev">{matchName(it)}</td>
          <td className="faint">{mine?.ingested_at ? fmtDate(mine.ingested_at) : ''}</td>
        </>
      )
    } else if (kind === 'requests') {
      const r = it.request!
      cells = (
        <>
          <td className="rv-tt-main">
            <span className={cx('rv-tt-what', !focused && 'clamp-2')}>{r.what}</span>
            {r.type && <span className="faint rv-tt-type">{r.type}</span>}
          </td>
          <td className="num tnum">{fmtNum(r.cite_count)}</td>
          <td className="rv-tt-ev">{r.likely_source ?? <span className="faint">Not given</span>}</td>
        </>
      )
    }
    return (
      <Fragment key={it.rid}>
        <tr data-rid={it.rid} className={cx('rv-tt-row', focused && 'focused', isTicked && 'ticked', busy && 'busy')} onMouseDown={() => setFocus(it.rid)}>
          <td className="rv-tt-check" onMouseDown={(e) => e.stopPropagation()}>
            <input type="checkbox" checked={isTicked} disabled={busy} onChange={() => toggle(it.rid)} aria-label="Tick" data-tip="Tick (X)" data-tip-pos="right" />
          </td>
          {cells}
        </tr>
        {focused && !busy && (
          <tr className="rv-tt-detail">
            <td />
            <td colSpan={cols - 1}>
              <Detail item={it} kind={kind} docs={docs.data} onMerge={it.pair ? () => setMergeFor((() => { const s = survivor(it.pair!); return { keep: s.keep.id, merge: s.merge.id } })()) : undefined} onPrimary={() => resolve([it.rid], kind === 'duplicates' ? 'copies' : undefined)} onDifferent={() => resolve([it.rid], 'different')} onKeep={() => (move(1), say('Kept open'))} />
            </td>
          </tr>
        )}
      </Fragment>
    )
  }

  let body: ReactNode
  if (kind === 'requests' && grouped) {
    const byKey = new Map<string, QItem[]>()
    visible.forEach((i) => byKey.set(i.request!.source_key, [...(byKey.get(i.request!.source_key) ?? []), i]))
    body = [...byKey.entries()].map(([key, list]) => {
      const g = groupMap.get(key)
      const groupLive = list.filter((i) => !merging.has(i.rid))
      const allIn = groupLive.length > 0 && groupLive.every((i) => ticked.has(i.rid))
      return (
        <Fragment key={`g:${key}`}>
          <tr className="rv-tt-group">
            <td className="rv-tt-check">
              <input type="checkbox" aria-label={`Tick every request to ${g?.label || 'no named source'}`} checked={allIn} onChange={() => tickGroup(key)} />
            </td>
            <td colSpan={cols - 1}>
              <b>{key ? g?.label ?? key : 'Source not given'}</b>
              <span className="faint">
                {' '}
                · {plural(list.length, 'request')}
                {g ? ` · cited by ${plural(g.documents, 'document')}` : ''}
              </span>
            </td>
          </tr>
          {list.map(row)}
        </Fragment>
      )
    })
  } else body = visible.map(row)

  const primaryCount = tickedLive.length
  return (
    <div className="rv-queue">
      <div className="rv-blurb">
        {kind === 'merges' ? <GitMerge /> : kind === 'duplicates' ? <GitCompare /> : <Files />}
        <p>{blurb}</p>
      </div>

      {kind === 'requests' && (
        <div className="rv-toolbar">
          <Segmented<'grouped' | 'flat'>
            value={grouped ? 'grouped' : 'flat'}
            onChange={(v) => {
              setGrouped(v === 'grouped')
              try {
                localStorage.setItem('wd.review.requestsGrouped', v)
              } catch {
                /* a remembered choice is a convenience only */
              }
            }}
            options={[
              { value: 'grouped', label: 'By likely source', icon: ListTree, tip: 'Requests to the same office or company together, so one request letter can cover them' },
              { value: 'flat', label: 'Most cited first', tip: 'Every request in one list, the one cited by the most documents first' }
            ]}
          />
          <span className="spacer" />
          <Button size="sm" icon={Copy} disabled={!live.length} tip="Copy as a table to paste into an email or a spreadsheet" onClick={() => void copyRequests(tickedLive.length ? tickedLive : live, groupMap)}>
            {tickedLive.length ? `Copy ${tickedLive.length} ticked` : 'Copy requests'}
          </Button>
        </div>
      )}
      {kind === 'duplicates' && (
        <div className="rv-note faint">
          Neither answer deletes anything. <b>These are copies</b> records that the two are the same record, and the later one stays marked as a copy. <b>Different documents</b> removes the possible-duplicate flag from both. Either can be brought back under Handled.
        </div>
      )}

      {mergeReport && mergeReport.failed.length > 0 && (
        <Callout
          tone="warning"
          title={`${plural(mergeReport.failed.length, 'pair')} could not be merged`}
          action={<Button size="sm" variant="ghost" icon={X} tip="Dismiss" onClick={() => setMergeReport(null)} />}
        >
          {mergeReport.merged.length ? `${plural(mergeReport.merged.length, 'pair')} merged; those merges stand. ` : ''}
          <ul className="rv-tt-failed">
            {mergeReport.failed.map((f, i) => (
              <li key={i}>
                <b>{f.merge_name}</b> into <b>{f.keep_name}</b>: {f.error}
              </li>
            ))}
          </ul>
        </Callout>
      )}

      {error ? (
        <ErrorNote error={error} retry={retry} />
      ) : loading ? (
        <Skeleton h={140} />
      ) : !visible.length ? (
        <Empty icon={CheckCircle2} title={handledHere ? `All clear — ${plural(handledHere, 'item')} handled` : 'Nothing to tidy here'} action={onEmpty}>
          {handledHere ? 'Handled items are listed under Handled, where any of them can be brought back.' : empty}
        </Empty>
      ) : (
        <div className="rv-tt-wrap">
          <table className="table rv-tt">
            <thead>{head}</thead>
            <tbody>{body}</tbody>
          </table>
        </div>
      )}

      {kind === 'merges' && <MergeLogSection />}
      {kind === 'merges' && <MergeModal open={!!mergeFor} onClose={() => setMergeFor(null)} initialKeep={mergeFor?.keep} initialMerge={mergeFor?.merge} />}

      <div className="rv-legend">
        {primaryCount > 0 ? (
          <>
            <b className="tnum">{primaryCount}</b>
            <span className="muted">ticked</span>
            {kind === 'merges' && (
              <Button size="sm" variant="primary" icon={GitMerge} disabled={!engine.ready} tip={engine.ready ? undefined : ENGINE_WAIT_OTHER} onClick={() => void mergeTicked()}>
                Merge ticked
              </Button>
            )}
            <Button size="sm" variant={kind === 'merges' ? 'default' : 'primary'} icon={Check} onClick={() => resolve(tickedLive.map((i) => i.rid), kind === 'duplicates' ? 'copies' : undefined)}>
              {kind === 'merges' ? 'Not the same for ticked' : kind === 'duplicates' ? 'These are copies' : 'Mark ticked handled'}
            </Button>
            {kind === 'duplicates' && (
              <Button size="sm" onClick={() => resolve(tickedLive.map((i) => i.rid), 'different')}>
                Different documents
              </Button>
            )}
            <Button size="sm" variant="ghost" onClick={() => setTicked(new Set())}>
              Clear
            </Button>
            <span className="spacer" />
            {!allTicked && (
              <Button size="sm" variant="ghost" onClick={() => setTicked(new Set(live.map((i) => i.rid)))}>
                Tick all {live.length}
              </Button>
            )}
          </>
        ) : flash ? (
          <>
            <CheckCircle2 className="rv-flash-icon" />
            <span>{flash.text}</span>
            {flash.undo && triage.canUndo && (
              <Button size="sm" variant="soft" icon={Undo2} onClick={() => void undo()}>
                Undo
              </Button>
            )}
          </>
        ) : (
          <>
            <Key k="J" k2="K" label="move" />
            <Key k="X" label="tick" />
            {kind === 'merges' && <Key k="M" label="merge" />}
            <Key k="H" label={PRIMARY[kind].label.toLowerCase()} />
            {kind === 'duplicates' && <Key k="D" label="different" />}
            <Key k="O" label="open" />
            <Key k="U" label="undo" />
            <span className="spacer" />
            {live.length > 1 && <span className="faint tnum">{Math.max(1, focusIdx + 1)} of {live.length}</span>}
          </>
        )}
      </div>
    </div>
  )
}

function Detail({ item, kind, docs, onMerge, onPrimary, onDifferent, onKeep }: { item: QItem; kind: TidyKind; docs?: DocumentRow[]; onMerge?: () => void; onPrimary: () => void; onDifferent: () => void; onKeep: () => void }) {
  const openLink = useOpenWikilink()
  const r = item.request
  return (
    <div className="rv-tt-more">
      {kind === 'merges' && item.pair && (
        <>
          <SamePairView pair={item.pair} expanded />
          {item.detail[0] && <div className="faint rv-tt-reason">{item.detail.join(' ')}</div>}
        </>
      )}
      {kind === 'duplicates' && <DuplicatePair item={item} docs={docs} />}
      {kind === 'requests' && r && (
        <>
          {r.why && <div className="rv-tt-why">{r.why}</div>}
          {r.likely_source && <div className="faint">Likely source: {r.likely_source}</div>}
          {r.cited_in.length > 0 && (
            <div className="rv-cited">
              <span className="faint">Cited in</span>
              {r.cited_in.slice(0, 12).map((c, i) => (
                <button key={i} className="rv-cite" onClick={() => (c.sha ? navigate({ view: 'document', sha: c.sha }) : c.note && void openLink(stripMd(c.note)))}>
                  {c.filename}
                </button>
              ))}
              {r.cited_in.length > 12 && <span className="faint">+{r.cited_in.length - 12}</span>}
            </div>
          )}
        </>
      )}
      <div className="rv-actions">
        {onMerge && (
          <Button size="sm" icon={GitMerge} onClick={onMerge} tip="Choose which record to keep, and see what the merge does">
            Merge…
          </Button>
        )}
        <Button size="sm" variant="primary" icon={Check} onClick={onPrimary}>
          {PRIMARY[kind].label} <Kbd>H</Kbd>
        </Button>
        {kind === 'duplicates' && (
          <Button size="sm" onClick={onDifferent}>
            Different documents <Kbd>D</Kbd>
          </Button>
        )}
        <Button size="sm" onClick={onKeep}>
          Keep open
        </Button>
        {kind === 'duplicates' && item.note && (
          <Button size="sm" variant="ghost" iconRight={ArrowUpRight} onClick={() => void openLink(stripMd(item.note!))}>
            Open document
          </Button>
        )}
        {kind === 'merges' && item.pair && <span className="faint">Merge ticked keeps {survivor(item.pair).keep.name}, the record in more documents.</span>}
      </div>
    </div>
  )
}
