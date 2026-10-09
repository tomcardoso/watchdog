import { useVirtualizer } from '@tanstack/react-virtual'
import { AddDocumentsButton } from '@renderer/components/EngineWait'
import { CalendarClock, FileText, MoreHorizontal, RefreshCw, Search, Users, X } from 'lucide-react'
import { CSSProperties, useEffect, useMemo, useRef, useState } from 'react'
import { EntityAvatar, EntityChip } from '@renderer/components/EntityChip'
import { DisputedBadge } from '@renderer/components/FactCheck'
import { Button, Dropdown, Empty, ErrorNote, Modal, Skeleton, useDebounced } from '@renderer/components/ui'
import { ENTITY_TYPES, TimelineEvent } from '@shared/api'
import { TYPE_META, typeMeta } from '@renderer/lib/entityTypes'
import { fmtDate, fmtNum, plural } from '@renderer/lib/format'
import { startJob } from '@renderer/lib/jobs'
import { errorMessage, useRpc } from '@renderer/lib/rpc'
import { navigate, toast, useApp, useVault } from '@renderer/lib/store'
import './timeline.css'

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']

type Item =
  | { k: 'year'; year: string; count: number }
  | { k: 'month'; label: string }
  | { k: 'event'; ev: TimelineEvent }

const yearOf = (d: string) => (/^\d{4}/.test(d) ? d.slice(0, 4) : '')
const monthOf = (d: string) => {
  const m = /^\d{4}-(\d{2})/.exec(d)
  return m ? Number(m[1]) : 0
}
const precisionOf = (ev: TimelineEvent) => ev.precision ?? (/^\d{4}-\d{2}-\d{2}/.test(ev.date) ? 'day' : /^\d{4}-\d{2}$/.test(ev.date) ? 'month' : /^\d{4}$/.test(ev.date) ? 'year' : 'day')

export default function TimelineView() {
  const vault = useVault()
  const route = useApp((s) => s.route)
  const q = useRpc('vault.timeline', { vault })
  const [entity, setEntity] = useState<string | null>(route.view === 'timeline' ? route.entity ?? null : null)
  const [type, setType] = useState('')
  const [text, setText] = useState('')
  const dt = useDebounced(text, 150).trim().toLowerCase()
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [pickOpen, setPickOpen] = useState(false)
  const [rebuildOpen, setRebuildOpen] = useState(false)
  const scroller = useRef<HTMLDivElement>(null)
  const [currentYear, setCurrentYear] = useState('')
  const [scrolled, setScrolled] = useState(false)

  useEffect(() => {
    if (route.view === 'timeline') setEntity(route.entity ?? null)
  }, [route])

  const events = useMemo(() => q.data?.events ?? [], [q.data])

  // Entities that appear on the timeline, for the picker.
  const people = useMemo(() => {
    const m = new Map<string, { id: string; name: string; type: string; n: number }>()
    for (const ev of events) for (const e of ev.entities) {
      const x = m.get(e.id)
      if (x) x.n++
      else m.set(e.id, { id: e.id, name: e.name, type: e.type, n: 1 })
    }
    return [...m.values()].sort((a, b) => b.n - a.n || a.name.localeCompare(b.name))
  }, [events])
  const picked = entity ? people.find((p) => p.id === entity) ?? { id: entity, name: entity, type: 'person', n: 0 } : null

  const base = useMemo(
    () =>
      events.filter((ev) => {
        if (entity && !ev.entities.some((e) => e.id === entity)) return false
        if (type && !ev.entities.some((e) => e.type === type)) return false
        if (dt && !(ev.text.toLowerCase().includes(dt) || ev.entities.some((e) => e.name.toLowerCase().includes(dt)) || (ev.filename ?? '').toLowerCase().includes(dt))) return false
        return true
      }),
    [events, entity, type, dt]
  )
  const filtered = useMemo(
    () =>
      base.filter((ev) => {
        const y = yearOf(ev.date)
        if (!y) return !from && !to
        return (!from || y >= from) && (!to || y <= to)
      }),
    [base, from, to]
  )

  const years = useMemo(() => {
    const ys = base.map((e) => yearOf(e.date)).filter(Boolean).map(Number)
    if (!ys.length) return []
    const lo = Math.min(...ys), hi = Math.max(...ys)
    const counts = new Map<number, number>()
    for (const y of ys) counts.set(y, (counts.get(y) ?? 0) + 1)
    const out: { year: string; n: number }[] = []
    for (let y = lo; y <= hi; y++) out.push({ year: String(y), n: counts.get(y) ?? 0 })
    return out
  }, [base])
  const allYears = useMemo(() => {
    const ys = events.map((e) => yearOf(e.date)).filter(Boolean)
    return [...new Set(ys)].sort()
  }, [events])

  const items = useMemo(() => {
    const out: Item[] = []
    let y = '__none', m = -1
    const counts = new Map<string, number>()
    for (const ev of filtered) counts.set(yearOf(ev.date), (counts.get(yearOf(ev.date)) ?? 0) + 1)
    for (const ev of filtered) {
      const ey = yearOf(ev.date)
      if (ey !== y) {
        y = ey
        m = -1
        out.push({ k: 'year', year: ey || 'Undated', count: counts.get(ey) ?? 0 })
      }
      const em = monthOf(ev.date)
      if (em !== m) {
        m = em
        if (em > 0) out.push({ k: 'month', label: MONTHS[em - 1] })
      }
      out.push({ k: 'event', ev })
    }
    return out
  }, [filtered])

  const yearIndex = useMemo(() => {
    const m = new Map<string, number>()
    items.forEach((it, i) => it.k === 'year' && m.set(it.year, i))
    return m
  }, [items])

  const virt = useVirtualizer({
    count: items.length,
    getScrollElement: () => scroller.current,
    estimateSize: (i) => (items[i].k === 'year' ? 76 : items[i].k === 'month' ? 40 : 96),
    overscan: 8
  })

  // Track the year at the top of the viewport.
  const vis = virt.getVirtualItems()
  const firstIdx = vis[0]?.index ?? 0
  useEffect(() => {
    for (let i = firstIdx; i >= 0; i--) {
      const it = items[i]
      if (it?.k === 'year') return setCurrentYear(it.year)
      if (it?.k === 'event') {
        const y = yearOf(it.ev.date)
        if (y) return setCurrentYear(y)
      }
    }
    setCurrentYear('')
  }, [firstIdx, items])

  useEffect(() => {
    scroller.current?.scrollTo({ top: 0 })
  }, [entity, type, dt, from, to])

  const jump = (year: string) => {
    let y = Number(year)
    // The nearest year with events, if this one is empty after filtering.
    for (let d = 0; d < 80; d++) {
      for (const c of [y + d, y - d]) {
        const i = yearIndex.get(String(c))
        if (i !== undefined) return virt.scrollToIndex(i, { align: 'start' })
      }
    }
  }

  const maxN = Math.max(1, ...years.map((y) => y.n))
  const filtering = !!(entity || type || dt || from || to)
  const clear = () => { setEntity(null); setType(''); setText(''); setFrom(''); setTo(''); if (route.view === 'timeline' && route.entity) navigate({ view: 'timeline' }, { replace: true }) }

  return (
    <div className="tl-shell">
      <div className="tl-top">
        <div className="tl-title-row">
          <div style={{ flex: 1 }}>
            <h1 className="page-title">Timeline</h1>
            <div className="page-sub">
              {q.isLoading ? 'Loading…' : filtered.length === events.length ? `${plural(events.length, 'dated event')} across the investigation` : `${fmtNum(filtered.length)} of ${plural(events.length, 'event')}`}
            </div>
          </div>
          <Dropdown align="right" trigger={(open) => <Button variant="ghost" icon={MoreHorizontal} tip="More" onClick={open} />} items={[{ label: 'Rebuild timeline.md…', icon: RefreshCw, onClick: () => setRebuildOpen(true) }]} />
        </div>

        <div className="tl-filters">
          <div className="tl-pick">
            <Button icon={Users} onClick={() => setPickOpen((o) => !o)} iconRight={undefined}>
              {picked ? picked.name : 'Any entity'}
            </Button>
            {picked && <Button variant="ghost" size="sm" icon={X} tip="Clear entity" onClick={() => { setEntity(null); if (route.view === 'timeline' && route.entity) navigate({ view: 'timeline' }, { replace: true }) }} style={{ marginLeft: 4 }} />}
            {pickOpen && <EntityPicker people={people} onClose={() => setPickOpen(false)} onPick={(id) => { setEntity(id); setPickOpen(false) }} />}
          </div>
          <select className="select" value={type} onChange={(e) => setType(e.target.value)} aria-label="Entity type">
            <option value="">Any type</option>
            {ENTITY_TYPES.map((t) => <option key={t} value={t}>{TYPE_META[t].plural}</option>)}
          </select>
          <div className="input-group">
            <Search />
            <input className="input" placeholder="Search events" value={text} onChange={(e) => setText(e.target.value)} aria-label="Search events" />
          </div>
          <select className="select" value={from} onChange={(e) => setFrom(e.target.value)} aria-label="From year" style={{ minWidth: 100 }}>
            <option value="">From</option>
            {allYears.map((y) => <option key={y}>{y}</option>)}
          </select>
          <select className="select" value={to} onChange={(e) => setTo(e.target.value)} aria-label="To year" style={{ minWidth: 100 }}>
            <option value="">To</option>
            {allYears.map((y) => <option key={y}>{y}</option>)}
          </select>
          {filtering && <Button variant="ghost" size="sm" onClick={clear}>Clear filters</Button>}
        </div>

        {years.length > 1 && (
          <div className="tl-density" role="list" aria-label="Events per year">
            {years.map((y, i) => (
              <button key={y.year} className="tl-bar" data-current={y.year === currentYear} data-empty={y.n === 0} onClick={() => jump(y.year)} data-tip={`${y.year}: ${plural(y.n, 'event')}`} aria-label={`${y.year}, ${plural(y.n, 'event')}`}>
                <i style={{ height: `${y.n ? 10 + (y.n / maxN) * 90 : 3}%` }} />
                {(years.length <= 14 || i % Math.ceil(years.length / 12) === 0) && <span className="lbl">{years.length > 24 ? `’${y.year.slice(2)}` : y.year}</span>}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="tl-scroll" ref={scroller} onScroll={(e) => setScrolled((e.target as HTMLElement).scrollTop > 90)} style={years.length > 1 ? undefined : { marginTop: 18 }}>
        {q.isLoading ? (
          <div className="tl-inner" style={{ padding: '30px 36px', display: 'flex', flexDirection: 'column', gap: 16 }}>
            {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} h={72} style={{ marginLeft: 126, width: 'calc(100% - 126px)' }} />)}
          </div>
        ) : q.isError ? (
          <div className="tl-inner" style={{ padding: 36 }}><ErrorNote error={q.error} retry={() => void q.refetch()} /></div>
        ) : events.length === 0 ? (
          <Empty icon={CalendarClock} title="No dated events yet" action={<AddDocumentsButton />}>
            Events are dated facts that the pipeline pulls from your documents, such as filings, meetings and payments. They appear here once documents have been read.
          </Empty>
        ) : filtered.length === 0 ? (
          <Empty icon={Search} title="No events match" action={<Button onClick={clear}>Clear filters</Button>}>
            Nothing on the timeline fits these filters.
          </Empty>
        ) : (
          <>
            {currentYear && scrolled && (
              <div className="tl-sticky"><div className="tl-sticky-year"><span>{currentYear}</span></div></div>
            )}
            <div className="tl-inner" style={{ height: virt.getTotalSize() }}>
              {vis.map((v) => {
                const it = items[v.index]
                return (
                  <div key={v.key} data-index={v.index} ref={virt.measureElement} className="tl-row" style={{ top: 0, transform: `translateY(${v.start}px)` }}>
                    {it.k === 'year' ? (
                      <div className="tl-year">{it.year}<small>{plural(it.count, 'event')}</small></div>
                    ) : it.k === 'month' ? (
                      <div className="tl-month">{it.label}</div>
                    ) : (
                      <EventRow ev={it.ev} />
                    )}
                  </div>
                )
              })}
            </div>
          </>
        )}
      </div>

      <Modal
        open={rebuildOpen}
        onClose={() => setRebuildOpen(false)}
        title="Rebuild timeline.md"
        sub="Regenerates the readable timeline note from the event files in the vault."
        footer={
          <>
            <Button onClick={() => setRebuildOpen(false)}>Cancel</Button>
            <Button variant="primary" icon={RefreshCw} onClick={() => { setRebuildOpen(false); void startJob(['timeline'], 'Rebuilding timeline.md').catch((e) => toast({ kind: 'error', title: 'Could not start the rebuild', body: errorMessage(e) })) }}>Rebuild</Button>
          </>
        }
      >
        <p style={{ margin: 0, lineHeight: 1.55, color: 'var(--text-2)' }}>
          The timeline note is rebuilt from the saved events. It makes no model call and costs nothing. Use it if timeline.md was deleted or edited by mistake; nothing is lost, because the note is a generated output and the events themselves are stored separately. This screen reads the events directly and is unaffected.
        </p>
      </Modal>
    </div>
  )
}

function EventRow({ ev }: { ev: TimelineEvent }) {
  const p = precisionOf(ev)
  const first = ev.entities[0]
  const when = p === 'day' ? fmtDate(ev.date).replace(/,?\s*\d{4}$/, '') : p === 'month' ? MONTHS[monthOf(ev.date) - 1]?.slice(0, 3) ?? fmtDate(ev.date) : yearOf(ev.date) || ev.date
  return (
    <>
      <div className="tl-date">
        {when}
        {p !== 'day' && <small>{p === 'month' ? 'month only' : 'year only'}</small>}
      </div>
      <div className="tl-dot" style={{ '--dot': first ? typeMeta(first.type).color : 'var(--text-3)' } as CSSProperties} />
      <div className="tl-card">
        <div className="tl-text selectable">{ev.text}</div>
        {(ev.entities.length > 0 || ev.sha || ev.disputed) && (
          <div className="tl-meta">
            {ev.disputed && <DisputedBadge />}
            {ev.entities.slice(0, 5).map((e) => <EntityChip key={e.id} id={e.id} name={e.name} type={e.type} />)}
            {ev.entities.length > 5 && <span className="faint" style={{ fontSize: 'var(--fs-xs)' }}>+{ev.entities.length - 5}</span>}
            {ev.sha && ev.filename && (
              <button className="ent-src" onClick={() => navigate({ view: 'document', sha: ev.sha!, page: ev.page ?? undefined })} title="Open the source document">
                <FileText />
                <span className="truncate">{ev.filename}{ev.page ? ` · p. ${ev.page}` : ''}</span>
              </button>
            )}
          </div>
        )}
      </div>
    </>
  )
}

function EntityPicker({ people, onPick, onClose }: { people: { id: string; name: string; type: string; n: number }[]; onPick: (id: string) => void; onClose: () => void }) {
  const [s, setS] = useState('')
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const away = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) onClose() }
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    setTimeout(() => window.addEventListener('mousedown', away))
    window.addEventListener('keydown', esc)
    return () => { window.removeEventListener('mousedown', away); window.removeEventListener('keydown', esc) }
  }, [onClose])
  const list = people.filter((p) => !s || p.name.toLowerCase().includes(s.toLowerCase())).slice(0, 60)
  return (
    <div className="tl-pick-menu" ref={ref}>
      <div className="input-group"><Search /><input className="input" autoFocus placeholder="Find an entity" value={s} onChange={(e) => setS(e.target.value)} /></div>
      <div className="tl-pick-list">
        {list.map((p) => (
          <button key={p.id} className="tl-pick-item" onClick={() => onPick(p.id)}>
            <EntityAvatar type={p.type} size={20} />
            <span className="truncate">{p.name}</span>
            <span className="n">{p.n}</span>
          </button>
        ))}
        {list.length === 0 && <div className="faint" style={{ padding: 10 }}>No entity matches.</div>}
      </div>
    </div>
  )
}
