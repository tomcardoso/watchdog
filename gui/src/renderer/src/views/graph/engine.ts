// The network's drawing and physics, kept out of React so a few hundred nodes stay smooth:
// d3-force positions the nodes, a <canvas> draws them, and a small camera handles pan and zoom.
// React owns the controls and the side card; it talks to this class through a handful of methods.

import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, Simulation, SimulationLinkDatum, SimulationNodeDatum } from 'd3-force'
import type { GraphEdge } from '@shared/api'

export interface GNode extends SimulationNodeDatum {
  id: string
  name: string
  type: string
  doc_count: number
  r: number
  deg: number
  a: number // eased visibility 0..1 (dimming)
  h: number // eased hover emphasis 0..1
}
export interface GEdge extends SimulationLinkDatum<GNode> {
  role: string
  labels: string[]
  docs: string[]
  directed: boolean
  raw: RawEdge
  source: GNode
  target: GNode
  curve: number
}
export interface RawNode { id: string; name: string; type: string; doc_count: number }
/** One edge per pair of entities, listing every distinct relationship between them (D291). */
export type RawEdge = GraphEdge

interface Cam { k: number; x: number; y: number }
interface Callbacks {
  onHover: (id: string | null) => void
  onSelect: (id: string | null) => void
  onSelectEdge: (edge: RawEdge | null) => void
  onOpen: (id: string) => void
}
interface Palette {
  types: Record<string, string>
  fallback: string
  bg: string
  surface: string
  text: string
  text2: string
  text3: string
  border: string
  accent: string
  font: string
}

// Thicker for a pair more documents connect: 1x for one document, about 3x from eight on.
const weightFor = (docs: number) => 1 + Math.min(2, Math.log2(Math.max(docs, 1)) * 0.7)

const radiusFor = (docs: number) => Math.max(5, Math.min(26, 4.5 + Math.sqrt(Math.max(docs, 1)) * 3.1))

export class GraphEngine {
  private ctx: CanvasRenderingContext2D
  private sim: Simulation<GNode, GEdge>
  private nodes: GNode[] = []
  private edges: GEdge[] = []
  private byId = new Map<string, GNode>()
  private adj = new Map<string, Set<string>>()
  private inc = new Map<string, GEdge[]>()
  private pos = new Map<string, { x: number; y: number }>()
  private cam: Cam = { k: 1, x: 0, y: 0 }
  private tgt: Cam = { k: 1, x: 0, y: 0 }
  private w = 800
  private h = 600
  private dpr = 1
  private raf = 0
  private dirty = true
  private palette!: Palette
  private hoverId: string | null = null
  private hoverEdge: GEdge | null = null
  private selectedEdge: RawEdge | null = null
  private selectedId: string | null = null
  private pendingFit = false
  private pendingFocus: string | null = null
  private drag: { mode: 'pan' | 'node'; node?: GNode; sx: number; sy: number; moved: boolean; cx: number; cy: number } | null = null
  private dead = false
  private cleanup: (() => void)[] = []
  private reduced = false

  constructor(private canvas: HTMLCanvasElement, private cb: Callbacks) {
    this.ctx = canvas.getContext('2d')!
    this.reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    this.sim = forceSimulation<GNode, GEdge>()
      .alphaDecay(0.045)
      .alphaMin(0.004)
      .velocityDecay(0.42)
      .on('tick', () => (this.dirty = true))
      .on('end', () => {
        if (this.pendingFit) {
          this.pendingFit = false
          this.fit(true)
        }
        if (this.pendingFocus) {
          const id = this.pendingFocus
          this.pendingFocus = null
          this.focusNode(id)
        }
      })
    this.sim.stop()
    this.refreshColors()
    this.bind()
    const loop = () => {
      if (this.dead) return
      this.frame()
      this.raf = requestAnimationFrame(loop)
    }
    this.raf = requestAnimationFrame(loop)
  }

  // ── setup ──────────────────────────────────────────────────────────────────
  refreshColors() {
    const root = getComputedStyle(document.documentElement)
    const v = (n: string) => root.getPropertyValue(n).trim()
    const types: Record<string, string> = {}
    for (const t of ['person', 'organization', 'public-body', 'place', 'asset', 'proceeding']) types[t] = v(`--${t}`)
    this.palette = {
      types,
      fallback: v('--text-3'),
      bg: v('--bg'),
      surface: v('--surface'),
      text: v('--text'),
      text2: v('--text-2'),
      text3: v('--text-3'),
      border: v('--border-strong'),
      accent: v('--accent'),
      font: getComputedStyle(document.body).fontFamily || 'sans-serif'
    }
    this.dirty = true
  }

  setSize(w: number, h: number) {
    this.w = Math.max(1, w)
    this.h = Math.max(1, h)
    this.dpr = Math.min(3, window.devicePixelRatio || 1)
    this.canvas.width = Math.round(this.w * this.dpr)
    this.canvas.height = Math.round(this.h * this.dpr)
    this.canvas.style.width = `${this.w}px`
    this.canvas.style.height = `${this.h}px`
    this.dirty = true
  }

  /** Replace the visible graph. Nodes that were already placed keep their positions. */
  setData(rawNodes: RawNode[], rawEdges: RawEdge[], opts: { warm?: boolean; fit?: boolean } = {}) {
    for (const n of this.nodes) if (n.x !== undefined && n.y !== undefined) this.pos.set(n.id, { x: n.x, y: n.y })
    const first = this.nodes.length === 0
    this.byId = new Map()
    this.nodes = rawNodes.map((n) => {
      const p = this.pos.get(n.id)
      const node: GNode = { id: n.id, name: n.name, type: n.type, doc_count: n.doc_count, r: radiusFor(n.doc_count), deg: 0, a: 1, h: 0, x: p?.x, y: p?.y }
      this.byId.set(n.id, node)
      return node
    })
    const pair = new Map<string, GEdge[]>()
    this.adj = new Map()
    this.inc = new Map()
    this.edges = []
    for (const e of rawEdges) {
      const s = this.byId.get(e.source)
      const t = this.byId.get(e.target)
      if (!s || !t || s === t) continue
      const edge: GEdge = { source: s, target: t, role: e.role, labels: e.labels?.length ? e.labels : [e.role], docs: e.docs, directed: e.directed ?? true, raw: e, curve: 0 }
      this.edges.push(edge)
      const key = s.id < t.id ? `${s.id}|${t.id}` : `${t.id}|${s.id}`
      if (!pair.has(key)) pair.set(key, [])
      pair.get(key)!.push(edge)
      s.deg++
      t.deg++
      for (const [a, b] of [[s.id, t.id], [t.id, s.id]]) {
        if (!this.adj.has(a)) this.adj.set(a, new Set())
        this.adj.get(a)!.add(b)
      }
      for (const id of [s.id, t.id]) {
        if (!this.inc.has(id)) this.inc.set(id, [])
        this.inc.get(id)!.push(edge)
      }
    }
    for (const list of pair.values()) {
      list.forEach((e, i) => {
        const dir = e.source.id < e.target.id ? 1 : -1
        e.curve = (list.length === 1 ? 0.1 : (i - (list.length - 1) / 2) * 0.28) * dir
      })
    }
    for (const n of this.nodes) n.r = radiusFor(n.doc_count)

    // Seed unplaced nodes near a connected neighbour (or the centre) so they don't fly in from afar.
    for (const n of this.nodes) {
      if (n.x !== undefined) continue
      const nb = [...(this.adj.get(n.id) ?? [])].map((id) => this.byId.get(id)!).find((m) => m.x !== undefined)
      const ang = Math.random() * Math.PI * 2
      n.x = (nb?.x ?? 0) + Math.cos(ang) * 30
      n.y = (nb?.y ?? 0) + Math.sin(ang) * 30
    }

    const maxDeg = Math.max(1, ...this.nodes.map((n) => n.deg))
    this.sim
      .nodes(this.nodes)
      .force(
        'link',
        forceLink<GNode, GEdge>(this.edges)
          .id((d) => d.id)
          .distance((l) => 38 + l.source.r + l.target.r + 18 * Math.min(2, Math.sqrt(Math.max(l.source.deg, l.target.deg) / maxDeg) * 2))
          .strength((l) => 0.55 / Math.sqrt(Math.min(l.source.deg, l.target.deg) || 1))
      )
      .force('charge', forceManyBody<GNode>().strength((d) => -90 - d.r * 9).distanceMax(420))
      .force('collide', forceCollide<GNode>().radius((d) => d.r + 4).strength(0.9))
      .force('x', forceX<GNode>(0).strength((d) => (d.deg === 0 ? 0.09 : 0.028)))
      .force('y', forceY<GNode>(0).strength((d) => (d.deg === 0 ? 0.09 : 0.028)))

    if (first || opts.warm) {
      this.sim.alpha(1)
      const n = Math.min(300, 120 + this.nodes.length)
      for (let i = 0; i < n; i++) this.sim.tick()
      this.sim.alpha(0.02)
      this.sim.stop()
      if (opts.fit !== false) this.fit(false)
    } else {
      this.pendingFit = opts.fit ?? false
      this.sim.alpha(0.45).restart()
    }
    if (this.selectedId && !this.byId.has(this.selectedId)) {
      this.selectedId = null
      this.cb.onSelect(null)
    }
    this.dirty = true
  }

  // ── camera ─────────────────────────────────────────────────────────────────
  fit(animate = true) {
    if (!this.nodes.length) return
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
    for (const n of this.nodes) {
      x0 = Math.min(x0, (n.x ?? 0) - n.r)
      x1 = Math.max(x1, (n.x ?? 0) + n.r)
      y0 = Math.min(y0, (n.y ?? 0) - n.r)
      y1 = Math.max(y1, (n.y ?? 0) + n.r + 14)
    }
    const pad = 64
    const bw = Math.max(1, x1 - x0)
    const bh = Math.max(1, y1 - y0)
    const k = Math.max(0.15, Math.min((this.w - pad * 2) / bw, (this.h - pad * 2) / bh, 1.5))
    this.setTarget({ k, x: this.w / 2 - ((x0 + x1) / 2) * k, y: this.h / 2 - ((y0 + y1) / 2) * k }, animate)
  }

  focusNode(id: string, zoom?: number) {
    const n = this.byId.get(id)
    if (!n || n.x === undefined || n.y === undefined) {
      this.pendingFocus = id
      return
    }
    const k = Math.max(this.tgt.k, zoom ?? 1.25)
    // Aim slightly left of centre: the side card sits on the right.
    this.setTarget({ k, x: this.w * 0.42 - n.x * k, y: this.h / 2 - n.y * k }, true)
  }

  zoomBy(f: number) {
    this.zoomAt(this.w / 2, this.h / 2, f)
  }

  private zoomAt(px: number, py: number, f: number) {
    const k = Math.max(0.12, Math.min(6, this.tgt.k * f))
    const r = k / this.tgt.k
    this.tgt = { k, x: px - (px - this.tgt.x) * r, y: py - (py - this.tgt.y) * r }
    if (this.reduced) this.cam = { ...this.tgt }
    this.dirty = true
  }

  private setTarget(c: Cam, animate: boolean) {
    this.tgt = c
    if (!animate || this.reduced) this.cam = { ...c }
    this.dirty = true
  }

  setSelected(id: string | null) {
    this.selectedId = id
    this.dirty = true
  }

  setSelectedEdge(edge: RawEdge | null) {
    this.selectedEdge = edge
    this.dirty = true
  }

  /** Select the edge between two entities, as a click on its line does. */
  selectEdgeBetween(a: string, b: string): boolean {
    const e = this.edges.find((x) => (x.source.id === a && x.target.id === b) || (x.source.id === b && x.target.id === a))
    if (!e) return false
    this.selectedId = null
    this.selectedEdge = e.raw
    this.cb.onSelectEdge(e.raw)
    this.dirty = true
    return true
  }

  has(id: string) {
    return this.byId.has(id)
  }

  // ── events ─────────────────────────────────────────────────────────────────
  private toWorld(sx: number, sy: number) {
    return { x: (sx - this.cam.x) / this.cam.k, y: (sy - this.cam.y) / this.cam.k }
  }

  private hit(sx: number, sy: number): GNode | null {
    const { x, y } = this.toWorld(sx, sy)
    let best: GNode | null = null
    let bd = Infinity
    for (const n of this.nodes) {
      const dx = (n.x ?? 0) - x
      const dy = (n.y ?? 0) - y
      const d = Math.hypot(dx, dy)
      const lim = Math.max(n.r, 7 / this.cam.k) + 2 / this.cam.k
      if (d <= lim && d - n.r < bd) {
        best = n
        bd = d - n.r
      }
    }
    return best
  }

  private edgeAt(sx: number, sy: number): GEdge | null {
    const { x, y } = this.toWorld(sx, sy)
    const tol = 6 / this.cam.k
    let best: GEdge | null = null
    let bd = tol
    for (const e of this.edges) {
      const p0 = e.source, p1 = e.target
      const mx = ((p0.x ?? 0) + (p1.x ?? 0)) / 2, my = ((p0.y ?? 0) + (p1.y ?? 0)) / 2
      const dx = (p1.x ?? 0) - (p0.x ?? 0), dy = (p1.y ?? 0) - (p0.y ?? 0)
      const cx = mx - dy * e.curve, cy = my + dx * e.curve
      for (let t = 0.1; t < 1; t += 0.1) {
        const u = 1 - t
        const px = u * u * (p0.x ?? 0) + 2 * u * t * cx + t * t * (p1.x ?? 0)
        const py = u * u * (p0.y ?? 0) + 2 * u * t * cy + t * t * (p1.y ?? 0)
        const d = Math.hypot(px - x, py - y)
        if (d < bd) {
          bd = d
          best = e
        }
      }
    }
    return best
  }

  private bind() {
    const c = this.canvas
    const rel = (e: PointerEvent | WheelEvent | MouseEvent) => {
      const r = c.getBoundingClientRect()
      return { x: e.clientX - r.left, y: e.clientY - r.top }
    }
    const down = (e: PointerEvent) => {
      if (e.button !== 0) return
      const p = rel(e)
      const n = this.hit(p.x, p.y)
      c.setPointerCapture(e.pointerId)
      this.drag = { mode: n ? 'node' : 'pan', node: n ?? undefined, sx: p.x, sy: p.y, moved: false, cx: this.cam.x, cy: this.cam.y }
      if (n) {
        const w = this.toWorld(p.x, p.y)
        n.fx = n.x
        n.fy = n.y
        void w
      }
    }
    const move = (e: PointerEvent) => {
      const p = rel(e)
      const d = this.drag
      if (d) {
        const dist = Math.hypot(p.x - d.sx, p.y - d.sy)
        if (!d.moved && dist > 3) {
          d.moved = true
          if (d.mode === 'node') this.sim.alphaTarget(0.16).restart()
        }
        if (d.moved) {
          if (d.mode === 'pan') {
            this.cam.x = this.tgt.x = d.cx + (p.x - d.sx)
            this.cam.y = this.tgt.y = d.cy + (p.y - d.sy)
          } else if (d.node) {
            const w = this.toWorld(p.x, p.y)
            d.node.fx = w.x
            d.node.fy = w.y
          }
          this.dirty = true
        }
        return
      }
      const n = this.hit(p.x, p.y)
      const id = n?.id ?? null
      const edge = n ? null : this.edgeAt(p.x, p.y)
      if (id !== this.hoverId) {
        this.hoverId = id
        c.style.cursor = id ? 'pointer' : 'grab'
        this.cb.onHover(id)
      }
      if (edge !== this.hoverEdge) {
        this.hoverEdge = edge
        if (!id) c.style.cursor = edge ? 'pointer' : 'grab'
      }
      this.dirty = true
    }
    const up = (e: PointerEvent) => {
      const d = this.drag
      this.drag = null
      if (c.hasPointerCapture(e.pointerId)) c.releasePointerCapture(e.pointerId)
      if (!d) return
      if (d.mode === 'node' && d.node) {
        d.node.fx = null
        d.node.fy = null
        this.sim.alphaTarget(0)
      }
      if (!d.moved) {
        const id = d.node?.id ?? null
        const p = rel(e)
        const edge = id ? null : this.edgeAt(p.x, p.y)
        this.selectedId = id
        this.selectedEdge = edge?.raw ?? null
        if (edge) this.cb.onSelectEdge(edge.raw)
        else this.cb.onSelect(id)
      }
      c.style.cursor = this.hoverId ? 'pointer' : 'grab'
      this.dirty = true
    }
    const leave = () => {
      if (this.drag) return
      if (this.hoverId) this.cb.onHover(null)
      this.hoverId = null
      this.hoverEdge = null
      this.dirty = true
    }
    const wheel = (e: WheelEvent) => {
      e.preventDefault()
      const p = rel(e)
      const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY
      this.zoomAt(p.x, p.y, Math.exp(-dy * (e.ctrlKey ? 0.012 : 0.0016)))
    }
    const dbl = (e: MouseEvent) => {
      const p = rel(e)
      const n = this.hit(p.x, p.y)
      if (n) this.cb.onOpen(n.id)
    }
    c.style.cursor = 'grab'
    c.addEventListener('pointerdown', down)
    c.addEventListener('pointermove', move)
    c.addEventListener('pointerup', up)
    c.addEventListener('pointercancel', up)
    c.addEventListener('pointerleave', leave)
    c.addEventListener('wheel', wheel, { passive: false })
    c.addEventListener('dblclick', dbl)
    this.cleanup.push(() => {
      c.removeEventListener('pointerdown', down)
      c.removeEventListener('pointermove', move)
      c.removeEventListener('pointerup', up)
      c.removeEventListener('pointercancel', up)
      c.removeEventListener('pointerleave', leave)
      c.removeEventListener('wheel', wheel)
      c.removeEventListener('dblclick', dbl)
    })
  }

  destroy() {
    this.dead = true
    cancelAnimationFrame(this.raf)
    this.sim.stop()
    this.cleanup.forEach((f) => f())
  }

  // ── drawing ────────────────────────────────────────────────────────────────
  private frame() {
    // Ease the camera.
    const c = this.cam, t = this.tgt
    if (Math.abs(c.k - t.k) > 0.0005 || Math.abs(c.x - t.x) > 0.05 || Math.abs(c.y - t.y) > 0.05) {
      const f = 0.2
      c.k += (t.k - c.k) * f
      c.x += (t.x - c.x) * f
      c.y += (t.y - c.y) * f
      this.dirty = true
    } else if (c.k !== t.k || c.x !== t.x || c.y !== t.y) {
      c.k = t.k
      c.x = t.x
      c.y = t.y
      this.dirty = true
    }
    // Ease node emphasis.
    const focus = this.hoverId ?? this.selectedId
    const nb = focus ? this.adj.get(focus) : null
    for (const n of this.nodes) {
      const want = !focus ? 1 : n.id === focus || nb?.has(n.id) ? 1 : 0.14
      const wantH = n.id === this.hoverId || n.id === this.selectedId ? 1 : 0
      if (Math.abs(n.a - want) > 0.004) {
        n.a += (want - n.a) * 0.22
        this.dirty = true
      } else n.a = want
      if (Math.abs(n.h - wantH) > 0.004) {
        n.h += (wantH - n.h) * 0.3
        this.dirty = true
      } else n.h = wantH
    }
    if (!this.dirty) return
    this.dirty = false
    this.draw(focus, nb ?? null)
  }

  private colour(type: string) {
    return this.palette.types[type] || this.palette.fallback
  }

  private draw(focus: string | null, nb: Set<string> | null) {
    const { ctx, palette: P } = this
    const { k, x, y } = this.cam
    const dpr = this.dpr
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, this.w, this.h)

    // Dot grid, so panning and zooming have something to push against.
    if (k > 0.35) {
      const step = 48 * k
      const ox = ((x % step) + step) % step
      const oy = ((y % step) + step) % step
      ctx.fillStyle = P.border
      ctx.globalAlpha = Math.min(0.5, (k - 0.35) * 0.9)
      for (let gx = ox; gx < this.w; gx += step) for (let gy = oy; gy < this.h; gy += step) ctx.fillRect(gx - 0.75, gy - 0.75, 1.5, 1.5)
      ctx.globalAlpha = 1
    }

    ctx.setTransform(dpr * k, 0, 0, dpr * k, dpr * x, dpr * y)

    // Edges.
    ctx.lineCap = 'round'
    const baseW = Math.max(0.6, 1 / Math.sqrt(k))
    for (const e of this.edges) {
      const s = e.source, t = e.target
      const involved = focus && (s.id === focus || t.id === focus)
      const dim = Math.min(s.a, t.a)
      const isHover = e === this.hoverEdge || e.raw === this.selectedEdge
      ctx.globalAlpha = involved || isHover ? 0.85 : focus ? 0.05 + 0.1 * dim : 0.26
      ctx.strokeStyle = involved || isHover ? P.accent : P.text3
      ctx.lineWidth = (involved || isHover ? 1.6 : 1) * baseW * weightFor(e.docs.length)
      this.path(e)
      ctx.stroke()
      // An arrow only when every relationship on the pair runs the same way.
      if ((involved || isHover) && k > 0.5 && e.directed) this.arrow(e, P.accent)
    }
    ctx.globalAlpha = 1

    // Nodes.
    const order = [...this.nodes].sort((a, b) => a.a + a.h - (b.a + b.h))
    for (const n of order) {
      const col = this.colour(n.type)
      const r = n.r * (1 + n.h * 0.14)
      ctx.globalAlpha = 0.25 + 0.75 * n.a
      if (n.id === this.selectedId) {
        ctx.beginPath()
        ctx.arc(n.x ?? 0, n.y ?? 0, r + 5, 0, Math.PI * 2)
        ctx.strokeStyle = P.accent
        ctx.lineWidth = 2
        ctx.globalAlpha = 0.9
        ctx.stroke()
        ctx.globalAlpha = 1
      }
      ctx.beginPath()
      ctx.arc(n.x ?? 0, n.y ?? 0, r, 0, Math.PI * 2)
      ctx.fillStyle = col
      ctx.fill()
      ctx.lineWidth = 1.6
      ctx.strokeStyle = P.bg
      ctx.stroke()
    }
    ctx.globalAlpha = 1

    // Labels, in screen space so they stay one size.
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.textAlign = 'center'
    ctx.textBaseline = 'top'
    ctx.lineJoin = 'round'
    const showAll = k > 1.9
    const labelled: { n: GNode; strong: boolean }[] = []
    for (const n of this.nodes) {
      const sx = n.x! * k + x, sy = n.y! * k + y
      if (sx < -80 || sx > this.w + 80 || sy < -40 || sy > this.h + 40) continue
      const big = n.r * k >= 11
      const near = focus && (n.id === focus || nb?.has(n.id))
      const strong = n.id === this.hoverId || n.id === this.selectedId
      if (strong || near || big || showAll || (k > 1.1 && n.r * k >= 8)) labelled.push({ n, strong: !!strong })
    }
    // Biggest first, skipping labels that would overlap one already placed.
    labelled.sort((a, b) => Number(b.strong) - Number(a.strong) || b.n.r - a.n.r)
    const placed: [number, number, number, number][] = []
    for (const { n, strong } of labelled) {
      const size = strong ? 13 : 11.5
      ctx.font = `${strong ? 650 : 560} ${size}px ${P.font}`
      let text = n.name
      if (text.length > 28 && !strong) text = text.slice(0, 27) + '…'
      const tw = ctx.measureText(text).width
      const sx = n.x! * k + x
      const sy = n.y! * k + y + n.r * k * (1 + n.h * 0.14) + 4
      const box: [number, number, number, number] = [sx - tw / 2 - 2, sy - 1, sx + tw / 2 + 2, sy + size + 2]
      if (!strong && placed.some((p) => box[0] < p[2] && box[2] > p[0] && box[1] < p[3] && box[3] > p[1])) continue
      placed.push(box)
      ctx.globalAlpha = Math.max(0.12, n.a)
      ctx.lineWidth = 4
      ctx.strokeStyle = P.bg
      ctx.strokeText(text, sx, sy)
      ctx.fillStyle = strong ? P.text : P.text2
      ctx.fillText(text, sx, sy)
      if (n.id === this.hoverId) {
        ctx.font = `500 10.5px ${P.font}`
        ctx.fillStyle = P.text3
        ctx.lineWidth = 3
        const sub = `${n.doc_count} document${n.doc_count === 1 ? '' : 's'}`
        ctx.strokeText(sub, sx, sy + size + 1)
        ctx.fillText(sub, sx, sy + size + 1)
      }
    }
    ctx.globalAlpha = 1

    // Relationship labels: for the focused node's edges, the best-documented relationship and how
    // many more there are; for the edge under the pointer or selected, every one, a line each.
    const incident = focus ? this.inc.get(focus) ?? [] : []
    const roleEdges = new Map<GEdge, boolean>()
    if (incident.length && incident.length <= 7) for (const e of incident) roleEdges.set(e, false)
    for (const e of this.edges) if (e === this.hoverEdge || e.raw === this.selectedEdge) roleEdges.set(e, true)
    if (roleEdges.size) {
      ctx.font = `600 10.5px ${P.font}`
      ctx.textBaseline = 'middle'
      for (const [e, full] of roleEdges) {
        const lines = full ? e.labels : [e.labels.length > 1 ? `${e.labels[0]}  +${e.labels.length - 1}` : e.labels[0]]
        const mid = this.mid(e)
        const sx = mid.x * k + x, sy = mid.y * k + y
        const lh = 15
        const tw = Math.max(...lines.map((l) => ctx.measureText(l).width))
        const w = tw + 14, h = lines.length * lh + 3
        ctx.fillStyle = P.surface
        ctx.strokeStyle = P.border
        ctx.lineWidth = 1
        ctx.beginPath()
        ctx.roundRect(sx - w / 2, sy - h / 2, w, h, 9)
        ctx.fill()
        ctx.stroke()
        ctx.fillStyle = P.text
        lines.forEach((l, i) => ctx.fillText(l, sx, sy - h / 2 + 1.5 + lh * (i + 0.5) + 0.5))
      }
    }
  }

  private mid(e: GEdge) {
    const s = e.source, t = e.target
    const mx = ((s.x ?? 0) + (t.x ?? 0)) / 2, my = ((s.y ?? 0) + (t.y ?? 0)) / 2
    const dx = (t.x ?? 0) - (s.x ?? 0), dy = (t.y ?? 0) - (s.y ?? 0)
    const cx = mx - dy * e.curve, cy = my + dx * e.curve
    return { x: 0.25 * (s.x ?? 0) + 0.5 * cx + 0.25 * (t.x ?? 0), y: 0.25 * (s.y ?? 0) + 0.5 * cy + 0.25 * (t.y ?? 0), cx, cy }
  }

  private path(e: GEdge) {
    const { ctx } = this
    const s = e.source, t = e.target
    const m = this.mid(e)
    ctx.beginPath()
    ctx.moveTo(s.x ?? 0, s.y ?? 0)
    ctx.quadraticCurveTo(m.cx, m.cy, t.x ?? 0, t.y ?? 0)
  }

  private arrow(e: GEdge, colour: string) {
    const { ctx } = this
    const t = e.target
    const m = this.mid(e)
    const ang = Math.atan2((t.y ?? 0) - m.cy, (t.x ?? 0) - m.cx)
    const tx = (t.x ?? 0) - Math.cos(ang) * (t.r + 3)
    const ty = (t.y ?? 0) - Math.sin(ang) * (t.r + 3)
    const sz = 6 / Math.sqrt(this.cam.k)
    ctx.beginPath()
    ctx.moveTo(tx, ty)
    ctx.lineTo(tx - Math.cos(ang - 0.45) * sz, ty - Math.sin(ang - 0.45) * sz)
    ctx.lineTo(tx - Math.cos(ang + 0.45) * sz, ty - Math.sin(ang + 0.45) * sz)
    ctx.closePath()
    ctx.fillStyle = colour
    ctx.fill()
  }
}
