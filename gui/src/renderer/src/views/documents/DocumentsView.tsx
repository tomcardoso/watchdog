// The document library: every document in the vault as a grid of page thumbnails or a dense list,
// with filtering, sorting, facets, and the pipeline strip for work that hasn't reached the vault.

import { useVirtualizer } from '@tanstack/react-virtual'
import { ArrowDown, ArrowUp, Copy, FilePlus2, FileText, LayoutGrid, List as ListIcon, Search, X } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { DocThumb } from '@renderer/components/DocThumb'
import { Button, Empty, ErrorNote, Segmented, Skeleton, useDebounced } from '@renderer/components/ui'
import { fmtDate, fmtNum, plural } from '@renderer/lib/format'
import { useRpc } from '@renderer/lib/rpc'
import { navigate, useApp, useVault } from '@renderer/lib/store'
import type { DocumentRow } from '@shared/api'
import { PipelineStrip } from './PipelineStrip'
import './documents.css'

type Layout = 'grid' | 'list'
type SortKey = 'ingested' | 'docdate' | 'title' | 'pages' | 'entities'

const SORTS: { value: SortKey; label: string; dir: 1 | -1 }[] = [
  { value: 'ingested', label: 'Date ingested', dir: -1 },
  { value: 'docdate', label: 'Document date', dir: -1 },
  { value: 'title', label: 'Title', dir: 1 },
  { value: 'pages', label: 'Pages', dir: -1 },
  { value: 'entities', label: 'Entities', dir: -1 }
]
const VIRTUALIZE_AFTER = 150
const MIN_CARD = 196
const GAP = 20

const titleOf = (d: DocumentRow) => d.title || d.filename

function compare(a: DocumentRow, b: DocumentRow, key: SortKey, dir: 1 | -1): number {
  const nulls = (x: unknown, y: unknown): number | null => {
    const xn = x === null || x === undefined || x === ''
    const yn = y === null || y === undefined || y === ''
    if (xn && yn) return 0
    if (xn) return 1 // missing values always sort last
    if (yn) return -1
    return null
  }
  let x: string | number | null, y: string | number | null
  switch (key) {
    case 'ingested': x = a.ingested_at; y = b.ingested_at; break
    case 'docdate': x = a.date_of_document; y = b.date_of_document; break
    case 'pages': x = a.page_count; y = b.page_count; break
    case 'entities': x = a.entity_count; y = b.entity_count; break
    default: return titleOf(a).localeCompare(titleOf(b), 'en-CA', { sensitivity: 'base', numeric: true }) * dir
  }
  const n = nulls(x, y)
  if (n !== null) return n
  const r = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y))
  return r * dir
}

function DupBadge() {
  return (
    <span className="docs-card-dup" data-tip="Looks like another document in this vault" data-tip-pos="bottom">
      <Copy />
      Possible duplicate
    </span>
  )
}

function Card({ d }: { d: DocumentRow }) {
  const open = () => navigate({ view: 'document', sha: d.sha })
  return (
    <button className="docs-card" onClick={open} title={d.filename}>
      <div className="docs-card-thumb">
        <DocThumb sha={d.sha} ext={d.ext} original={d.original} title={titleOf(d)} summary={d.summary} />
        {d.near_duplicate_of && <DupBadge />}
        {!!d.page_count && d.page_count > 1 && <span className="docs-card-pages">{d.page_count} pp</span>}
      </div>
      <div className="docs-card-title">{titleOf(d)}</div>
      <div className="docs-card-meta">
        {d.document_type && <span className="type">{d.document_type}</span>}
        {d.date_of_document && <span>{fmtDate(d.date_of_document)}</span>}
        {d.entity_count > 0 && <span>{plural(d.entity_count, 'entity', 'entities')}</span>}
      </div>
    </button>
  )
}

function ListRow({ d }: { d: DocumentRow }) {
  return (
    <button className="docs-list-row" onClick={() => navigate({ view: 'document', sha: d.sha })}>
      <DocThumb sha={d.sha} ext={d.ext} original={d.original} title={titleOf(d)} summary={null} width={60} />
      <div className="docs-list-title">
        <span className="t truncate">{titleOf(d)}</span>
        <span className="f truncate">
          {d.title ? d.filename : d.ext.toUpperCase()}
          {d.near_duplicate_of ? ' · possible duplicate' : ''}
        </span>
      </div>
      <span className="cell truncate">{d.document_type ?? ''}</span>
      <span className="cell truncate">{fmtDate(d.date_of_document)}</span>
      <span className="cell num tnum">{d.page_count ?? ''}</span>
      <span className="cell num tnum">{d.entity_count || ''}</span>
      <span className="cell truncate">{fmtDate(d.ingested_at)}</span>
    </button>
  )
}

export default function DocumentsView() {
  const vault = useVault()
  const route = useApp((s) => s.route)
  const projectName = useApp((s) => s.project?.name ?? 'this investigation')
  const openAdd = useApp((s) => s.openAdd)
  const docsQ = useRpc('vault.documents', { vault })
  const pipeQ = useRpc('vault.pipeline', { vault })

  const [layout, setLayout] = useState<Layout>('grid')
  const [filter, setFilter] = useState(route.view === 'documents' ? route.filter ?? '' : '')
  const [sort, setSort] = useState<SortKey>('ingested')
  const [dir, setDir] = useState<1 | -1>(-1)
  const [type, setType] = useState('')
  const [skill, setSkill] = useState('')
  const [fileType, setFileType] = useState('')
  const [dupOnly, setDupOnly] = useState(false)
  const [stuck, setStuck] = useState(false)
  const q = useDebounced(filter, 120)
  const filterRef = useRef<HTMLInputElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    window.watchdog.prefs.get<Layout>('docsLayout').then((v) => v && setLayout(v)).catch(() => undefined)
  }, [])
  useEffect(() => {
    if (route.view === 'documents' && route.filter !== undefined) setFilter(route.filter)
  }, [route])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(t.tagName) && !t.isContentEditable) {
        e.preventDefault()
        filterRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const changeLayout = (l: Layout) => {
    setLayout(l)
    void window.watchdog.prefs.set('docsLayout', l)
  }
  const changeSort = (k: SortKey) => {
    if (k === sort) setDir((d) => (d === 1 ? -1 : 1))
    else {
      setSort(k)
      setDir(SORTS.find((s) => s.value === k)!.dir)
    }
  }

  const docs = docsQ.data
  const facets = useMemo(() => {
    const uniq = (f: (d: DocumentRow) => string | null) =>
      [...new Set((docs ?? []).map(f).filter((v): v is string => !!v))].sort((a, b) => a.localeCompare(b))
    return { types: uniq((d) => d.document_type), skills: uniq((d) => d.record_skill), exts: uniq((d) => d.ext) }
  }, [docs])
  const dupCount = useMemo(() => (docs ?? []).filter((d) => d.near_duplicate_of).length, [docs])

  const rows = useMemo(() => {
    if (!docs) return []
    const needle = q.trim().toLowerCase()
    const out = docs.filter((d) => {
      if (type && d.document_type !== type) return false
      if (skill && d.record_skill !== skill) return false
      if (fileType && d.ext !== fileType) return false
      if (dupOnly && !d.near_duplicate_of) return false
      if (!needle) return true
      return [d.title, d.filename, d.document_type, d.record_skill].some((v) => v?.toLowerCase().includes(needle))
    })
    return out.sort((a, b) => compare(a, b, sort, dir) || a.filename.localeCompare(b.filename))
  }, [docs, q, type, skill, fileType, dupOnly, sort, dir])

  const filtered = !!(q || type || skill || fileType || dupOnly)
  const clear = () => {
    setFilter('')
    setType('')
    setSkill('')
    setFileType('')
    setDupOnly(false)
  }

  // ── virtualisation (only for big vaults) ───────────────────────────────────
  const virtual = rows.length > VIRTUALIZE_AFTER
  const [width, setWidth] = useState(1000)
  const [margin, setMargin] = useState(0)
  useLayoutEffect(() => {
    const el = listRef.current
    if (!el) return
    const ro = new ResizeObserver(() => {
      setWidth(el.clientWidth)
      const sc = scrollRef.current
      if (sc) setMargin(el.getBoundingClientRect().top - sc.getBoundingClientRect().top + sc.scrollTop)
    })
    ro.observe(el)
    return () => ro.disconnect()
  })
  const cols = layout === 'grid' ? Math.max(1, Math.floor((width + GAP) / (MIN_CARD + GAP))) : 1
  const cardW = (width - GAP * (cols - 1)) / cols
  const lines = Math.ceil(rows.length / cols)
  const estimate = layout === 'grid' ? Math.round((cardW * 11) / 8.5 + 84 + 22) : 56
  const virt = useVirtualizer({
    count: virtual ? lines : 0,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => estimate,
    overscan: layout === 'grid' ? 2 : 12,
    scrollMargin: margin
  })
  useEffect(() => virt.measure(), [cols, layout, virt])

  const onScroll = useCallback(() => setStuck((scrollRef.current?.scrollTop ?? 0) > 6), [])

  const pipe = pipeQ.data
  const pipeHasWork = !!pipe && (pipe.incoming.length + pipe.queued.length + pipe.failed.length + pipe.chew_failed.length + pipe.skipped.length > 0 || pipe.locks.chew || pipe.locks.ingest || !!pipe.pending_finalization)

  const sortHeader = (k: SortKey, label: string, num?: boolean) => (
    <button className={num ? 'num' : undefined} aria-pressed={sort === k} onClick={() => changeSort(k)}>
      {label}
      {sort === k && (dir === 1 ? <ArrowUp /> : <ArrowDown />)}
    </button>
  )

  return (
    <div className="docs-page" ref={scrollRef} onScroll={onScroll}>
      <div className="docs-inner">
        <div className="docs-head">
          <h1 className="page-title">Documents</h1>
          {docs && (
            <span className="docs-count tnum">
              {filtered ? `${fmtNum(rows.length)} of ${plural(docs.length, 'document')}` : plural(docs.length, 'document')}
            </span>
          )}
        </div>

        {pipe && pipeHasWork && <PipelineStrip pipeline={pipe} vaultName={projectName} />}

        {docsQ.isError ? (
          <ErrorNote error={docsQ.error} retry={() => docsQ.refetch()} />
        ) : docsQ.isLoading ? (
          <div className="docs-skeleton-grid">
            {Array.from({ length: 12 }).map((_, i) => (
              <div key={i} className="col">
                <Skeleton h={0} style={{ aspectRatio: '8.5 / 11', height: 'auto', borderRadius: 6 }} />
                <Skeleton w="80%" h={14} />
                <Skeleton w="50%" h={11} />
              </div>
            ))}
          </div>
        ) : docs && docs.length === 0 ? (
          <Empty
            icon={FileText}
            title="No documents in the vault yet"
            action={
              <Button variant="primary" icon={FilePlus2} onClick={() => openAdd()}>
                Add documents
              </Button>
            }
          >
            Documents appear here once they have been read, extracted and written to the vault. Add PDFs, scans, Word files, spreadsheets or web pages, and each one gets a note with its facts and the people and organizations it names.
          </Empty>
        ) : (
          <>
            <div className={'docs-toolbar' + (stuck ? ' stuck' : '')}>
              <div className="docs-toolbar-row">
                <div className="input-group docs-filter">
                  <Search />
                  <input
                    ref={filterRef}
                    className="input"
                    placeholder="Filter by title, file name, type or skill"
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                    onKeyDown={(e) => e.key === 'Escape' && (setFilter(''), filterRef.current?.blur())}
                    style={{ userSelect: 'text' }}
                  />
                  {filter && (
                    <button className="btn btn-ghost btn-sm btn-icon" style={{ marginRight: 2 }} aria-label="Clear filter" onClick={() => setFilter('')}>
                      <X />
                    </button>
                  )}
                </div>
                <select className="select" style={{ width: 'auto', height: 32 }} value={sort} onChange={(e) => changeSort(e.target.value as SortKey)} aria-label="Sort by">
                  {SORTS.map((s) => (
                    <option key={s.value} value={s.value}>
                      Sort: {s.label}
                    </option>
                  ))}
                </select>
                <Button size="sm" variant="ghost" icon={dir === 1 ? ArrowUp : ArrowDown} tip={dir === 1 ? 'Ascending' : 'Descending'} onClick={() => setDir((d) => (d === 1 ? -1 : 1))} />
                <span className="spacer" />
                <Segmented
                  value={layout}
                  onChange={changeLayout}
                  options={[
                    { value: 'grid', icon: LayoutGrid, label: 'Grid', tip: 'Thumbnails' },
                    { value: 'list', icon: ListIcon, label: 'List', tip: 'Dense table' }
                  ]}
                />
              </div>
              <div className="docs-facets">
                <select className={'select' + (type ? ' active' : '')} value={type} onChange={(e) => setType(e.target.value)} aria-label="Document type">
                  <option value="">All document types</option>
                  {facets.types.map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
                <select className={'select' + (skill ? ' active' : '')} value={skill} onChange={(e) => setSkill(e.target.value)} aria-label="Record skill">
                  <option value="">All record skills</option>
                  {facets.skills.map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
                <select className={'select' + (fileType ? ' active' : '')} value={fileType} onChange={(e) => setFileType(e.target.value)} aria-label="File type">
                  <option value="">All file types</option>
                  {facets.exts.map((t) => (
                    <option key={t} value={t}>
                      {t.toUpperCase()}
                    </option>
                  ))}
                </select>
                <button className="docs-toggle" aria-pressed={dupOnly} onClick={() => setDupOnly((v) => !v)} disabled={!dupCount && !dupOnly} style={!dupCount && !dupOnly ? { opacity: 0.5, cursor: 'default' } : undefined}>
                  <Copy />
                  Possible duplicates only{dupCount ? ` (${dupCount})` : ''}
                </button>
                {filtered && (
                  <Button size="sm" variant="ghost" onClick={clear}>
                    Clear filters
                  </Button>
                )}
              </div>
            </div>

            {rows.length === 0 ? (
              <div style={{ marginTop: 24 }}>
                <Empty
                  icon={Search}
                  title="No documents match"
                  action={
                    <Button onClick={clear}>
                      Clear filters
                    </Button>
                  }
                >
                  Nothing in the vault matches those filters. The filter box searches titles, file names, document types and record skills.
                </Empty>
              </div>
            ) : layout === 'grid' ? (
              virtual ? (
                <div ref={listRef} style={{ position: 'relative', height: virt.getTotalSize(), marginTop: 14 }}>
                  {virt.getVirtualItems().map((vi) => (
                    <div
                      key={vi.key}
                      ref={virt.measureElement}
                      data-index={vi.index}
                      className="docs-vrow"
                      style={{ transform: `translateY(${vi.start - virt.options.scrollMargin}px)`, gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, paddingBottom: 22 }}
                    >
                      {rows.slice(vi.index * cols, vi.index * cols + cols).map((d) => (
                        <Card key={d.sha} d={d} />
                      ))}
                    </div>
                  ))}
                </div>
              ) : (
                <div ref={listRef} className="docs-grid" style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${MIN_CARD}px, 1fr))` }}>
                  {rows.map((d) => (
                    <Card key={d.sha} d={d} />
                  ))}
                </div>
              )
            ) : (
              <div className="docs-list" role="table" aria-label="Documents">
                <div className="docs-list-cols" role="row">
                  <span />
                  {sortHeader('title', 'Title')}
                  <span>Type</span>
                  {sortHeader('docdate', 'Document date')}
                  {sortHeader('pages', 'Pages', true)}
                  {sortHeader('entities', 'Entities', true)}
                  {sortHeader('ingested', 'Ingested')}
                </div>
                {virtual ? (
                  <div ref={listRef} style={{ position: 'relative', height: virt.getTotalSize() }}>
                    {virt.getVirtualItems().map((vi) => (
                      <div key={vi.key} className="docs-list-vrow" style={{ height: vi.size, transform: `translateY(${vi.start - virt.options.scrollMargin}px)` }}>
                        <ListRow d={rows[vi.index]} />
                      </div>
                    ))}
                  </div>
                ) : (
                  <div ref={listRef}>
                    {rows.map((d) => (
                      <ListRow key={d.sha} d={d} />
                    ))}
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
