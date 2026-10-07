import { ArrowLeft, ArrowRight, Maximize, Minus, Network, Plus, Search, X } from 'lucide-react'
import { CSSProperties, useEffect, useMemo, useRef, useState } from 'react'
import { EntityAvatar } from '@renderer/components/EntityChip'
import { plainText } from '@renderer/components/Markdown'
import { Button, Empty, ErrorNote, Spinner, Switch } from '@renderer/components/ui'
import { ENTITY_TYPES } from '@shared/api'
import { TYPE_META, typeMeta } from '@renderer/lib/entityTypes'
import { fmtNum, plural } from '@renderer/lib/format'
import { useRpc } from '@renderer/lib/rpc'
import { navigate, useApp, useVault } from '@renderer/lib/store'
import { GraphEngine, RawEdge, RawNode } from './engine'
import '../entities/entities.css'
import './graph.css'

export default function GraphView() {
  const vault = useVault()
  const route = useApp((s) => s.route)
  const focus = route.view === 'graph' ? route.focus : undefined
  const q = useRpc('vault.graph', { vault })
  if (q.isLoading)
    return (
      <div className="gr-empty">
        <Spinner size="lg" />
      </div>
    )
  if (q.isError || !q.data)
    return (
      <div className="page-inner">
        <ErrorNote error={q.error ?? 'The network could not be loaded.'} retry={() => void q.refetch()} />
      </div>
    )
  if (q.data.edges.length === 0)
    return (
      <div className="gr-empty">
        <Empty icon={Network} title="No relationships to draw yet" action={<Button onClick={() => navigate({ view: 'entities' })}>Browse entities</Button>}>
          The network connects entities that the documents relate to one another, such as an officer of a company or a party to a case. It fills in as documents are read and synthesized.
        </Empty>
      </div>
    )
  return <Network_ nodes={q.data.nodes} edges={q.data.edges} focus={focus} />
}

function Network_({ nodes, edges, focus }: { nodes: RawNode[]; edges: RawEdge[]; focus?: string }) {
  const vault = useVault()
  const wrap = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const engine = useRef<GraphEngine | null>(null)
  const ents = useRpc('vault.entities', { vault })
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [minDocs, setMinDocs] = useState(1)
  const [showLoose, setShowLoose] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const firstData = useRef(true)

  const connected = useMemo(() => {
    const s = new Set<string>()
    for (const e of edges) {
      s.add(e.source)
      s.add(e.target)
    }
    return s
  }, [edges])
  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes])
  const maxDocs = useMemo(() => Math.max(1, ...nodes.map((n) => n.doc_count)), [nodes])
  const typeCounts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const n of nodes) if (showLoose || connected.has(n.id)) c[n.type] = (c[n.type] ?? 0) + 1
    return c
  }, [nodes, connected, showLoose])

  const visible = useMemo(() => nodes.filter((n) => !hidden.has(n.type) && n.doc_count >= minDocs && (showLoose || connected.has(n.id))), [nodes, hidden, minDocs, showLoose, connected])
  const visibleEdges = useMemo(() => {
    const ids = new Set(visible.map((n) => n.id))
    return edges.filter((e) => ids.has(e.source) && ids.has(e.target))
  }, [visible, edges])

  // Engine lifecycle.
  useEffect(() => {
    const c = canvas.current!
    const eng = new GraphEngine(c, {
      onHover: () => {},
      onSelect: (id) => setSelected(id),
      onOpen: (id) => navigate({ view: 'entity', id })
    })
    engine.current = eng
    const size = () => {
      if (!wrap.current) return
      const r = wrap.current.getBoundingClientRect()
      eng.setSize(r.width, r.height)
    }
    size()
    const ro = new ResizeObserver(size)
    ro.observe(wrap.current!)
    const mo = new MutationObserver(() => eng.refreshColors())
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onMq = () => eng.refreshColors()
    mq.addEventListener('change', onMq)
    return () => {
      ro.disconnect()
      mo.disconnect()
      mq.removeEventListener('change', onMq)
      eng.destroy()
      engine.current = null
      firstData.current = true
    }
  }, [])

  useEffect(() => {
    const eng = engine.current
    if (!eng) return
    eng.setData(visible, visibleEdges, { fit: !firstData.current ? true : undefined })
    firstData.current = false
  }, [visible, visibleEdges])

  useEffect(() => {
    engine.current?.setSelected(selected)
  }, [selected])

  const pick = (id: string) => {
    // Relax filters if they hide the node being asked for.
    const n = byId.get(id)
    if (!n) return
    if (hidden.has(n.type)) setHidden((h) => { const x = new Set(h); x.delete(n.type); return x })
    if (n.doc_count < minDocs) setMinDocs(1)
    if (!connected.has(id)) setShowLoose(true)
    setSelected(id)
    setTimeout(() => engine.current?.focusNode(id), 60)
  }

  useEffect(() => {
    if (focus) pick(focus)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus])

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setSelected(null)
    }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [])

  const results = useMemo(() => {
    const s = search.trim().toLowerCase()
    if (!s) return []
    return nodes.filter((n) => n.name.toLowerCase().includes(s)).sort((a, b) => b.doc_count - a.doc_count).slice(0, 7)
  }, [search, nodes])

  const sel = selected ? byId.get(selected) : null

  return (
    <div className="gr-shell" ref={wrap}>
      <canvas ref={canvas} className="gr-canvas" />

      <div className="gr-panel gr-controls">
        <div className="gr-search">
          <div className="input-group">
            <Search />
            <input
              className="input"
              placeholder="Find an entity"
              value={search}
              onChange={(e) => { setSearch(e.target.value); setSearchOpen(true) }}
              onFocus={() => setSearchOpen(true)}
              onBlur={() => setTimeout(() => setSearchOpen(false), 120)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && results[0]) { pick(results[0].id); setSearch(''); (e.target as HTMLInputElement).blur() }
              }}
              aria-label="Find an entity in the network"
            />
            {search && (
              <span className="input-suffix">
                <Button variant="ghost" size="sm" icon={X} tip="Clear" onMouseDown={(e) => e.preventDefault()} onClick={() => setSearch('')} />
              </span>
            )}
          </div>
          {searchOpen && results.length > 0 && (
            <div className="gr-results">
              {results.map((n, i) => (
                <button key={n.id} className="gr-result" aria-selected={i === 0} onMouseDown={(e) => e.preventDefault()} onClick={() => { pick(n.id); setSearch('') }}>
                  <EntityAvatar type={n.type} size={20} />
                  <span className="truncate">{n.name}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        <div>
          <div className="eyebrow" style={{ padding: '0 8px 4px' }}>Show</div>
          <div className="gr-legend">
            {ENTITY_TYPES.map((t) => (
              <button
                key={t}
                className="gr-leg"
                aria-pressed={!hidden.has(t)}
                style={{ '--chip-color': TYPE_META[t].color } as CSSProperties}
                onClick={() => setHidden((h) => { const x = new Set(h); if (x.has(t)) x.delete(t); else x.add(t); return x })}
              >
                <span className="dot" />
                <span>{TYPE_META[t].plural}</span>
                <span className="n">{fmtNum(typeCounts[t] ?? 0)}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="gr-slider">
          <div className="gr-slider-head">
            <span>Minimum documents</span>
            <span className="tnum">{minDocs}</span>
          </div>
          <input type="range" min={1} max={Math.min(maxDocs, 30)} value={minDocs} onChange={(e) => setMinDocs(Number(e.target.value))} aria-label="Minimum documents" />
        </div>

        <div className="gr-row">
          <span data-tip="Entities with no relationships recorded" data-tip-pos="bottom">Show unconnected</span>
          <Switch checked={showLoose} onChange={setShowLoose} label="Show unconnected entities" />
        </div>
      </div>

      <div className="gr-panel gr-stats">
        {plural(visible.length, 'entity', 'entities')} · {plural(visibleEdges.length, 'relationship')}
      </div>
      {!sel && <div className="gr-panel gr-hint">Scroll to zoom · drag to pan · click a node to inspect · double-click to open</div>}

      <div className="gr-panel gr-zoom" data-shifted={!!sel}>
        <Button variant="ghost" size="sm" icon={Plus} tip="Zoom in" onClick={() => engine.current?.zoomBy(1.5)} />
        <Button variant="ghost" size="sm" icon={Minus} tip="Zoom out" onClick={() => engine.current?.zoomBy(1 / 1.5)} />
        <Button variant="ghost" size="sm" icon={Maximize} tip="Fit to screen" onClick={() => engine.current?.fit(true)} />
      </div>

      {sel && <SideCard id={sel.id} edges={edges} byId={byId} summary={ents.data?.find((e) => e.id === sel.id)?.summary ?? null} aliases={ents.data?.find((e) => e.id === sel.id)?.aliases ?? []} onPick={pick} onClose={() => setSelected(null)} />}
    </div>
  )
}

function SideCard({ id, edges, byId, summary, aliases, onPick, onClose }: { id: string; edges: RawEdge[]; byId: Map<string, RawNode>; summary: string | null; aliases: string[]; onPick: (id: string) => void; onClose: () => void }) {
  const n = byId.get(id)!
  const m = typeMeta(n.type)
  const rels = useMemo(() => {
    const out: { other: RawNode; role: string; dir: 'out' | 'in'; key: string }[] = []
    edges.forEach((e, i) => {
      if (e.source === id && byId.get(e.target)) out.push({ other: byId.get(e.target)!, role: e.role, dir: 'out', key: `${i}` })
      else if (e.target === id && byId.get(e.source)) out.push({ other: byId.get(e.source)!, role: e.role, dir: 'in', key: `${i}` })
    })
    return out.sort((a, b) => b.other.doc_count - a.other.doc_count)
  }, [edges, id, byId])
  const sum = plainText(summary, 260)
  return (
    <div className="gr-panel gr-card" style={{ '--chip-color': m.color } as CSSProperties}>
      <div className="gr-card-head">
        <EntityAvatar type={n.type} size={44} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="ent-type-tag" style={{ marginBottom: 3 }}>{m.label}</div>
          <h2>{n.name}</h2>
          <div className="muted" style={{ fontSize: 'var(--fs-sm)', marginTop: 3 }}>
            {plural(n.doc_count, 'document')} · {plural(rels.length, 'relationship')}
          </div>
        </div>
        <Button variant="ghost" size="sm" icon={X} tip="Close" onClick={onClose} />
      </div>
      <div className="gr-card-body">
        {aliases.length > 0 && <div className="muted" style={{ fontSize: 'var(--fs-sm)', marginBottom: 8 }}>Also known as {aliases.slice(0, 4).join(', ')}</div>}
        {sum && <p style={{ margin: '0 0 12px', color: 'var(--text-2)', lineHeight: 1.5, fontSize: 'var(--fs-sm)' }}>{sum}</p>}
        <div className="eyebrow" style={{ margin: '4px 6px 4px' }}>Relationships</div>
        {rels.map((r) => (
          <button key={r.key} className="gr-rel" onClick={() => onPick(r.other.id)}>
            {r.dir === 'out' ? <ArrowRight size={13} color="var(--text-3)" /> : <ArrowLeft size={13} color="var(--text-3)" />}
            <span className="role truncate">{r.role}</span>
            <EntityAvatar type={r.other.type} size={20} />
            <span className="nm truncate">{r.other.name}</span>
          </button>
        ))}
      </div>
      <div className="gr-card-foot">
        <Button variant="primary" iconRight={ArrowRight} style={{ flex: 1 }} onClick={() => navigate({ view: 'entity', id })}>
          Open entity
        </Button>
        <Button onClick={() => navigate({ view: 'timeline', entity: id })}>Timeline</Button>
      </div>
    </div>
  )
}
