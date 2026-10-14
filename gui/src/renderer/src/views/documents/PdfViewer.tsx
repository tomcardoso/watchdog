// A continuous-scroll PDF reader on pdf.js. Pages are paper sheets of known size from the start,
// so the scrollbar and jump-to-page are exact; only pages near the viewport hold a canvas and a
// text layer, canvases come from a small pool, and in-flight renders are cancelled the moment a
// page scrolls away. Text layers make the text selectable and searchable.
//
// A scanned page has no text layer of its own. Where pre-processing saved the positions of its
// OCR'd lines (D289), an invisible layer built from them takes the place of pdf.js's, so the
// page can be searched, highlighted, selected and copied like any other. Where it didn't (a
// document pre-processed before D289), find searches the text extracted from that page instead
// and can say only which page a match is on.

import { Maximize2, ZoomIn, ZoomOut } from 'lucide-react'
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Button, Callout, Spinner } from '@renderer/components/ui'
import { countMatches, foldQuery, matchSegments } from '@renderer/lib/findText'
import { cssVar, openPdf, pdfjs } from '@renderer/lib/pdf'
import type { PagePositions } from '@shared/api'
import type { PDFDocumentProxy, PDFPageProxy } from 'pdfjs-dist'
import { FindBox, FindNote, SearchTextInstead } from './FindBox'
import { OcrLayer } from './OcrLayer'
import type { PositionsSource } from './positions'

export interface JumpTarget {
  page: number
  nonce: number
  find?: string
}

type TextContent = Awaited<ReturnType<PDFPageProxy['getTextContent']>>

const GAP = 16
const PAD_TOP = 18
const OVERSCAN = 2
const MAX_PIXELS = 16_000_000

// ── canvas pool ──────────────────────────────────────────────────────────────
const pool: HTMLCanvasElement[] = []
function acquire(): HTMLCanvasElement {
  return pool.pop() ?? document.createElement('canvas')
}
function release(c: HTMLCanvasElement | null | undefined): void {
  if (!c) return
  c.remove()
  c.width = 0
  c.height = 0
  if (pool.length < 14) pool.push(c)
}

// ── what each page can be searched by ────────────────────────────────────────
/** A page's searchable text: its own text layer, its saved OCR lines, or (with neither) the text
 * extracted from it, where a match can be counted and its page shown but not highlighted. */
type PageInfo =
  | { kind: 'pdf'; strs: string[] }
  | { kind: 'ocr'; pos: PagePositions }
  | { kind: 'extracted'; text: string }
  | { kind: 'empty' }
type PageKind = PageInfo['kind']

function itemStrs(tc: TextContent): string[] {
  return tc.items.filter((it) => 'str' in it).map((it) => ('str' in it ? it.str : ''))
}

function countOn(info: PageInfo, q: string): number {
  if (info.kind === 'pdf') return matchSegments(info.strs, q).count
  if (info.kind === 'ocr') return matchSegments(info.pos.lines.map((l) => l[4]), q).count
  if (info.kind === 'extracted') return countMatches(info.text, q)
  return 0
}

// ── a single page ────────────────────────────────────────────────────────────
interface SlotProps {
  pdf: PDFDocumentProxy
  num: number
  w: number
  h: number
  scale: number
  active: boolean
  flash: number
  query: string
  hitOffset: number
  current: number
  getText: (n: number) => Promise<TextContent>
  getInfo: (n: number) => Promise<PageInfo>
  onLayer: () => void
}

const PageSlot = memo(function PageSlot({ pdf, num, w, h, scale, active, flash, query, hitOffset, current, getText, getInfo, onLayer }: SlotProps) {
  const host = useRef<HTMLDivElement>(null)
  const layer = useRef<HTMLDivElement>(null)
  const [layerKey, setLayerKey] = useState(0)
  // Saved OCR lines replace pdf.js's text layer on a scanned page (D289).
  const [ocr, setOcr] = useState<PagePositions | null>(null)
  useEffect(() => {
    if (!active) return
    let dead = false
    void getInfo(num).then((info) => {
      if (!dead) setOcr(info.kind === 'ocr' ? info.pos : null)
    })
    return () => {
      dead = true
    }
  }, [active, num, getInfo])
  useEffect(() => {
    if (ocr && active) onLayer()
  }, [ocr, active, query, onLayer])

  // Canvas: render into a pooled canvas, then swap it in so a zoom never flashes blank.
  useEffect(() => {
    const el = host.current
    if (!el) return
    if (!active) {
      el.querySelectorAll('canvas').forEach((c) => release(c))
      return
    }
    let cancelled = false
    let task: { cancel: () => void; promise: Promise<unknown> } | null = null
    const next = acquire()
    ;(async () => {
      try {
        const page = await pdf.getPage(num)
        if (cancelled) return
        const base = page.getViewport({ scale })
        let out = Math.min(window.devicePixelRatio || 1, 2)
        if (base.width * base.height * out * out > MAX_PIXELS) out = Math.sqrt(MAX_PIXELS / (base.width * base.height))
        const vp = page.getViewport({ scale: scale * out })
        next.width = Math.floor(vp.width)
        next.height = Math.floor(vp.height)
        const ctx = next.getContext('2d')
        if (!ctx) return
        ctx.fillStyle = cssVar('--pdf-page')
        ctx.fillRect(0, 0, next.width, next.height)
        task = page.render({ canvasContext: ctx, viewport: vp, canvas: next })
        await task.promise
        if (cancelled) return
        el.querySelectorAll('canvas').forEach((c) => release(c))
        el.appendChild(next)
      } catch {
        /* cancelled renders reject; real failures leave the sheet blank */
      }
    })()
    return () => {
      cancelled = true
      task?.cancel()
      if (!next.isConnected) release(next)
    }
  }, [pdf, num, active, scale])

  useEffect(() => () => host.current?.querySelectorAll('canvas').forEach((c) => release(c)), [])

  // Text layer.
  useEffect(() => {
    const el = layer.current
    if (!el) return
    if (!active || ocr) {
      el.replaceChildren()
      return
    }
    let cancelled = false
    let tl: InstanceType<typeof pdfjs.TextLayer> | null = null
    ;(async () => {
      try {
        const [page, content] = await Promise.all([pdf.getPage(num), getText(num)])
        if (cancelled) return
        const fresh = document.createElement('div')
        fresh.className = 'textLayer'
        fresh.style.setProperty('--total-scale-factor', String(scale))
        el.replaceChildren(fresh)
        tl = new pdfjs.TextLayer({ textContentSource: content, container: fresh, viewport: page.getViewport({ scale }) })
        await tl.render()
        if (cancelled) return
        ;(fresh as HTMLElement & { _tl?: unknown })._tl = tl
        setLayerKey((k) => k + 1)
        onLayer()
      } catch {
        /* cancelled */
      }
    })()
    return () => {
      cancelled = true
      tl?.cancel()
    }
  }, [pdf, num, active, scale, getText, onLayer, ocr])

  // Highlights.
  useEffect(() => {
    const root = layer.current?.querySelector('.textLayer') as (HTMLElement & { _tl?: InstanceType<typeof pdfjs.TextLayer> }) | null
    const tl = root?._tl
    if (!root || !tl) return
    const divs = tl.textDivs
    const strs = tl.textContentItemsStr
    root.querySelectorAll('mark.find-hit').forEach((m) => {
      const p = m.parentElement
      if (p) p.textContent = p.dataset.str ?? p.textContent
    })
    if (!query) return
    const { bySegment } = matchSegments(strs, query)
    bySegment.forEach((hits, i) => {
      const div = divs[i]
      const s = strs[i]
      if (!div || !s) return
      div.dataset.str = s
      div.textContent = ''
      let pos = 0
      for (const hit of hits) {
        if (hit.from > pos) div.append(s.slice(pos, hit.from))
        const m = document.createElement('mark')
        m.className = 'find-hit'
        m.dataset.hit = String(hitOffset + hit.k)
        m.textContent = s.slice(hit.from, hit.to)
        div.append(m)
        pos = hit.to
      }
      if (pos < s.length) div.append(s.slice(pos))
    })
    onLayer()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, layerKey, hitOffset])

  useEffect(() => {
    layer.current?.querySelectorAll('mark.find-hit').forEach((m) => m.classList.toggle('current', Number((m as HTMLElement).dataset.hit) === current))
  }, [current, query, layerKey])

  return (
    <div className={'pdf-slot' + (flash ? ' flash' : '')} data-page={num} style={{ width: w, height: h }} key={flash}>
      {!active && <div className="pdf-slot-num">{num}</div>}
      <div className="pdf-canvas-host" ref={host} />
      <div ref={layer} style={{ position: 'absolute', inset: 0 }} />
      {active && ocr && <OcrLayer pos={ocr} width={w} height={h} query={query} hitOffset={hitOffset} current={current} />}
    </div>
  )
})

// ── the viewer ───────────────────────────────────────────────────────────────
interface ViewerProps {
  path: string
  target: JumpTarget | null
  onFailed?: (message: string) => void
  /** Saved OCR positions for scanned pages (D289), if any. */
  positions?: PositionsSource | null
  /** The text extracted from each page, searched on a page with neither a text layer nor
   * positions. */
  extracted?: { page: number; text: string }[]
  /** Open the Text tab searching for these words. */
  onSearchText?: (q: string) => void
}

export function PdfViewer({ path, target, onFailed, positions, extracted, onSearchText }: ViewerProps) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sizes, setSizes] = useState<{ w: number; h: number }[]>([])
  const [cw, setCw] = useState(0)
  const [zoom, setZoom] = useState<'fit' | number>('fit')
  const [range, setRange] = useState<[number, number]>([1, 3])
  const [current, setCurrent] = useState(1)
  const [pageText, setPageText] = useState('1')
  const [flash, setFlash] = useState<{ page: number; n: number } | null>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const root = useRef<HTMLDivElement>(null)
  const anchor = useRef({ page: 1, frac: 0 })
  const pendingJump = useRef<{ page: number; until: number } | null>(null)
  const textCache = useRef(new Map<number, Promise<TextContent>>())

  // find
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  const [hitCounts, setHitCounts] = useState<number[]>([])
  const [kinds, setKinds] = useState<(PageKind | undefined)[]>([])
  const [scanning, setScanning] = useState(false)
  const [cur, setCur] = useState(0)
  const [revealTick, setRevealTick] = useState(0)
  const findInput = useRef<HTMLInputElement>(null)
  const infoCache = useRef(new Map<number, Promise<PageInfo>>())

  // Load the document and every page's size (page 1 first, the rest in the background).
  useEffect(() => {
    let dead = false
    setPdf(null)
    setError(null)
    setSizes([])
    textCache.current = new Map()
    infoCache.current = new Map()
    ;(async () => {
      try {
        const doc = await openPdf(path)
        if (dead) return
        const first = await doc.getPage(1)
        const v = first.getViewport({ scale: 1 })
        const all = Array.from({ length: doc.numPages }, () => ({ w: v.width, h: v.height }))
        setSizes(all)
        setPdf(doc)
        let next = all
        for (let i = 2; i <= doc.numPages; i += 30) {
          const batch = await Promise.all(Array.from({ length: Math.min(30, doc.numPages - i + 1) }, (_, k) => doc.getPage(i + k).then((p) => p.getViewport({ scale: 1 }))))
          if (dead) return
          let changed = false
          next = next.slice()
          batch.forEach((vp, k) => {
            const cur = next[i - 1 + k]
            if (Math.abs(cur.w - vp.width) > 0.5 || Math.abs(cur.h - vp.height) > 0.5) {
              next[i - 1 + k] = { w: vp.width, h: vp.height }
              changed = true
            }
          })
          if (changed) setSizes(next)
          await new Promise((r) => setTimeout(r, 0))
        }
      } catch (e) {
        if (dead) return
        const msg = e instanceof Error ? e.message : String(e)
        setError(msg)
        onFailed?.(msg)
      }
    })()
    return () => {
      dead = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path])

  useLayoutEffect(() => {
    const el = scroller.current
    if (!el) return
    const ro = new ResizeObserver(() => setCw(el.clientWidth))
    ro.observe(el)
    setCw(el.clientWidth)
    return () => ro.disconnect()
  }, [pdf])

  const n = sizes.length
  const fitScale = sizes.length && cw ? Math.min(4, Math.max(0.25, (cw - 56) / sizes[0].w)) : 1
  const scale = zoom === 'fit' ? fitScale : zoom
  const offs = useMemo(() => {
    const o = new Array<number>(n)
    let y = PAD_TOP
    for (let i = 0; i < n; i++) {
      o[i] = y
      y += sizes[i].h * scale + GAP
    }
    return o
  }, [sizes, scale, n])

  const compute = useCallback(() => {
    const el = scroller.current
    if (!el || !n) return
    const top = el.scrollTop
    const bottom = top + el.clientHeight
    const find = (y: number) => {
      let lo = 0
      let hi = n - 1
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1
        if (offs[mid] <= y) lo = mid
        else hi = mid - 1
      }
      return lo
    }
    const first = find(top)
    const last = find(bottom)
    const probe = find(top + el.clientHeight * 0.3)
    const a = Math.max(1, first + 1 - OVERSCAN)
    const b = Math.min(n, last + 1 + OVERSCAN)
    setRange((r) => (r[0] === a && r[1] === b ? r : [a, b]))
    setCurrent(probe + 1)
    anchor.current = { page: probe + 1, frac: Math.max(0, (top - offs[probe]) / (sizes[probe].h * scale)) }
  }, [offs, n, sizes, scale])

  useEffect(() => {
    const el = scroller.current
    if (!el) return
    let raf = 0
    const on = () => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(compute)
    }
    el.addEventListener('scroll', on, { passive: true })
    compute()
    return () => {
      el.removeEventListener('scroll', on)
      cancelAnimationFrame(raf)
    }
  }, [compute, pdf])

  useEffect(() => setPageText(String(current)), [current])

  // Keep the reading position when the zoom changes.
  const lastScale = useRef(scale)
  useLayoutEffect(() => {
    if (lastScale.current === scale || !scroller.current || !n) {
      lastScale.current = scale
      return
    }
    lastScale.current = scale
    const pj = pendingJump.current
    const { page, frac } = pj && pj.until > Date.now() ? { page: pj.page, frac: 0 } : anchor.current
    scroller.current.scrollTop = offs[page - 1] + frac * sizes[page - 1].h * scale
  }, [scale, offs, sizes, n])

  const goTo = useCallback(
    (page: number, smooth = true) => {
      const el = scroller.current
      if (!el || !n) return
      const p = Math.min(n, Math.max(1, Math.round(page)))
      const top = Math.max(0, offs[p - 1] - 10)
      el.scrollTo({ top, behavior: smooth && Math.abs(p - anchor.current.page) <= 6 ? 'smooth' : 'auto' })
    },
    [offs, n]
  )

  // Jump requests from outside (fact citations, ?page=).
  const handled = useRef(0)
  useEffect(() => {
    if (!target || !pdf || !cw || target.nonce === handled.current) return
    handled.current = target.nonce
    pendingJump.current = { page: Math.min(n, Math.max(1, target.page)), until: Date.now() + 1200 }
    goTo(target.page, true)
    setFlash({ page: Math.min(n, Math.max(1, target.page)), n: target.nonce })
    const t = setTimeout(() => setFlash(null), 1700)
    if (target.find) setQuery(target.find)
    return () => clearTimeout(t)
  }, [target, pdf, cw, goTo, n])

  // Zoom.
  const zoomBy = useCallback((f: number) => setZoom(Math.min(5, Math.max(0.25, +(scale * f).toFixed(3)))), [scale])
  useEffect(() => {
    const el = scroller.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return
      e.preventDefault()
      zoomBy(e.deltaY < 0 ? 1.08 : 1 / 1.08)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [zoomBy, pdf])

  // Find in document.
  useEffect(() => {
    const t = setTimeout(() => setDebounced(query), 220)
    return () => clearTimeout(t)
  }, [query])

  const getText = useCallback(
    (num: number) => {
      let p = textCache.current.get(num)
      if (!p && pdf) {
        p = pdf.getPage(num).then((pg) => pg.getTextContent())
        textCache.current.set(num, p)
      }
      return p ?? Promise.reject(new Error('no document'))
    },
    [pdf]
  )

  const extractedByPage = useMemo(() => new Map((extracted ?? []).map((p) => [p.page, p.text])), [extracted])
  useEffect(() => {
    infoCache.current = new Map()
  }, [positions, extractedByPage])

  const getInfo = useCallback(
    (num: number): Promise<PageInfo> => {
      let p = infoCache.current.get(num)
      if (p) return p
      const fromLayer = (): Promise<PageInfo> =>
        getText(num).then(
          (tc) => {
            const strs = itemStrs(tc)
            if (strs.some((x) => x.trim())) return { kind: 'pdf', strs }
            const text = extractedByPage.get(num)
            return text && text.trim() ? { kind: 'extracted', text } : { kind: 'empty' }
          },
          () => ({ kind: 'empty' })
        )
      p = positions?.has(num) ? positions.get(num).then((pos) => (pos ? { kind: 'ocr', pos } : fromLayer())) : fromLayer()
      infoCache.current.set(num, p)
      return p
    },
    [getText, positions, extractedByPage]
  )

  const q = foldQuery(debounced) ? debounced : ''
  useEffect(() => {
    setCur(0)
    if (!q || !pdf) {
      setHitCounts([])
      setScanning(false)
      return
    }
    let dead = false
    setScanning(true)
    ;(async () => {
      const counts = new Array<number>(pdf.numPages).fill(0)
      const found = new Array<PageKind | undefined>(pdf.numPages)
      for (let i = 1; i <= pdf.numPages; i++) {
        const info = await getInfo(i)
        if (dead) return
        counts[i - 1] = countOn(info, q)
        found[i - 1] = info.kind
        if (i % 25 === 0 || i === pdf.numPages) {
          setHitCounts(counts.slice())
          setKinds(found.slice())
        }
      }
      if (!dead) setScanning(false)
    })()
    return () => {
      dead = true
    }
  }, [q, pdf, getInfo])

  const hitOffsets = useMemo(() => {
    const o = new Array<number>(hitCounts.length)
    let s = 0
    hitCounts.forEach((c, i) => {
      o[i] = s
      s += c
    })
    return o
  }, [hitCounts])
  const total = hitCounts.reduce((a, b) => a + b, 0)

  // The page the current match is on, and whether it can be highlighted there.
  const curPage = total && cur < total ? hitOffsets.findIndex((o, i) => cur >= o && cur < o + hitCounts[i]) + 1 : 0
  const curUnplaced = curPage > 0 && kinds[curPage - 1] === 'extracted'

  // Scroll the current match into view (and nudge to its page first if it isn't rendered yet). A
  // match found only in the extracted text has no mark: its page is shown instead.
  const lastCur = useRef(-1)
  useEffect(() => {
    if (!total || cur >= total || !curPage) return
    if (curUnplaced) {
      if (lastCur.current !== cur) {
        goTo(curPage, false)
        lastCur.current = cur
      }
      return
    }
    const mark = scroller.current?.querySelector(`mark.find-hit[data-hit="${cur}"]`)
    if (mark) {
      if (lastCur.current !== cur) {
        mark.scrollIntoView({ block: 'center', inline: 'nearest' })
        lastCur.current = cur
      }
      return
    }
    lastCur.current = -1
    goTo(curPage, false)
  }, [cur, total, revealTick, curPage, curUnplaced, goTo])

  const onLayer = useCallback(() => setRevealTick((t) => (t + 1) % 1_000_000), [])
  const step = (d: number) => total && setCur((c) => (c + d + total) % total)


  const commitPage = () => {
    const v = parseInt(pageText, 10)
    if (Number.isFinite(v)) goTo(v, true)
    else setPageText(String(current))
  }

  if (error)
    return (
      <div className="pdf-viewer">
        <div className="pdf-center">
          <Callout tone="danger" title="This PDF could not be displayed">
            {error}
          </Callout>
        </div>
      </div>
    )

  return (
    <div className="pdf-viewer" ref={root} tabIndex={-1}>
      <div className="pdf-toolbar">
        <input
          className="input pdf-pageinput"
          value={pageText}
          aria-label="Page number"
          onChange={(e) => setPageText(e.target.value.replace(/[^\d]/g, ''))}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              commitPage()
              ;(e.target as HTMLInputElement).blur()
            }
          }}
          onBlur={commitPage}
          onFocus={(e) => e.target.select()}
          disabled={!n}
          style={{ userSelect: 'text' }}
        />
        <span className="pdf-pagelabel tnum">of {n || '…'}</span>
        <span className="sep" />
        <FindBox inputRef={findInput} query={query} onQuery={setQuery} active={!!q} total={total} current={cur} scanning={scanning} onStep={step} disabled={!n} />
        <span className="spacer" />
        <Button variant="ghost" size="sm" icon={ZoomOut} tip="Zoom out" onClick={() => zoomBy(1 / 1.2)} disabled={!n} />
        <span className="pdf-zoomlabel">{Math.round(scale * 100)}%</span>
        <Button variant="ghost" size="sm" icon={ZoomIn} tip="Zoom in" onClick={() => zoomBy(1.2)} disabled={!n} />
        <Button variant={zoom === 'fit' ? 'soft' : 'ghost'} size="sm" icon={Maximize2} tip="Fit to width" onClick={() => setZoom('fit')} disabled={!n} />
      </div>
      {q && !scanning && !total && (
        <FindNote action={<SearchTextInstead query={q} onSearchText={onSearchText} />}>No matches on the pages of this document.</FindNote>
      )}
      {curUnplaced && (
        <FindNote action={onSearchText && <Button size="sm" variant="soft" onClick={() => onSearchText(q)}>Show in the Text tab</Button>}>
          Page {curPage} has no text layer, so this match can’t be highlighted on the page. Watchdog found it in the text extracted from that page.
        </FindNote>
      )}
      <div className="pdf-scroll" ref={scroller}>
        {!pdf ? (
          <div className="pdf-center">
            <Spinner size="lg" />
            <span>Opening the document…</span>
          </div>
        ) : (
          <div className="pdf-pages">
            {sizes.map((s, i) => {
              const num = i + 1
              return (
                <PageSlot
                  key={num}
                  pdf={pdf}
                  num={num}
                  w={Math.floor(s.w * scale)}
                  h={Math.floor(s.h * scale)}
                  scale={scale}
                  active={num >= range[0] && num <= range[1]}
                  flash={flash?.page === num ? flash.n : 0}
                  query={q}
                  hitOffset={hitOffsets[i] ?? 0}
                  current={cur}
                  getText={getText}
                  getInfo={getInfo}
                  onLayer={onLayer}
                />
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
