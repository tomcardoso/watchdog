// An invisible, selectable text layer over a scanned page, built from the OCR'd lines saved at
// pre-processing (D289). Each line's text is set in a plain sans-serif face and stretched to the
// width of the line's box, so selecting, copying and finding work as on a page with its own text
// layer. Docling reports a box per line, not per word: a match's highlight sits where its
// characters fall when the line is set that way, an estimate that is close for ordinary type and
// can drift by a few characters on unusual spacing.

import { memo, ReactNode, useMemo } from 'react'
import { matchSegments } from '@renderer/lib/findText'
import type { PagePositions } from '@shared/api'

const FONT = 'sans-serif'
let ctx: CanvasRenderingContext2D | null = null
const widths = new Map<string, number>()

/** The width of `text` at 100px in the layer's face, measured once and cached. */
function width100(text: string): number {
  let w = widths.get(text)
  if (w !== undefined) return w
  if (!ctx) {
    ctx = document.createElement('canvas').getContext('2d')
    if (ctx) ctx.font = `100px ${FONT}`
  }
  w = ctx ? ctx.measureText(text).width : text.length * 55
  if (widths.size > 20_000) widths.clear()
  widths.set(text, w)
  return w
}

interface Props {
  pos: PagePositions
  /** The size the page is drawn at, in CSS pixels. */
  width: number
  height: number
  /** The (debounced) query; empty for none. */
  query: string
  /** Number of the page's first match within the whole document. */
  hitOffset: number
  current: number
}

export const OcrLayer = memo(function OcrLayer({ pos, width, height, query, hitOffset, current }: Props) {
  const fx = width / pos.width
  const fy = height / pos.height
  const texts = useMemo(() => pos.lines.map((l) => l[4]), [pos])
  const hits = useMemo(() => (query ? matchSegments(texts, query).bySegment : null), [texts, query])
  return (
    <div className="ocr-layer" aria-label="Text recognised on this page">
      {pos.lines.map(([l, t, r, b, text], i) => {
        const h = Math.max(1, (b - t) * fy)
        const w = Math.max(1, (r - l) * fx)
        const size = h * 0.86
        const natural = (width100(text) * size) / 100
        const sx = natural > 0 ? w / natural : 1
        const parts = hits?.get(i)
        let body: ReactNode = text
        if (parts) {
          const out: ReactNode[] = []
          let at = 0
          parts.forEach((p, j) => {
            if (p.from > at) out.push(text.slice(at, p.from))
            const n = hitOffset + p.k
            out.push(
              <mark key={j} className={'find-hit' + (n === current ? ' current' : '')} data-hit={n}>
                {text.slice(p.from, p.to)}
              </mark>
            )
            at = p.to
          })
          if (at < text.length) out.push(text.slice(at))
          body = out
        }
        return (
          <span key={i}>
            <span className="ocr-line" style={{ left: l * fx, top: t * fy, height: h, lineHeight: `${h}px`, fontSize: size, transform: `scaleX(${sx})` }}>
              {body}
            </span>
            <br />
          </span>
        )
      })}
    </div>
  )
})
