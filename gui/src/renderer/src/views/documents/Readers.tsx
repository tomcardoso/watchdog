// The image viewer and the extracted-text reader, each with the same find box as the PDF viewer
// (D289). A scanned image with saved OCR positions gets an invisible text layer, so a match is
// highlighted where it is on the image; without them, find searches the image's extracted text
// and says so.

import { ExternalLink, Minus, Plus } from 'lucide-react'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Button, Callout } from '@renderer/components/ui'
import { countMatches, matchSegments } from '@renderer/lib/findText'
import { plural } from '@renderer/lib/format'
import type { DocumentDetail, PagePositions } from '@shared/api'
import { FindBox, FindNote, Marked, SearchTextInstead, useFindQuery, useRevealCurrent, useTextMatches } from './FindBox'
import { OcrLayer } from './OcrLayer'
import type { JumpTarget } from './PdfViewer'
import type { PositionsSource } from './positions'

interface ImageProps {
  src: string
  positions?: PositionsSource | null
  /** The text extracted from the image (its one page). */
  text?: string
  onSearchText?: (q: string) => void
}

export function ImageViewer({ src, positions, text, onSearchText }: ImageProps) {
  const [zoom, setZoom] = useState<number | null>(null) // null = fit
  const [nat, setNat] = useState<number | null>(null)
  const [shown, setShown] = useState({ w: 0, h: 0 })
  const [pos, setPos] = useState<PagePositions | null>(null)
  const box = useRef<HTMLDivElement>(null)
  const img = useRef<HTMLImageElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const find = useFindQuery()
  const q = find.active ? find.debounced : ''

  useEffect(() => {
    let dead = false
    setPos(null)
    if (positions?.has(1)) void positions.get(1).then((p) => !dead && setPos(p))
    return () => {
      dead = true
    }
  }, [positions])

  useLayoutEffect(() => {
    const el = img.current
    if (!el) return
    const ro = new ResizeObserver(() => setShown({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const total = useMemo(() => {
    if (!q) return 0
    if (pos) return matchSegments(pos.lines.map((l) => l[4]), q).count
    return text ? countMatches(text, q) : 0
  }, [q, pos, text])
  const placed = !!pos
  const step = (d: number) => total && find.setCurrent((c) => (c + d + total) % total)
  useRevealCurrent(box, find.current, placed ? total : 0, [shown.w, pos])

  return (
    <div className="pdf-viewer">
      <div className="pdf-toolbar">
        <FindBox inputRef={input} query={find.query} onQuery={find.setQuery} active={!!q} total={total} current={find.current} onStep={step} />
        <span className="spacer" />
        <Button variant="ghost" size="sm" icon={Minus} tip="Zoom out" onClick={() => setZoom(Math.max(0.1, (zoom ?? 1) / 1.25))} />
        <span className="pdf-zoomlabel">{zoom ? Math.round(zoom * 100) + '%' : 'Fit'}</span>
        <Button variant="ghost" size="sm" icon={Plus} tip="Zoom in" onClick={() => setZoom(Math.min(8, (zoom ?? 1) * 1.25))} />
        <Button variant={zoom === null ? 'soft' : 'ghost'} size="sm" onClick={() => setZoom(null)}>Fit</Button>
        <Button variant="ghost" size="sm" onClick={() => setZoom(1)}>100%</Button>
      </div>
      {q && !total && <FindNote action={<SearchTextInstead query={q} onSearchText={onSearchText} />}>No matches in this image’s text.</FindNote>}
      {q && total > 0 && !placed && (
        <FindNote action={onSearchText && <Button size="sm" variant="soft" onClick={() => onSearchText(q)}>Show in the Text tab</Button>}>
          This image has no saved text positions, so matches can’t be highlighted on it. Watchdog found {total === 1 ? 'this match' : 'them'} in the text extracted from it.
        </FindNote>
      )}
      <div className="img-scroll" ref={box}>
        <div className="img-stage" style={zoom === null ? { maxWidth: '100%' } : undefined}>
          <img
            ref={img}
            src={src}
            alt="Original document"
            onLoad={(e) => {
              const el = e.target as HTMLImageElement
              setNat(el.naturalWidth)
              setShown({ w: el.clientWidth, h: el.clientHeight })
            }}
            style={zoom === null || !nat ? { maxWidth: '100%', height: 'auto' } : { width: nat * zoom }}
          />
          {pos && shown.w > 0 && <OcrLayer pos={pos} width={shown.w} height={shown.h} query={q} hitOffset={0} current={find.current} />}
        </div>
      </div>
    </div>
  )
}

interface TextReaderProps {
  d: DocumentDetail
  target: JumpTarget | null
  note?: string
  onOpen: () => void
  onSearchText?: (q: string) => void
}

export function TextReader({ d, target, note, onOpen, onSearchText }: TextReaderProps) {
  const ref = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const [flash, setFlash] = useState<number | null>(null)
  const find = useFindQuery()
  const q = find.active ? find.debounced : ''
  const texts = useMemo(() => d.pages.map((p) => p.text), [d.pages])
  const { ranges, offsets, total } = useTextMatches(texts, q)
  const step = (delta: number) => total && find.setCurrent((c) => (c + delta + total) % total)
  useRevealCurrent(ref, find.current, total, [q])

  useEffect(() => {
    if (!target) return
    const el = ref.current?.querySelector(`[data-p="${target.page}"]`)
    el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    setFlash(target.page)
    if (target.find) find.setQuery(target.find)
    const t = setTimeout(() => setFlash(null), 1700)
    return () => clearTimeout(t)
  }, [target]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="pdf-viewer">
      <div className="pdf-toolbar">
        <FindBox inputRef={input} query={find.query} onQuery={find.setQuery} active={!!q} total={total} current={find.current} onStep={step} disabled={!d.pages.length} />
        <span className="spacer" />
        <span className="pdf-pagelabel">Extracted text · {plural(d.pages.length, 'page')}</span>
        {d.original && <Button size="sm" icon={ExternalLink} onClick={onOpen}>Open original</Button>}
      </div>
      {q && !total && <FindNote action={<SearchTextInstead query={q} onSearchText={onSearchText} />}>No matches in the extracted text.</FindNote>}
      <div className="text-reader" ref={ref}>
        <div className="text-reader-note">
          <Callout tone="info">
            {note ?? `${d.ext.toUpperCase()} files can't be shown page by page here. This is the text Watchdog extracted from the original, which is what the facts were drawn from.`}
          </Callout>
        </div>
        {d.pages.length === 0 && <div className="text-reader-note faint">No extracted text is available for this document.</div>}
        {d.pages.map((p, i) => (
          <div key={p.page} data-p={p.page} className={'text-sheet' + (flash === p.page ? ' flash' : '')}>
            <span className="text-sheet-num">p. {p.page}</span>
            <Marked text={p.text} ranges={ranges[i]} offset={offsets[i]} current={find.current} />
          </div>
        ))}
      </div>
    </div>
  )
}
