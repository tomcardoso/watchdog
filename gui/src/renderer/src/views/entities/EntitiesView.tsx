import { useVirtualizer } from '@tanstack/react-virtual'
import { AlertTriangle, FileText, GitMerge, Link2, Search, Shapes, UserX, Users, X, Copy } from 'lucide-react'
import { CSSProperties, useEffect, useMemo, useRef, useState } from 'react'
import { EntityAvatar } from '@renderer/components/EntityChip'
import { plainText } from '@renderer/components/Markdown'
import { Badge, Button, Empty, ErrorNote, Skeleton, cx, useDebounced } from '@renderer/components/ui'
import { ENTITY_TYPES, EntityRow } from '@shared/api'
import { TYPE_META, typeMeta } from '@renderer/lib/entityTypes'
import { fmtNum, plural } from '@renderer/lib/format'
import { useRpc } from '@renderer/lib/rpc'
import { navigate, useApp, useVault } from '@renderer/lib/store'
import MergeModal from './MergeModal'
import './entities.css'

type Sort = 'docs' | 'name' | 'updated' | 'contradictions'
type Flag = 'single' | 'contra' | 'unprofiled'

const SORTS: { value: Sort; label: string }[] = [
  { value: 'docs', label: 'Most documents' },
  { value: 'name', label: 'Name (A–Z)' },
  { value: 'updated', label: 'Recently updated' },
  { value: 'contradictions', label: 'Most contradictions' }
]

const FLAGS: { value: Flag; label: string; icon: typeof Copy; tip: string; test: (e: EntityRow) => boolean }[] = [
  { value: 'single', label: 'Single-source', icon: Copy, tip: 'Appears in exactly one document. Duplicate entities tend to show up here.', test: (e) => e.doc_count === 1 },
  { value: 'contra', label: 'Has contradictions', icon: AlertTriangle, tip: 'Carries at least one contradiction flag.', test: (e) => e.contradiction_count > 0 },
  { value: 'unprofiled', label: 'No summary', icon: UserX, tip: 'No prose summary has been written for this entity yet.', test: (e) => !e.has_summary }
]

export default function EntitiesView() {
  const vault = useVault()
  const route = useApp((s) => s.route)
  const type = route.view === 'entities' ? route.type ?? null : null
  const q = useRpc('vault.entities', { vault })
  const [search, setSearch] = useState('')
  const dq = useDebounced(search, 150).trim().toLowerCase()
  const [sort, setSort] = useState<Sort>('docs')
  const [flags, setFlags] = useState<Set<Flag>>(new Set())
  const [selected, setSelected] = useState<string[]>([])
  const [mergeOpen, setMergeOpen] = useState(false)
  const scroller = useRef<HTMLDivElement>(null)

  const all = q.data ?? []
  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const e of all) c[e.type] = (c[e.type] ?? 0) + 1
    return c
  }, [all])

  // Counts per quick filter, within the current type, so the pills say what they'll find.
  const inType = useMemo(() => (type ? all.filter((e) => e.type === type) : all), [all, type])
  const flagCounts = useMemo(() => Object.fromEntries(FLAGS.map((f) => [f.value, inType.filter(f.test).length])) as Record<Flag, number>, [inType])

  const rows = useMemo(() => {
    let r = inType
    for (const f of FLAGS) if (flags.has(f.value)) r = r.filter(f.test)
    if (dq) r = r.filter((e) => e.name.toLowerCase().includes(dq) || e.aliases.some((a) => a.toLowerCase().includes(dq)))
    const by: Record<Sort, (a: EntityRow, b: EntityRow) => number> = {
      docs: (a, b) => b.doc_count - a.doc_count || a.name.localeCompare(b.name),
      name: (a, b) => a.name.localeCompare(b.name),
      updated: (a, b) => (b.last_updated ?? '').localeCompare(a.last_updated ?? '') || a.name.localeCompare(b.name),
      contradictions: (a, b) => b.contradiction_count - a.contradiction_count || b.doc_count - a.doc_count
    }
    return [...r].sort(by[sort])
  }, [inType, flags, dq, sort])

  const virt = useVirtualizer({ count: rows.length, getScrollElement: () => scroller.current, estimateSize: () => 80, overscan: 10 })

  useEffect(() => {
    scroller.current?.scrollTo({ top: 0 })
  }, [type, dq, sort, flags])
  // Drop selections that are no longer in the vault (after a merge).
  useEffect(() => {
    if (q.data) setSelected((s) => s.filter((id) => q.data.some((e) => e.id === id)))
  }, [q.data])

  const toggle = (id: string) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id].slice(-2)))
  const toggleFlag = (f: Flag) =>
    setFlags((s) => {
      const n = new Set(s)
      if (n.has(f)) n.delete(f)
      else n.add(f)
      return n
    })
  const setType = (t: string | null) => navigate({ view: 'entities', type: t ?? undefined }, { replace: true })
  const meta = type ? typeMeta(type) : null

  return (
    <div className="ent-shell">
      <nav className="ent-rail" aria-label="Entity types">
        <div className="eyebrow">Entity types</div>
        <RailItem label="All entities" icon={Shapes} count={all.length} active={!type} onClick={() => setType(null)} loading={q.isLoading} />
        {ENTITY_TYPES.map((t) => (
          <RailItem key={t} label={TYPE_META[t].plural} icon={TYPE_META[t].icon} color={TYPE_META[t].color} count={counts[t] ?? 0} active={type === t} onClick={() => setType(t)} loading={q.isLoading} />
        ))}
        <p className="ent-rail-blurb">{meta ? meta.blurb : 'Every person, organization, public body, place, asset and proceeding the pipeline has profiled from your documents.'}</p>
      </nav>

      <div className="ent-main">
        <div className="ent-head">
          <h1>{meta ? meta.plural : 'Entities'}</h1>
          <div className="ent-head-sub">
            {q.isLoading ? 'Loading…' : rows.length === inType.length ? plural(rows.length, 'entity', 'entities') : `${fmtNum(rows.length)} of ${plural(inType.length, 'entity', 'entities')}`}
          </div>
        </div>

        <div className="ent-toolbar">
          <div className="input-group">
            <Search />
            <input className="input" placeholder="Search names and aliases" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search entities" />
            {search && (
              <span className="input-suffix">
                <Button variant="ghost" size="sm" icon={X} tip="Clear" onClick={() => setSearch('')} />
              </span>
            )}
          </div>
          <select className="select ent-sort" value={sort} onChange={(e) => setSort(e.target.value as Sort)} aria-label="Sort by">
            {SORTS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
          <div className="ent-filters">
            {FLAGS.map((f) => (
              <button key={f.value} className="ent-pill" aria-pressed={flags.has(f.value)} onClick={() => toggleFlag(f.value)} data-tip={f.tip} data-tip-pos="bottom">
                <f.icon />
                {f.label}
                <span className="n">{flagCounts[f.value]}</span>
              </button>
            ))}
          </div>
        </div>

        {flags.has('single') && <div className="ent-hint"><b>Single-source</b> entities are where duplicates hide: the same person or company extracted under two spellings. Select two and choose Merge to fold them together.</div>}

        <div className="ent-main-rel">
          <div className="ent-listwrap" ref={scroller} data-selecting={selected.length > 0}>
            {q.isLoading ? (
              <ListSkeleton />
            ) : q.isError ? (
              <ErrorNote error={q.error} retry={() => void q.refetch()} />
            ) : all.length === 0 ? (
              <Empty icon={Users} title="No entities yet" action={<Button variant="primary" onClick={() => useApp.getState().openAdd()}>Add documents</Button>}>
                Entities appear once documents have been read and synthesized. Add some documents to begin.
              </Empty>
            ) : rows.length === 0 ? (
              <Empty
                icon={Search}
                title="Nothing matches"
                action={
                  <Button
                    onClick={() => {
                      setSearch('')
                      setFlags(new Set())
                      setType(null)
                    }}
                  >
                    Clear filters
                  </Button>
                }
              >
                No entity fits the current search and filters.
              </Empty>
            ) : (
              <div style={{ height: virt.getTotalSize(), position: 'relative' }}>
                {virt.getVirtualItems().map((v) => {
                  const e = rows[v.index]
                  return <Row key={e.id} e={e} top={v.start} selected={selected.includes(e.id)} onToggle={() => toggle(e.id)} />
                })}
              </div>
            )}
          </div>

          {selected.length > 0 && (
            <div className="ent-bar">
              <span style={{ fontWeight: 560 }}>{selected.length === 1 ? '1 selected. Choose a second entity to merge.' : '2 selected'}</span>
              <Button variant="primary" size="sm" icon={GitMerge} disabled={selected.length !== 2} onClick={() => setMergeOpen(true)}>
                Merge…
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setSelected([])}>
                Clear
              </Button>
            </div>
          )}
        </div>
      </div>

      <MergeModal
        open={mergeOpen}
        onClose={() => {
          setMergeOpen(false)
          setSelected([])
        }}
        initialKeep={selected.length === 2 ? [...selected].map((id) => all.find((e) => e.id === id)).sort((a, b) => (b?.doc_count ?? 0) - (a?.doc_count ?? 0))[0]?.id : undefined}
        initialMerge={selected.length === 2 ? [...selected].map((id) => all.find((e) => e.id === id)).sort((a, b) => (b?.doc_count ?? 0) - (a?.doc_count ?? 0))[1]?.id : undefined}
      />
    </div>
  )
}

function RailItem({ label, icon: Icon, color, count, active, onClick, loading }: { label: string; icon: typeof Users; color?: string; count: number; active: boolean; onClick: () => void; loading: boolean }) {
  return (
    <button className="ent-rail-item" aria-current={active} onClick={onClick} style={color ? ({ '--chip-color': color } as CSSProperties) : undefined}>
      <span className="ico">
        <Icon />
      </span>
      <span className="truncate">{label}</span>
      <span className="n">{loading ? '' : fmtNum(count)}</span>
    </button>
  )
}

function Row({ e, top, selected, onToggle }: { e: EntityRow; top: number; selected: boolean; onToggle: () => void }) {
  const m = typeMeta(e.type)
  const aliases = e.aliases.filter((a) => a.toLowerCase() !== e.name.toLowerCase())
  const sum = plainText(e.summary, 200)
  return (
    <div
      className="ent-row"
      data-selected={selected}
      style={{ top, '--chip-color': m.color } as CSSProperties}
      onClick={() => navigate({ view: 'entity', id: e.id })}
      role="link"
      tabIndex={0}
      onKeyDown={(ev) => ev.key === 'Enter' && navigate({ view: 'entity', id: e.id })}
    >
      <label className="check" onClick={(ev) => ev.stopPropagation()}>
        <input type="checkbox" checked={selected} onChange={onToggle} aria-label={`Select ${e.name}`} />
      </label>
      <EntityAvatar type={e.type} size={44} />
      <div className="ent-row-body">
        <div className="ent-row-name">
          <span className="nm truncate">{e.name}</span>
          <span className="ty">{m.label}</span>
        </div>
        {aliases.length > 0 && <div className="ent-row-alias truncate">Also: {aliases.slice(0, 4).join(' · ')}{aliases.length > 4 ? ` · +${aliases.length - 4}` : ''}</div>}
        <div className={cx('ent-row-sum', !sum && 'none')}>{sum || 'No summary yet.'}</div>
      </div>
      <div className="ent-row-stats">
        {e.contradiction_count > 0 && (
          <Badge tone="danger" icon={AlertTriangle} tip="Contradiction flags">
            {e.contradiction_count}
          </Badge>
        )}
        <span className="st" data-tip="Documents" data-tip-pos="bottom">
          <FileText />
          {fmtNum(e.doc_count)}
        </span>
        <span className={cx('st rel', e.role_count === 0 && 'dim')} data-tip="Relationships" data-tip-pos="bottom">
          <Link2 />
          {fmtNum(e.role_count)}
        </span>
      </div>
    </div>
  )
}

function ListSkeleton() {
  return (
    <div>
      {Array.from({ length: 9 }).map((_, i) => (
        <div key={i} style={{ display: 'flex', gap: 14, alignItems: 'center', height: 80, padding: '0 12px 0 36px', borderBottom: '1px solid var(--border)' }}>
          <Skeleton w={44} h={44} style={{ borderRadius: 13 }} />
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 8 }}>
            <Skeleton w={`${30 + ((i * 13) % 25)}%`} h={14} />
            <Skeleton w="72%" h={11} />
          </div>
          <Skeleton w={90} h={14} />
        </div>
      ))}
    </div>
  )
}
