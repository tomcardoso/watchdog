// `watchdog search` in full: exact matches, source passages and notes in three separate lanes,
// plus the two cheaper modes the CLI has — every investigation at once, and a pasted list of
// names checked one by one. See docs/commands.md § watchdog search.

import { useEngineGate } from '@renderer/lib/engine'
import {
  ArrowUpRight,
  FileText,
  FileUp,
  History,
  ListChecks,
  Quote,
  Search,
  SearchX,
  SlidersHorizontal,
  Sparkles,
  X
} from 'lucide-react'
import { Fragment, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { BatchResult, EverywhereResult, SearchExact, SearchPassage, SearchNote } from '@shared/api'
import { Badge, Button, Callout, Empty, ErrorNote, Segmented, Skeleton, Switch, cx } from '@renderer/components/ui'
import { useOpenWikilink } from '@renderer/components/Markdown'
import { call, errorMessage, useRpc } from '@renderer/lib/rpc'
import { navigate, toast, useApp, useVault } from '@renderer/lib/store'
import { startJob } from '@renderer/lib/jobs'
import { plural } from '@renderer/lib/format'
import './search.css'

type Mode = 'here' | 'everywhere' | 'batch'

// ── query parsing (mirrors the CLI's _search_query_terms: quotes, +phrase, -phrase) ──────────
export function queryTerms(q: string): string[] {
  const out: string[] = []
  const quoted = /"([^"]+)"/g
  let m: RegExpExecArray | null
  while ((m = quoted.exec(q))) out.push(m[1].trim())
  const words = q.replace(quoted, ' ').split(/\s+/).filter(Boolean)
  const segs: { sign: string; words: string[] }[] = [{ sign: '+', words: [] }]
  for (const w of words) {
    if (/^[+-]./.test(w)) segs.push({ sign: w[0], words: [w.slice(1)] })
    else segs[segs.length - 1].words.push(w)
  }
  for (const s of segs) {
    if (s.sign === '-' || !s.words.length) continue
    out.push(s.words.join(' '))
    for (const w of s.words) if (w.length >= 3) out.push(w)
  }
  return [...new Set(out.filter((t) => t.length >= 2))]
}

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

function windowed(text: string, terms: string[], width: number): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  if (flat.length <= width) return flat
  const lower = flat.toLowerCase()
  let at = -1
  for (const t of terms) {
    const i = lower.indexOf(t.toLowerCase())
    if (i >= 0 && (at < 0 || i < at)) at = i
  }
  const start = at < 0 ? 0 : Math.max(0, at - Math.floor(width / 3))
  const end = Math.min(flat.length, start + width)
  return (start > 0 ? '… ' : '') + flat.slice(start, end).trim() + (end < flat.length ? ' …' : '')
}

function Highlight({ text, terms }: { text: string; terms: string[] }) {
  if (!terms.length) return <>{text}</>
  const re = new RegExp(`(${[...terms].sort((a, b) => b.length - a.length).map(escapeRe).join('|')})`, 'gi')
  const parts = text.split(re)
  return (
    <>
      {parts.map((p, i) => (i % 2 ? <mark key={i}>{p}</mark> : <Fragment key={i}>{p}</Fragment>))}
    </>
  )
}

const prettyPath = (p: string) => p.replace(/\.md$/, '').split('/')
const noteTitle = (p: string) => (prettyPath(p).pop() ?? p).replace(/[-_]/g, ' ')

// ── recent searches ──────────────────────────────────────────────────────────────────────────
function useRecent() {
  const [recent, setRecent] = useState<string[]>([])
  useEffect(() => {
    window.watchdog.prefs.get<string[]>('recentSearches').then((v) => Array.isArray(v) && setRecent(v)).catch(() => {})
  }, [])
  const push = useCallback((q: string) => {
    setRecent((cur) => {
      const next = [q, ...cur.filter((x) => x !== q)].slice(0, 8)
      void window.watchdog.prefs.set('recentSearches', next)
      return next
    })
  }, [])
  const clear = useCallback(() => {
    setRecent([])
    void window.watchdog.prefs.set('recentSearches', [])
  }, [])
  return { recent, push, clear }
}

interface Opener {
  id: string
  open: () => void
}

export default function SearchView() {
  const vault = useVault()
  const route = useApp((s) => s.route)
  const routeQuery = route.view === 'search' ? route.query ?? '' : ''
  const [mode, setMode] = useState<Mode>('here')
  const [text, setText] = useState(routeQuery)
  const [submitted, setSubmitted] = useState(routeQuery)
  const [top, setTop] = useState(5)
  const [useThreshold, setUseThreshold] = useState(false)
  const [threshold, setThreshold] = useState(0.3)
  const [rerank, setRerank] = useState(true)
  const [full, setFull] = useState(false)
  const [showOptions, setShowOptions] = useState(false)
  const [showSyntax, setShowSyntax] = useState(false)
  const [sel, setSel] = useState(-1)
  const inputRef = useRef<HTMLInputElement>(null)
  const { recent, push, clear } = useRecent()
  const openLink = useOpenWikilink()

  // A route query (from the palette or another view) prefills and runs.
  useEffect(() => {
    if (routeQuery && routeQuery !== submitted) {
      setText(routeQuery)
      setSubmitted(routeQuery)
      setMode((m) => (m === 'batch' ? 'here' : m))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeQuery])

  useEffect(() => {
    if (mode !== 'batch') inputRef.current?.focus()
  }, [mode])

  const run = (q = text) => {
    const t = q.trim()
    if (!t) return
    setText(t)
    setSubmitted(t)
    setSel(-1)
    push(t)
  }

  const here = useRpc(
    'search.query',
    mode === 'here' && submitted && vault ? { vault, query: submitted, top, threshold: useThreshold ? threshold : null, rerank } : null,
    { staleTime: 60_000, placeholderData: (prev) => prev }
  )
  const everywhere = useRpc('search.everywhere', mode === 'everywhere' && submitted ? { query: submitted, top } : null, { staleTime: 60_000 })

  const terms = useMemo(() => queryTerms(submitted), [submitted])

  // ── openers for keyboard navigation ────────────────────────────────────────────────────────
  const openers: Opener[] = useMemo(() => {
    const list: Opener[] = []
    if (mode !== 'here' || !here.data) return list
    here.data.exact.forEach((r, i) => list.push({ id: `exact-${i}`, open: () => openExact(r, openLink) }))
    here.data.passages.forEach((r, i) => list.push({ id: `passage-${i}`, open: () => openPassage(r, openLink) }))
    here.data.notes.forEach((r, i) => list.push({ id: `note-${i}`, open: () => void openLink(r.note_path.replace(/\.md$/, '')) }))
    return list
  }, [mode, here.data, openLink])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!openers.length) return
      const inField = (e.target as HTMLElement)?.closest('input, textarea, select')
      const inSearchBox = e.target === inputRef.current
      if (inField && !inSearchBox) return
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSel((s) => Math.min(openers.length - 1, s + 1))
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSel((s) => Math.max(-1, s - 1))
      } else if (e.key === 'Enter' && sel >= 0 && text.trim() === submitted) {
        e.preventDefault()
        openers[sel]?.open()
      } else if (e.key === 'Escape' && sel >= 0) {
        setSel(-1)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [openers, sel, text, submitted])

  useEffect(() => {
    if (sel < 0) return
    document.querySelector(`[data-opener="${openers[sel]?.id}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [sel, openers])

  const idx = (id: string) => openers.findIndex((o) => o.id === id)

  return (
    <div className="page">
      <div className="page-inner srch">
        <div className="srch-head">
          <h1 className="page-title">Search</h1>
          <Segmented<Mode>
            value={mode}
            onChange={setMode}
            options={[
              { value: 'here', label: 'This investigation' },
              { value: 'everywhere', label: 'Every investigation' },
              { value: 'batch', label: 'Check a list of names' }
            ]}
          />
        </div>

        {mode !== 'batch' && (
          <>
            <form
              className="srch-box"
              onSubmit={(e) => {
                e.preventDefault()
                run()
              }}
            >
              <Search className="srch-box-icon" />
              <input
                ref={inputRef}
                className="srch-input"
                value={text}
                placeholder={mode === 'here' ? 'Search documents and notes by meaning or exact wording' : 'A name or phrase, searched in every investigation'}
                onChange={(e) => {
                  setText(e.target.value)
                  setSel(-1)
                }}
                spellCheck={false}
                autoFocus
              />
              {text && (
                <button
                  type="button"
                  className="srch-clear"
                  aria-label="Clear"
                  onClick={() => {
                    setText('')
                    inputRef.current?.focus()
                  }}
                >
                  <X />
                </button>
              )}
              <Button type="submit" variant="primary" disabled={!text.trim()}>
                Search
              </Button>
            </form>

            <div className="srch-tools">
              <div className="srch-syntax-wrap">
                <Button variant="ghost" size="sm" icon={Quote} onClick={() => setShowSyntax((v) => !v)}>
                  Search syntax
                </Button>
                {showSyntax && <SyntaxPopover onClose={() => setShowSyntax(false)} onTry={(q) => { setShowSyntax(false); setText(q); run(q) }} />}
              </div>
              {mode === 'here' && (
                <Button variant="ghost" size="sm" icon={SlidersHorizontal} onClick={() => setShowOptions((v) => !v)}>
                  Options
                </Button>
              )}
              <div className="spacer" />
              {mode === 'here' && here.data && !here.data.index_empty && <span className="faint srch-hint">↑ ↓ to move through results · Enter to open</span>}
            </div>

            {showOptions && mode === 'here' && (
              <div className="srch-options panel">
                <label className="srch-opt">
                  <span className="srch-opt-label">Results per section</span>
                  <Segmented<string>
                    value={String(top)}
                    onChange={(v) => setTop(Number(v))}
                    options={['3', '5', '10', '20'].map((v) => ({ value: v, label: v }))}
                  />
                </label>
                <div className="srch-opt">
                  <span className="srch-opt-label">Hide weak matches</span>
                  <div className="row">
                    <Switch checked={useThreshold} onChange={setUseThreshold} label="Hide weak matches" />
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={threshold}
                      disabled={!useThreshold}
                      onChange={(e) => setThreshold(Number(e.target.value))}
                      className="srch-range"
                    />
                    <span className="tnum faint srch-range-val">{useThreshold ? threshold.toFixed(2) : 'off'}</span>
                  </div>
                  <span className="faint srch-opt-note">Off by default, so a search always returns its top results. Turn it on to drop passages scoring below the line.</span>
                </div>
                <div className="srch-opt">
                  <span className="srch-opt-label">Re-rank passages</span>
                  <div className="row">
                    <Switch checked={rerank} onChange={setRerank} label="Re-rank passages" />
                    <span className="muted">{rerank ? 'On: a second local model re-orders the passages' : 'Off: faster, lower quality'}</span>
                  </div>
                </div>
                <div className="srch-opt">
                  <span className="srch-opt-label">Show full passages</span>
                  <div className="row">
                    <Switch checked={full} onChange={setFull} label="Show full passages" />
                    <span className="muted">{full ? 'Whole passage text' : 'A short excerpt around your terms'}</span>
                  </div>
                </div>
              </div>
            )}
          </>
        )}

        {mode === 'here' && (
          <HereResults
            submitted={submitted}
            query={here}
            terms={terms}
            full={full}
            sel={sel}
            idx={idx}
            setSel={setSel}
            recent={recent}
            clearRecent={clear}
            run={run}
            openLink={openLink}
          />
        )}
        {mode === 'everywhere' && <EverywhereResults submitted={submitted} query={everywhere} terms={terms} recent={recent} clearRecent={clear} run={run} />}
        {mode === 'batch' && <BatchMode />}
      </div>
    </div>
  )
}

// ── opening a hit ────────────────────────────────────────────────────────────────────────────
function openExact(r: SearchExact, openLink: (t: string) => Promise<void>) {
  if (r.kind === 'corpus' && r.sha) navigate({ view: 'document', sha: r.sha, page: r.page ?? undefined })
  else if (r.kind === 'corpus' && r.note) void openLink(r.note.replace(/\.md$/, ''))
  else if (r.path) void openLink(r.path.replace(/\.md$/, ''))
  else toast({ kind: 'info', title: 'Nothing to open for this match' })
}
function openPassage(r: SearchPassage, openLink: (t: string) => Promise<void>) {
  if (r.sha) navigate({ view: 'document', sha: r.sha, page: r.page ?? undefined })
  else if (r.note) void openLink(r.note.replace(/\.md$/, ''))
  else toast({ kind: 'info', title: 'Could not tell which document this passage came from', body: r.filename })
}

const KIND_LABEL: Record<string, string> = {
  corpus: 'Document',
  entity: 'Entity note',
  document: 'Document note',
  briefing: 'Briefing',
  query: 'Saved answer',
  wiki: 'Wiki page',
  timeline: 'Timeline',
  other: 'Note'
}

function LaneHead({ title, count, sub, tone }: { title: string; count?: number; sub: ReactNode; tone?: ReactNode }) {
  return (
    <div className="srch-lane-head">
      <div className="row">
        <h2 className="srch-lane-title">{title}</h2>
        {count !== undefined && <span className="faint tnum">{count}</span>}
        {tone}
      </div>
      <div className="faint srch-lane-sub">{sub}</div>
    </div>
  )
}

function Hit({ opener, active, onHover, onOpen, children, meta, title, kicker }: { opener: string; active: boolean; onHover: () => void; onOpen: () => void; children?: ReactNode; meta?: ReactNode; title: ReactNode; kicker?: ReactNode }) {
  return (
    <button type="button" data-opener={opener} className={cx('srch-hit', active && 'active')} onClick={onOpen} onMouseMove={onHover}>
      <div className="srch-hit-top">
        {kicker}
        <span className="srch-hit-title truncate">{title}</span>
        <span className="spacer" />
        {meta}
        <ArrowUpRight className="srch-hit-go" />
      </div>
      {children}
    </button>
  )
}

function HereResults({ submitted, query, terms, full, sel, idx, setSel, recent, clearRecent, run, openLink }: {
  submitted: string
  query: ReturnType<typeof useRpc<'search.query'>>
  terms: string[]
  full: boolean
  sel: number
  idx: (id: string) => number
  setSel: (n: number) => void
  recent: string[]
  clearRecent: () => void
  run: (q: string) => void
  openLink: (t: string) => Promise<void>
}) {
  const vault = useVault()
  const [rebuilding, setRebuilding] = useState(false)
  const engine = useEngineGate()
  const status = useRpc('search.status', vault ? { vault } : null)

  if (!submitted) return <Start recent={recent} clearRecent={clearRecent} run={run} status={status.data} />
  if (query.isLoading || (query.isFetching && !query.data)) return <ResultsSkeleton />
  if (query.error) return <ErrorNote error={query.error} retry={() => void query.refetch()} />
  const d = query.data
  if (!d) return null

  if (d.index_empty) {
    return (
      <Empty
        icon={SearchX}
        title="The search index is empty"
        action={
          <Button
            variant="primary"
            loading={rebuilding}
            disabled={!engine.ready}
            onClick={async () => {
              setRebuilding(true)
              try {
                await startJob(['reindex'], 'Rebuild search index')
                toast({ kind: 'info', title: 'Rebuilding the search index', body: 'Follow progress in Activity. Search again when it finishes.' })
              } catch (e) {
                toast({ kind: 'error', title: 'Could not start the rebuild', body: errorMessage(e) })
              } finally {
                setRebuilding(false)
              }
            }}
          >
            Rebuild index
          </Button>
        }
      >
        Search indexes are built automatically while documents are ingested. This investigation has none yet: either nothing has been through <b>Add documents</b>, or the index was removed. If documents are already here, rebuilding reads them from disk and takes no model calls.
      </Empty>
    )
  }

  const nothing = !d.exact.length && !d.passages.length && !d.notes.length
  return (
    <div className={cx('srch-results', query.isFetching && 'fetching')}>
      {d.exact_error && (
        <Callout tone="warning" title="The exact-match lane was not checked">
          Exact matches could not be searched ({d.exact_error}). Treat the absence of exact matches below as unknown, not as “none”. Rebuilding the index usually fixes this.
        </Callout>
      )}
      {d.semantic_pending && (
        <div className="faint srch-hint" style={{ marginTop: -14 }}>
          Searching by meaning becomes available when Watchdog finishes setting up, in a few minutes. Until then only exact matches are shown.
        </div>
      )}
      {d.semantic_error && (
        <div className="faint srch-hint" style={{ marginTop: -14 }}>
          Ranking by meaning was unavailable ({d.semantic_error}), so only exact matches are shown.
        </div>
      )}
      {nothing && !d.exact_error && (
        <Empty icon={SearchX} title={`No results for “${d.query}”`}>
          Nothing in the documents or notes matched, by wording or by meaning. Try fewer words, or remove the threshold in Options.
        </Empty>
      )}

      {(d.exact.length > 0 || d.exact_error) && (
        <section className="srch-lane">
          <LaneHead title="Exact matches" count={d.exact_error ? undefined : d.exact.length} sub="Every literal occurrence of your words, from the full-text index. Unscored, in no ranked order." />
          {d.exact.map((r, i) => {
            const id = `exact-${i}`
            const corpus = r.kind === 'corpus'
            return (
              <Hit
                key={id}
                opener={id}
                active={sel === idx(id)}
                onHover={() => idx(id) !== sel && setSel(idx(id))}
                onOpen={() => openExact(r, openLink)}
                kicker={corpus ? <FileText className="srch-hit-icon" /> : <Badge>{KIND_LABEL[r.kind] ?? r.kind}</Badge>}
                title={corpus ? r.title ?? r.path ?? 'Untitled document' : r.title ?? noteTitle(r.path ?? '')}
                meta={corpus && r.page ? <span className="srch-page">p. {r.page}</span> : !corpus && r.path ? <span className="faint srch-path">{prettyPath(r.path).slice(0, -1).join(' / ')}</span> : null}
              >
                <p className="srch-snippet">
                  <Highlight text={full ? r.text.replace(/\s+/g, ' ') : windowed(r.text, terms, 240)} terms={terms} />
                </p>
              </Hit>
            )
          })}
        </section>
      )}

      {d.passages.length > 0 && (
        <section className="srch-lane">
          <LaneHead title="Source passages" count={d.passages.length} sub="Pieces of your documents closest in meaning to the query, best first. The bar shows relative score." />
          {d.passages.map((r, i) => {
            const id = `passage-${i}`
            return (
              <Hit
                key={id}
                opener={id}
                active={sel === idx(id)}
                onHover={() => idx(id) !== sel && setSel(idx(id))}
                onOpen={() => openPassage(r, openLink)}
                kicker={<FileText className="srch-hit-icon" />}
                title={r.filename}
                meta={
                  <>
                    {r.page ? <span className="srch-page">p. {r.page}</span> : null}
                    <ScoreBar score={r.score} />
                  </>
                }
              >
                <p className="srch-snippet">
                  <Highlight text={full ? r.text.replace(/\s+/g, ' ') : windowed(r.text, terms, 240)} terms={terms} />
                </p>
              </Hit>
            )
          })}
        </section>
      )}

      {d.notes.length > 0 && (
        <section className="srch-lane">
          <LaneHead title="Notes" count={d.notes.length} sub="What the investigation has concluded so far: entity pages, document notes, saved answers." />
          {d.notes.map((r: SearchNote, i) => {
            const id = `note-${i}`
            const parts = prettyPath(r.note_path)
            return (
              <Hit
                key={id}
                opener={id}
                active={sel === idx(id)}
                onHover={() => idx(id) !== sel && setSel(idx(id))}
                onOpen={() => void openLink(r.note_path.replace(/\.md$/, ''))}
                kicker={<Sparkles className="srch-hit-icon" />}
                title={noteTitle(r.note_path)}
                meta={
                  <>
                    <span className="faint srch-path">{parts.slice(0, -1).join(' / ')}</span>
                    <ScoreBar score={r.score} />
                  </>
                }
              >
                <p className="srch-snippet">
                  <Highlight text={full ? r.preview.replace(/\s+/g, ' ') : windowed(r.preview, terms, 200)} terms={terms} />
                </p>
              </Hit>
            )
          })}
        </section>
      )}
    </div>
  )
}

function ScoreBar({ score }: { score: number }) {
  const pct = Math.max(0, Math.min(1, score)) * 100
  return (
    <span className="srch-score" data-tip={`Score ${score.toFixed(2)}`} data-tip-pos="bottom">
      <span style={{ width: `${pct}%` }} />
    </span>
  )
}

function ResultsSkeleton() {
  return (
    <div className="srch-results">
      {[0, 1, 2].map((i) => (
        <div key={i} className="srch-lane">
          <Skeleton w={140} h={16} />
          {[0, 1].map((j) => (
            <div key={j} className="srch-hit" style={{ cursor: 'default' }}>
              <Skeleton w="40%" h={14} />
              <Skeleton h={12} style={{ marginTop: 10 }} />
              <Skeleton w="85%" h={12} style={{ marginTop: 6 }} />
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

function Start({ recent, clearRecent, run, status }: { recent: string[]; clearRecent: () => void; run: (q: string) => void; status?: { total: number; documents: number; notes: number } }) {
  return (
    <div className="srch-start">
      {recent.length > 0 && (
        <div>
          <div className="row" style={{ marginBottom: 8 }}>
            <History className="srch-mini-icon" />
            <span className="eyebrow">Recent searches</span>
            <span className="spacer" />
            <button className="srch-link" onClick={clearRecent}>
              Clear
            </button>
          </div>
          <div className="srch-chips">
            {recent.map((r) => (
              <button key={r} className="srch-chip" onClick={() => run(r)}>
                {r}
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="srch-explain">
        <div>
          <h3>Three kinds of result, kept apart</h3>
          <p className="muted">
            <b>Exact matches</b> are every place your words appear literally. <b>Source passages</b> are the parts of your documents that mean the same thing, even in different words. <b>Notes</b> are what the investigation has already concluded.
          </p>
        </div>
        <div>
          <h3>Steer with phrases</h3>
          <p className="muted">
            Lead a phrase with <code>+</code> to pull towards it or <code>-</code> to push away, and put <code>&quot;quotes&quot;</code> around words that must appear together.
          </p>
        </div>
        {status && status.total > 0 && (
          <div>
            <h3>What is searchable</h3>
            <p className="muted">
              {plural(status.documents, 'document passage')} and {plural(status.notes, 'note')} are indexed.
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

function SyntaxPopover({ onClose, onTry }: { onClose: () => void; onTry: (q: string) => void }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const down = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose()
    }
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    setTimeout(() => window.addEventListener('mousedown', down))
    window.addEventListener('keydown', esc)
    return () => {
      window.removeEventListener('mousedown', down)
      window.removeEventListener('keydown', esc)
    }
  }, [onClose])
  const rows: [string, string, string][] = [
    ['consulting fee', 'Plain words', 'Finds passages about the idea, and every literal occurrence.'],
    ['consulting fee +offshore', '+ pulls towards', 'Everything after a + up to the next sign is one phrase to move closer to.'],
    ['consulting fee -salary', '- pushes away', 'Move results away from that idea. It does not hide pages that mention it.'],
    ['"Harbour Point Holdings"', '“Quoted” is exact', 'Words that must appear together, in that order, in the exact-match lane.']
  ]
  return (
    <div className="srch-pop" ref={ref} role="dialog">
      <div className="srch-pop-title">Steering a search</div>
      {rows.map(([ex, name, desc]) => (
        <button key={ex} className="srch-pop-row" onClick={() => onTry(ex)}>
          <code>{ex}</code>
          <span className="srch-pop-name">{name}</span>
          <span className="faint">{desc}</span>
        </button>
      ))}
      <div className="faint srch-pop-foot">Click an example to try it. No quotes are needed around + and - phrases.</div>
    </div>
  )
}

// ── every investigation ──────────────────────────────────────────────────────────────────────
function EverywhereResults({ submitted, query, terms, recent, clearRecent, run }: { submitted: string; query: ReturnType<typeof useRpc<'search.everywhere'>>; terms: string[]; recent: string[]; clearRecent: () => void; run: (q: string) => void }) {
  const openLink = useOpenWikilink()
  return (
    <>
      <Callout tone="info" title="Names and exact wording only">
        Across investigations Watchdog checks entity names and every literal occurrence in the documents. Ranking by meaning does not run here: it would mean embedding your query against every investigation&apos;s index, which is slow and returns scores that cannot be compared between investigations.
      </Callout>
      <div style={{ height: 16 }} />
      {!submitted ? (
        <Start recent={recent} clearRecent={clearRecent} run={run} />
      ) : query.isLoading ? (
        <ResultsSkeleton />
      ) : query.error ? (
        <ErrorNote error={query.error} retry={() => void query.refetch()} />
      ) : query.data ? (
        <EverywhereList data={query.data} terms={terms} openLink={openLink} query={submitted} />
      ) : null}
    </>
  )
}

async function openInvestigation(slug: string, query: string) {
  try {
    const p = await call('projects.get', { slug })
    useApp.getState().setProject(p)
    navigate({ view: 'search', query })
  } catch (e) {
    toast({ kind: 'error', title: 'Could not open that investigation', body: errorMessage(e) })
  }
}

function EverywhereList({ data, terms, query }: { data: EverywhereResult; terms: string[]; openLink: (t: string) => Promise<void>; query: string }) {
  const hits = data.vaults.filter((v) => v.entity_hits.length || v.exact.length)
  return (
    <div className="srch-results">
      {hits.length === 0 ? (
        <Empty icon={SearchX} title="No matches in any investigation">
          Checked {plural(data.vaults.length, 'investigation')}. A name that appears nowhere here has not been seen in any of your other work.
        </Empty>
      ) : (
        hits.map((v) => (
          <section key={v.slug} className="srch-lane">
            <div className="srch-lane-head">
              <div className="row">
                <h2 className="srch-lane-title">{v.name}</h2>
                <span className="faint mono">{v.slug}</span>
                <span className="spacer" />
                <Button size="sm" iconRight={ArrowUpRight} onClick={() => void openInvestigation(v.slug, query)}>
                  Open and search
                </Button>
              </div>
              <div className="faint srch-lane-sub">
                {[v.entity_hits.length ? plural(v.entity_hits.length, 'entity', 'entities') : '', v.exact.length ? plural(v.exact.length, 'exact match', 'exact matches') : ''].filter(Boolean).join(' · ')}
              </div>
            </div>
            {v.entity_hits.map((e) => (
              <div key={e.id} className="srch-hit static">
                <div className="srch-hit-top">
                  <Badge>Entity</Badge>
                  <span className="srch-hit-title">{e.name}</span>
                  <span className="faint">{e.type}</span>
                </div>
              </div>
            ))}
            {v.exact.map((r, i) => (
              <div key={i} className="srch-hit static">
                <div className="srch-hit-top">
                  {r.kind === 'corpus' ? <FileText className="srch-hit-icon" /> : <Badge>{KIND_LABEL[r.kind] ?? r.kind}</Badge>}
                  <span className="srch-hit-title truncate">{r.title ?? r.path}</span>
                  <span className="spacer" />
                  {r.page ? <span className="srch-page">p. {r.page}</span> : null}
                </div>
                <p className="srch-snippet">
                  <Highlight text={windowed(r.text, terms, 240)} terms={terms} />
                </p>
              </div>
            ))}
          </section>
        ))
      )}
      {data.skipped.length > 0 && (
        <Callout tone="warning" title={`${plural(data.skipped.length, 'investigation')} skipped`}>
          {data.skipped.map((s) => `${s.slug}: ${s.reason}`).join(' · ')}. Use Check vaults under Settings to repair a broken folder path.
        </Callout>
      )}
    </div>
  )
}

// ── check a list of names ────────────────────────────────────────────────────────────────────
type BatchFilter = 'all' | 'hits' | 'none' | 'unchecked'

function BatchMode() {
  const vault = useVault()
  const openLink = useOpenWikilink()
  const [raw, setRaw] = useState('')
  const [everywhere, setEverywhere] = useState(false)
  const [result, setResult] = useState<BatchResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [filter, setFilter] = useState<BatchFilter>('all')
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const fileRef = useRef<HTMLInputElement>(null)

  const terms = useMemo(() => [...new Set(raw.split(/\r?\n/).map((l) => l.trim()).filter((l) => l && !l.startsWith('#')))], [raw])

  const loadFile = (f: File) => {
    const r = new FileReader()
    r.onload = () => setRaw(String(r.result ?? ''))
    r.onerror = () => toast({ kind: 'error', title: 'Could not read that file' })
    r.readAsText(f)
  }

  const check = async () => {
    setBusy(true)
    setErr(null)
    try {
      setResult(await call('search.batch', { vault: everywhere ? null : vault, terms, everywhere }))
      setOpen({})
      setFilter('all')
    } catch (e) {
      setErr(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const counts = useMemo(() => {
    const t = result?.terms ?? []
    return {
      all: t.length,
      hits: t.filter((x) => x.checked && x.hits.length).length,
      none: t.filter((x) => x.checked && !x.hits.length).length,
      unchecked: t.filter((x) => !x.checked).length
    }
  }, [result])

  const shown = (result?.terms ?? []).filter((t) => (filter === 'all' ? true : filter === 'hits' ? t.checked && t.hits.length : filter === 'none' ? t.checked && !t.hits.length : !t.checked))

  return (
    <div className="srch-batch">
      <Callout tone="info" title="For a leaked roster, donor list or sanctions list">
        Each name is checked against entity names and aliases and every literal occurrence in the documents. A name with no hits has genuinely not been found. A name marked <b>not checked</b> could not be searched, which is different from finding nothing.
      </Callout>
      <div className="srch-batch-grid">
        <div className="col">
          <div className="row">
            <span className="field-label">Names, one per line</span>
            <span className="spacer" />
            <input
              ref={fileRef}
              type="file"
              accept=".txt,.csv,.tsv,.md,text/*"
              hidden
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) loadFile(f)
                e.target.value = ''
              }}
            />
            <Button size="sm" variant="ghost" icon={FileUp} onClick={() => fileRef.current?.click()}>
              Load from a file
            </Button>
          </div>
          <textarea
            className="textarea srch-batch-text"
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            placeholder={'Harbour Point Holdings\nJane Whitcombe\n14 Quay Street'}
            spellCheck={false}
          />
          <span className="faint" style={{ fontSize: 'var(--fs-sm)' }}>{terms.length ? plural(terms.length, 'name') : 'Blank lines and lines starting with # are ignored.'}</span>
          <div className="row wrap">
            <label className="row" style={{ gap: 8 }}>
              <Switch checked={everywhere} onChange={setEverywhere} label="Search every investigation" />
              <span className="muted">Every investigation</span>
            </label>
            <span className="spacer" />
            <Button variant="primary" icon={ListChecks} disabled={!terms.length} loading={busy} onClick={() => void check()}>
              Check {terms.length ? plural(terms.length, 'name') : 'names'}
            </Button>
          </div>
        </div>
        <div className="col">
          {err && <ErrorNote error={new Error(err)} retry={() => void check()} />}
          {busy && !result && <ResultsSkeleton />}
          {!result && !busy && !err && (
            <Empty icon={ListChecks} title="No list checked yet">
              Paste names on the left, or load a text file with one name per line, then check them.
            </Empty>
          )}
          {result && (
            <>
              <div className="srch-batch-summary">
                <Segmented<BatchFilter>
                  value={filter}
                  onChange={setFilter}
                  options={[
                    { value: 'all', label: `All ${counts.all}` },
                    { value: 'hits', label: `With hits ${counts.hits}` },
                    { value: 'none', label: `No hits ${counts.none}` },
                    ...(counts.unchecked ? [{ value: 'unchecked' as BatchFilter, label: `Not checked ${counts.unchecked}` }] : [])
                  ]}
                />
              </div>
              {counts.unchecked > 0 && (
                <Callout tone="warning" title={`${plural(counts.unchecked, 'name')} not checked`}>
                  The exact-match search failed for these, so they were not compared with the document text. Rebuild the search index under Activity → Maintenance (open the investigation first) and check again.
                </Callout>
              )}
              <div className="srch-batch-list">
                {shown.map((t) => {
                  const isOpen = open[t.term]
                  const n = t.hits.length
                  return (
                    <div key={t.term} className={cx('srch-term', !t.checked && 'unchecked')}>
                      <button className="srch-term-row" onClick={() => n && setOpen({ ...open, [t.term]: !isOpen })} disabled={!n}>
                        <span className="srch-term-name truncate">{t.term}</span>
                        <span className="spacer" />
                        {!t.checked ? (
                          <Badge tone="warning">Not checked</Badge>
                        ) : n ? (
                          <Badge tone="accent">{plural(n, 'hit')}</Badge>
                        ) : (
                          <span className="faint">No hits</span>
                        )}
                      </button>
                      {isOpen && (
                        <div className="srch-term-hits">
                          {t.hits.map((h, i) => (
                            <button key={i} className="srch-term-hit" onClick={() => h.note && void openLink(h.note.replace(/\.md$/, ''))} disabled={!h.note}>
                              <div className="row">
                                {h.vault_name && <Badge>{h.vault_name}</Badge>}
                                <span className="faint srch-kind">{KIND_LABEL[h.kind] ?? h.kind}</span>
                                <span className="srch-hit-title truncate">{h.title ?? h.note ?? 'Untitled'}</span>
                                <span className="spacer" />
                                {h.page ? <span className="srch-page">p. {h.page}</span> : null}
                              </div>
                              {h.text && (
                                <p className="srch-snippet">
                                  <Highlight text={windowed(h.text, [t.term], 200)} terms={[t.term]} />
                                </p>
                              )}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  )
                })}
                {!shown.length && <div className="faint" style={{ padding: 16 }}>No names in this group.</div>}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
